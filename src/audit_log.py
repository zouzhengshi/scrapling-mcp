"""Local runtime and MCP invocation logging without sensitive request data."""
from __future__ import annotations

from datetime import datetime
import json
import inspect
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import time
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlsplit


LOG_DIR_ENV = "SCRAPLING_LOG_DIR"
CALLER_NAME_ENV = "SCRAPLING_CALLER_NAME"
MAX_LOG_BYTES = 5_000_000
BACKUP_COUNT = 3

_runtime_logger = logging.getLogger("scrapling.runtime")
_call_logger = logging.getLogger("scrapling.calls")
_configured_dir: Path | None = None


def log_dir() -> Path:
    raw = os.environ.get(LOG_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return root / "ScraplingMCP" / "logs"
    return Path.home() / ".local" / "state" / "scrapling-mcp" / "logs"


def log_paths() -> dict[str, Path]:
    directory = log_dir()
    return {"directory": directory, "runtime": directory / "runtime.log",
            "calls": directory / "calls.jsonl"}


def _ensure_log_dir(directory: Path) -> Path:
    if directory.is_symlink():
        raise OSError("日志目录不能是符号链接")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError("日志目录不能是符号链接")
    directory = directory.resolve()
    if os.name != "nt":
        os.chmod(directory, 0o700)
    return directory


class _RuntimeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        return f"{timestamp} {record.levelname} {record.getMessage()}"


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def configure_logging() -> Path:
    """Configure rotating local files once and return the log directory."""
    global _configured_dir
    directory = _ensure_log_dir(log_dir())
    if _configured_dir == directory:
        return directory
    for logger in (_runtime_logger, _call_logger):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = False
        logger.setLevel(logging.INFO)
    runtime_handler = RotatingFileHandler(
        directory / "runtime.log", maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    runtime_handler.setFormatter(_RuntimeFormatter())
    _runtime_logger.addHandler(runtime_handler)
    call_handler = RotatingFileHandler(
        directory / "calls.jsonl", maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    call_handler.setFormatter(_JsonLineFormatter())
    _call_logger.addHandler(call_handler)
    _configured_dir = directory
    return directory


def close_logging() -> None:
    """Flush and close local handlers, primarily for graceful service exit/tests."""
    global _configured_dir
    for logger in (_runtime_logger, _call_logger):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    _configured_dir = None


def _safe(value: Any, limit: int = 240) -> str:
    text = str(value)
    text = "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in text)
    return text[:limit]


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _write_call(record: dict[str, Any]) -> None:
    if _configured_dir is None:
        return
    record = {key: value for key, value in record.items() if value is not None}
    try:
        _call_logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Logging must never turn a valid MCP request into a failed request.
        pass


def runtime_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    if _configured_dir is None:
        return
    safe_fields = {key: _safe(value) if isinstance(value, (str, Path)) else value
                   for key, value in fields.items()}
    try:
        _runtime_logger.log(level, "%s %s", event, json.dumps(safe_fields, ensure_ascii=False,
                                                               separators=(",", ":")))
    except Exception:
        pass


def _parent_process() -> dict[str, Any]:
    parent_pid = os.getppid()
    result: dict[str, Any] = {"pid": parent_pid}
    try:
        import psutil
        process = psutil.Process(parent_pid)
        # Do not record the parent command line.  MCP clients often put
        # connection settings, tokens, or other secrets in argv; process
        # name + PID are enough to identify the caller in the local log.
        result["name"] = _safe(process.name(), 120)
    except Exception:
        result["name"] = None
    return result


def caller_context() -> dict[str, Any]:
    parent = _parent_process()
    configured = os.environ.get(CALLER_NAME_ENV)
    caller = _safe(configured.strip(), 120) if configured and configured.strip() else parent.get("name")
    return {
        "name": caller or "unknown",
        "source": "SCRAPLING_CALLER_NAME" if configured and configured.strip() else "parent_process",
        "pid": parent["pid"],
        "parent_name": parent.get("name"),
    }


def _target_host(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return _safe(urlsplit(value).hostname or "", 253) or None
    except ValueError:
        return "<invalid>"


def _call_details(tool_name: str, args: tuple[Any, ...], kwargs: dict[str, Any], function: Callable) -> dict[str, Any]:
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        values = bound.arguments
    except (AttributeError, TypeError, ValueError):
        values = kwargs
    details: dict[str, Any] = {}
    for key in ("site", "auth_profile", "cookie_profile", "mode", "finalize", "force"):
        if key in values:
            value = values[key]
            details[key] = _safe(value, 120) if isinstance(value, str) else value
    if "url" in values:
        details["target_host"] = _target_host(values["url"])
    if "urls" in values and isinstance(values["urls"], (list, tuple)):
        details["url_count"] = len(values["urls"])
        details["target_hosts"] = sorted({host for host in (_target_host(url) for url in values["urls"])
                                           if host})[:20]
    return details


def _result_summary(result: Any) -> tuple[bool | None, str | None]:
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        success = data.get("success")
        error_code = data.get("error_code")
        return (success if isinstance(success, bool) else None,
                _safe(error_code, 80) if error_code else None)
    is_error = getattr(result, "isError", None)
    return ((not is_error) if isinstance(is_error, bool) else None, None)


def audit_tool(tool_name: str):
    """Decorate an async MCP handler with a safe start/finish audit record."""
    def decorator(function):
        @wraps(function)
        async def wrapper(*args, **kwargs):
            request_id = secrets.token_hex(8)
            started = time.monotonic()
            context = caller_context()
            details = _call_details(tool_name, args, kwargs, function)
            base = {
                "timestamp": _timestamp(), "event": "tool_call_started",
                "request_id": request_id, "tool": tool_name,
                "pid": os.getpid(), "caller": context, "details": details,
            }
            _write_call(base)
            try:
                result = await function(*args, **kwargs)
            except BaseException as exc:
                _write_call({
                    "timestamp": _timestamp(), "event": "tool_call_finished",
                    "request_id": request_id, "tool": tool_name,
                    "pid": os.getpid(), "caller": context, "details": details,
                    "status": "exception", "success": False, "error_code": "EXCEPTION",
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })
                raise
            success, error_code = _result_summary(result)
            _write_call({
                "timestamp": _timestamp(), "event": "tool_call_finished",
                "request_id": request_id, "tool": tool_name,
                "pid": os.getpid(), "caller": context, "details": details,
                "status": "success" if success is not False else "error",
                "success": success, "error_code": error_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            })
            return result
        return wrapper
    return decorator


async def run_cli_call(tool_name: str, details: dict[str, Any], operation):
    """Run a CLI-backed tool and record the same safe audit events as MCP."""
    request_id = secrets.token_hex(8)
    started = time.monotonic()
    context = caller_context()
    base = {
        "timestamp": _timestamp(), "event": "tool_call_started",
        "request_id": request_id, "tool": tool_name,
        "transport": "cli", "pid": os.getpid(), "caller": context,
        "details": details,
    }
    _write_call(base)
    try:
        result = await operation()
    except BaseException as exc:
        _write_call({
            "timestamp": _timestamp(), "event": "tool_call_finished",
            "request_id": request_id, "tool": tool_name,
            "transport": "cli", "pid": os.getpid(), "caller": context,
            "details": details, "status": "exception", "success": False,
            "error_code": "EXCEPTION", "error_type": type(exc).__name__,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        })
        raise
    data = result if isinstance(result, dict) else {}
    success = data.get("success") if isinstance(data.get("success"), bool) else None
    _write_call({
        "timestamp": _timestamp(), "event": "tool_call_finished",
        "request_id": request_id, "tool": tool_name,
        "transport": "cli", "pid": os.getpid(), "caller": context,
        "details": details,
        "status": "success" if success is not False else "error",
        "success": success,
        "error_code": data.get("error_code"),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    })
    return result


def read_recent(kind: str, limit: int = 20) -> list[Any]:
    """Read recent log entries for the local management terminal."""
    if kind not in {"runtime", "calls"}:
        raise ValueError("unknown log kind")
    path = log_paths()[kind]
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    lines = lines[-max(1, min(limit, 100)):]
    if kind == "runtime":
        return lines
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"status": "invalid_log_line"})
    return records
