"""Exercise actual process-tree cleanup on this OS, without launching a browser."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from src.processes import run_worker


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_and_large_stderr_drain(self):
        code = "import sys,json; p=json.loads(sys.stdin.readline()); sys.stderr.write('x'*1000000); print(json.dumps({'ok':True}))"
        result = await asyncio.wait_for(run_worker({}, command=[sys.executable, "-c", code]), 5)
        self.assertEqual(result, {"ok": True})

    async def test_oversized_stdout_terminates_worker(self):
        code = "import sys,time; sys.stdin.readline(); print('x'*2100000,flush=True); time.sleep(60)"
        with self.assertRaises(ValueError):
            await asyncio.wait_for(run_worker({}, command=[sys.executable, "-c", code]), 5)

    async def test_cancel_kills_grandchild_and_cleans_profile(self):
        # Child/grandchild signal readiness using generated fixture files.
        import psutil
        with tempfile.TemporaryDirectory(prefix="scrapling-test-") as fixture:
            marker = Path(fixture) / "pids"
            grandchild = "import time; time.sleep(60)"
            code = (
                "import sys,json,subprocess,time; from pathlib import Path; "
                "p=json.loads(sys.stdin.readline()); "
                f"child=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
                "Path(p['marker']).write_text(str(child.pid)+'\\n'+p['work_dir']); "
                "time.sleep(60)"
            )
            task = asyncio.create_task(run_worker({"marker": str(marker)}, command=[sys.executable, "-c", code]))
            try:
                async with asyncio.timeout(5):
                    while not marker.exists():
                        await asyncio.sleep(0.01)
                pid, work_dir = marker.read_text().splitlines()
                self.assertTrue(psutil.pid_exists(int(pid)))
                started = time.perf_counter()
                task.cancel()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                async with asyncio.timeout(3):
                    while psutil.pid_exists(int(pid)):
                        await asyncio.sleep(0.02)
                self.assertFalse(Path(work_dir).exists())
                self.assertLess(time.perf_counter() - started, 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
