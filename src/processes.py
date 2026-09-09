"""Run browser workers in process trees that can be killed on timeout/cancel."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


class WindowsJob:
    """Kernel ownership covers descendants even if their Python parent exits."""
    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                        ("Flags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", w.DWORD), ("Scheduling", w.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("Basic", BasicLimits), ("Io", IoCounters),
                        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                        ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.AssignProcessToJobObject.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not kernel.AssignProcessToJobObject(self.handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(process)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


async def _read_bounded(stream, limit: int):
    data = bytearray()
    while chunk := await stream.read(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("worker output limit exceeded")
    return bytes(data)


async def _discard(stream):
    # Third-party logs may contain URLs/cookies; drain without exposing them.
    while await stream.read(65536):
        pass


async def _run_worker(payload: dict, *, command=None) -> dict:
    """Worker must wait for stdin before spawning descendants (Job attach barrier)."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Windows venv python.exe is a launcher which may spawn the real interpreter
    # BEFORE Job assignment. Launch the actual binary and reuse the current paths.
    executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in sys.path if p)
    argv = list(command) if command else [executable, "-m", "src.worker"]
    if os.name == "nt" and command and os.path.normcase(argv[0]) == os.path.normcase(sys.executable):
        argv[0] = executable
    creation = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(Path(__file__).resolve().parent.parent),
        env=env, **options,
    ))
    try:
        process = await asyncio.shield(creation)
    except asyncio.CancelledError:
        # Spawn may finish after cancellation. Worker is still waiting for stdin.
        async def discard_spawn():
            child = await creation
            if child.returncode is None:
                child.kill()
            await child.communicate()
        await _complete_cleanup(discard_spawn())
        raise
    job = None
    readers = []
    try:
        if os.name == "nt":
            job = WindowsJob(process.pid)
        process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        await process.stdin.drain()
        process.stdin.close()
        readers = [asyncio.create_task(_read_bounded(process.stdout, 2_000_000)),
                   asyncio.create_task(_discard(process.stderr))]
        output, _ = await asyncio.gather(*readers)
        code = await process.wait()
        if code:
            raise RuntimeError(f"worker exited with code {code}")
        return json.loads(output)
    finally:
        async def cleanup():
            if job:
                job.close()
            elif os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            # Drain remaining bytes even after an output-limit error, otherwise
            # asyncio can wait forever for a full stdout pipe while reaping.
            await asyncio.gather(_discard(process.stdout), _discard(process.stderr))
            await process.wait()
        await _complete_cleanup(cleanup())


async def _complete_cleanup(coro):
    """Repeated cancellation cannot release capacity before descendants are reaped."""
    task = asyncio.create_task(coro)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def run_worker(payload: dict, *, command=None) -> dict:
    # Parent owns temporary files so timeout/kill cannot leave profiles and caches.
    with tempfile.TemporaryDirectory(prefix="scrapling-worker-") as work_dir:
        return await _run_worker({**payload, "work_dir": work_dir}, command=command)
