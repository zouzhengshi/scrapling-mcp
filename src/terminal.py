"""A small local management terminal for Scrapling MCP.

This is deliberately a line-oriented CLI rather than a full-screen UI so it
works in ordinary terminals and does not interfere with MCP's stdout protocol.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from src.agent_guide import agent_guide_data, connection_config_data
from src.audit_log import log_paths, read_recent, runtime_event
from src.config import (
    TOOL_DESCRIPTIONS,
    TOOL_NAMES,
    ConfigError,
    config_path,
    is_tool_enabled,
    set_tool_enabled,
    tool_states,
)
from src.diagnostics import check
from src.egress import upstream_proxy_info


APP_VERSION = "1.2.0"
MAX_COOKIE_FILE_BYTES = 1_000_000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_ENTRYPOINT = os.path.normcase(os.path.abspath(str(PROJECT_ROOT / "main.py")))

_USE_COLOR = False
_RESET = "\033[0m"
_COLORS = {
    "red": "\033[91m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "dim": "\033[90m",
}


def _enable_windows_ansi() -> bool:
    """Enable ANSI colors on Windows consoles without adding a dependency."""
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, TypeError):
        return False


def _configure_colors(*, disabled: bool = False) -> None:
    global _USE_COLOR
    if disabled or os.environ.get("NO_COLOR") is not None:
        _USE_COLOR = False
        return
    try:
        is_tty = bool(sys.stdout.isatty())
    except (AttributeError, OSError):
        is_tty = False
    _USE_COLOR = is_tty and _enable_windows_ansi()


def _paint(value: Any, color: str) -> str:
    text = str(value)
    if not _USE_COLOR or color not in _COLORS:
        return text
    return f"{_COLORS[color]}{text}{_RESET}"


def _safe_text(value: Any, limit: int = 160) -> str:
    """Make local metadata safe to print as one terminal line."""
    text = str(value)
    text = "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in text)
    return text[:limit]


def _unique_strings(values: Any, *, strip_dot: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        value = value.lstrip(".") if strip_dot else value
        value = _safe_text(value)
        if value not in result:
            result.append(value)
    return sorted(result, key=str.casefold)


def _summarise_cookie_file() -> dict[str, Any]:
    raw = os.environ.get("SCRAPLING_COOKIE_FILE")
    result: dict[str, Any] = {
        "configured": bool(raw),
        "path": str(Path(raw).expanduser()) if raw else None,
        "profiles": [],
        "values_hidden": True,
    }
    if not raw:
        return result
    path = Path(raw).expanduser()
    try:
        if not path.is_file() or path.stat().st_size > MAX_COOKIE_FILE_BYTES:
            result["error"] = "Cookie 文件不存在、不是普通文件或过大"
            return result
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["error"] = "Cookie 文件无法读取或不是有效 JSON"
        return result
    profiles = document.get("profiles") if isinstance(document, dict) and "profiles" in document else document
    if not isinstance(profiles, dict):
        result["error"] = "Cookie 文件格式无效"
        return result
    for name in sorted(profiles, key=str.casefold):
        profile = profiles[name]
        if not isinstance(name, str) or not isinstance(profile, dict):
            continue
        entries = profile.get("cookies", [])
        entries = entries if isinstance(entries, list) else []
        names = _unique_strings([entry.get("name") for entry in entries if isinstance(entry, dict)])
        domains = _unique_strings(
            [entry.get("domain") for entry in entries if isinstance(entry, dict)], strip_dot=True,
        )
        result["profiles"].append({
            "name": _safe_text(name),
            "allowed_domains": _unique_strings(profile.get("allowed_domains")),
            "cookie_count": len(entries),
            "cookie_names": names,
            "cookie_domains": domains,
        })
    return result


def _auth_state_file_summary() -> dict[str, Any]:
    from src import auth

    try:
        root = auth._auth_root()
    except Exception as exc:
        return {"path": None, "profiles": [], "error": _safe_text(str(exc))}
    result: dict[str, Any] = {"path": str(root), "profiles": [], "values_hidden": True}
    for path in sorted(root.glob("*.state.json"), key=lambda item: item.name.casefold()):
        profile = path.name[:-len(".state.json")]
        item: dict[str, Any] = {"name": _safe_text(profile), "file": str(path)}
        try:
            document = auth._read_state(path)
            entries = document.get("cookies", [])
            entries = entries if isinstance(entries, list) else []
            names = _unique_strings([entry.get("name") for entry in entries if isinstance(entry, dict)])
            domains = _unique_strings(
                [entry.get("domain") for entry in entries if isinstance(entry, dict)], strip_dot=True,
            )
            origins = document.get("origins", [])
            origin_values = [origin.get("origin") for origin in origins if isinstance(origin, dict)]
            origin_domains = []
            for value in origin_values:
                if isinstance(value, str):
                    try:
                        origin_domains.append(urlsplit(value).hostname or value)
                    except ValueError:
                        origin_domains.append(value)
            domains = _unique_strings(domains + origin_domains)
            try:
                allowed = auth._profile_allowed_domains(profile, document, root)
                allowed_domains = list(allowed)
            except Exception:
                allowed_domains = []
            item.update({
                "status": "ready" if auth._state_is_ready(profile, document) else "not_ready",
                "kind": "custom" if isinstance(document.get("scrapling_auth"), dict)
                and document["scrapling_auth"].get("kind") == "custom" else "preset",
                "cookie_count": len(entries),
                "cookie_names": names,
                "cookie_domains": domains,
                "allowed_domains": allowed_domains,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })
        except Exception as exc:
            item.update({"status": "invalid", "error": _safe_text(str(exc))})
        result["profiles"].append(item)
    return result


def _path_matches_entrypoint(value: Any) -> bool:
    """Return whether a process argument is this project's main.py."""
    if not isinstance(value, str) or not value.lower().endswith(".py"):
        return False
    try:
        candidate = os.path.normcase(os.path.abspath(value.strip().strip('"')))
    except (OSError, TypeError, ValueError):
        return False
    return candidate == MAIN_ENTRYPOINT


def _server_process_objects() -> list[Any]:
    """Find this project's MCP entrypoint processes, excluding this terminal."""
    processes: list[Any] = []
    try:
        import psutil  # optional runtime convenience, installed by requirements-dev
    except ImportError:
        return processes
    current_pid = os.getpid()
    try:
        for process in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            info = process.info or {}
            if info.get("pid") == current_pid:
                continue
            cmdline = info.get("cmdline") or []
            if "--terminal" in cmdline or not any(_path_matches_entrypoint(arg) for arg in cmdline):
                continue
            processes.append(process)
    except Exception:
        return []
    return processes


def _server_processes() -> list[dict[str, Any]]:
    """Best-effort discovery; stdio services are normally client-owned."""
    processes = []
    for process in _server_process_objects():
        try:
            info = process.info or {}
            command = " ".join(info.get("cmdline") or [])
            processes.append({
                "pid": info.get("pid"),
                "parent_pid": info.get("ppid"),
                "command": _safe_text(command, 240),
            })
        except Exception:
            continue
    return processes


def restart_mcp() -> dict[str, Any]:
    """Stop the client-owned MCP process tree so the client can respawn it.

    MCP stdio servers are normally started and supervised by the MCP client.
    Starting a second copy from this terminal would not have the client's stdio
    pipe, so a restart means terminating only this project's validated process
    tree and letting the client reconnect it.
    """
    try:
        import psutil
    except ImportError:
        return {
            "success": False,
            "found": 0,
            "requested": 0,
            "terminated": 0,
            "remaining": 0,
            "message": "无法重启：当前 Python 环境没有安装 psutil。",
        }

    processes = _server_process_objects()
    if not processes:
        return {
            "success": False,
            "found": 0,
            "requested": 0,
            "terminated": 0,
            "remaining": 0,
            "message": "未发现当前项目的 MCP 服务进程。stdio 服务由 MCP 客户端启动，请先确认客户端已连接。",
        }

    process_by_pid: dict[int, Any] = {}
    for process in processes:
        try:
            process_by_pid[process.pid] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not process_by_pid:
        return {
            "success": False,
            "found": 0,
            "requested": 0,
            "terminated": 0,
            "remaining": 0,
            "message": "MCP 服务进程在检查期间已经退出，无需重启。",
        }

    # Only terminate roots here. Their complete descendant tree is added below,
    # which covers the launcher, the Python server and active worker/browser
    # children without touching unrelated Python processes.
    roots = []
    for process in process_by_pid.values():
        try:
            if process.ppid() not in process_by_pid:
                roots.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    targets: dict[int, Any] = {}
    for root in roots:
        try:
            targets[root.pid] = root
            for child in root.children(recursive=True):
                targets[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    requested = 0
    errors = 0
    target_list = list(targets.values())
    # Children first reduces the chance that a launcher immediately respawns
    # a child while the restart is in progress.
    for process in reversed(target_list):
        try:
            process.terminate()
            requested += 1
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, OSError):
            errors += 1

    gone, alive = psutil.wait_procs(target_list, timeout=5)
    # A validated process tree that ignores graceful termination is safe to
    # force-stop; leaving it alive would make the MCP client talk to stale code.
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, OSError):
            errors += 1
    if alive:
        gone_after_kill, alive = psutil.wait_procs(alive, timeout=3)
        gone = list(gone) + list(gone_after_kill)

    terminated = len(gone)
    remaining = len(alive)
    runtime_event(
        "mcp_restart_requested",
        found=len(process_by_pid),
        requested=requested,
        terminated=terminated,
        remaining=remaining,
        errors=errors,
    )

    # Give a supervising MCP client a short opportunity to spawn the fresh
    # stdio process before returning the human-readable result.
    time.sleep(0.5)
    if remaining:
        message = f"已请求重启，但仍有 {remaining} 个 MCP 进程未退出，请检查权限或手动重连 MCP 客户端。"
        success = False
    else:
        message = "已停止旧的 MCP 服务进程。MCP 客户端应自动重新拉起；如果未恢复，请重新连接 MCP 客户端。"
        success = True
    return {
        "success": success,
        "found": len(process_by_pid),
        "requested": requested,
        "terminated": terminated,
        "remaining": remaining,
        "errors": errors,
        "message": message,
    }


def status_data() -> dict[str, Any]:
    try:
        states = tool_states()
        config_error = None
    except ConfigError as exc:
        states = {}
        config_error = str(exc)
    report = check()
    auth = _auth_state_file_summary()
    cookie_file = os.environ.get("SCRAPLING_COOKIE_FILE")
    paths = log_paths()
    proxy = upstream_proxy_info()
    return {
        "version": APP_VERSION,
        "python": report.get("python"),
        "python_executable": sys.executable,
        "terminal_pid": os.getpid(),
        "terminal_parent_pid": os.getppid(),
        "mcp_transport": "stdio（由 MCP 客户端按需启动）",
        "mcp_server_processes": _server_processes(),
        "config_file": str(config_path()),
        "config_error": config_error,
        "tools": states,
        "cookie_file": str(Path(cookie_file).expanduser()) if cookie_file else None,
        "cookie_file_exists": bool(cookie_file and Path(cookie_file).expanduser().is_file()),
        "upstream_proxy": proxy,
        "auth_dir": auth.get("path"),
        "auth_profile_count": len(auth.get("profiles", [])),
        "active_login_sessions": _active_login_sessions(),
        "logs": {
            "directory": str(paths["directory"]),
            "runtime": str(paths["runtime"]),
            "calls": str(paths["calls"]),
            "runtime_exists": paths["runtime"].is_file(),
            "calls_exists": paths["calls"].is_file(),
        },
        "diagnostics": report,
    }


def _active_login_sessions() -> int:
    try:
        from src import auth
        return len(auth._sessions)
    except Exception:
        return 0


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _print_status(data: dict[str, Any]) -> None:
    diagnostics = data["diagnostics"]
    print(_paint("=== Scrapling MCP 运行状态 ===", "cyan"))
    print(f"版本: {data['version']}")
    print(f"Python: {data['python']} | {data['python_executable']}")
    print(f"终端进程: PID {data['terminal_pid']} | 父进程 {data['terminal_parent_pid']}")
    print(f"MCP 传输: {data['mcp_transport']}")
    server_processes = data["mcp_server_processes"]
    if server_processes:
        print(_paint("MCP 服务进程: " + ", ".join(f"PID {item['pid']}" for item in server_processes), "green"))
    else:
        print(_paint("MCP 服务进程: 当前未发现（stdio 通常由客户端独立启动）", "yellow"))
    print(f"配置文件: {data['config_file']}")
    if data["config_error"]:
        print(_paint(f"配置状态: 错误：{data['config_error']}", "red"))
    else:
        enabled = sum(data["tools"].values())
        print(_paint(f"工具开关: {enabled}/{len(TOOL_NAMES)} 启用",
                     "green" if enabled == len(TOOL_NAMES) else "yellow"))
    print(f"Cookie 文件: {data['cookie_file'] or '未配置'}")
    if data["cookie_file"]:
        print(f"Cookie 文件状态: {'存在' if data['cookie_file_exists'] else '不存在'}")
    proxy = data["upstream_proxy"]
    if proxy.get("active"):
        auth_text = "（已配置认证）" if proxy.get("authentication") else ""
        mode_text = "自动模式" if proxy.get("mode") == "auto" else "显式配置"
        source_text = f"，来源：{proxy['source']}" if proxy.get("source") else ""
        print(_paint(f"上游代理/VPN 出口: {proxy['proxy']}（{mode_text}{source_text}）{auth_text}", "green"))
        routes = proxy.get("routes") or {}
        if len(routes) > 1:
            print("代理路由: " + ", ".join(f"{name}={value}" for name, value in routes.items()))
        if proxy.get("bypass"):
            print("代理绕过: " + ", ".join(proxy["bypass"]))
    elif proxy.get("error"):
        print(_paint(f"上游代理/VPN 出口: 配置错误：{proxy['error']}", "red"))
    else:
        if proxy.get("pac_detected"):
            print(_paint("上游代理/VPN 出口: 未执行 PAC/WPAD（请改用代理软件本地端口或显式配置）", "yellow"))
        else:
            print(_paint("上游代理/VPN 出口: 直连（如果系统 VPN 已连接，会使用系统路由）", "yellow"))
    print(f"认证状态目录: {data['auth_dir'] or '不可用'}")
    print(f"认证配置: {data['auth_profile_count']} 个 | 活跃登录会话: {data['active_login_sessions']} 个")
    print(f"运行日志: {data['logs']['runtime']}")
    print(f"调用日志: {data['logs']['calls']}")
    print(_paint(f"依赖/浏览器检查: {'就绪' if diagnostics['ready'] else '未就绪'}",
                 "green" if diagnostics["ready"] else "red"))
    missing = [name for name, version in diagnostics["dependencies"].items() if not version]
    missing += [name for name, item in diagnostics["browsers"].items() if not item.get("installed")]
    if missing:
        print(_paint("缺失项: " + ", ".join(missing), "red"))


def _print_tools(states: dict[str, bool]) -> None:
    print(_paint(f"=== MCP 工具开关（配置：{config_path()}）===", "cyan"))
    for name in TOOL_NAMES:
        state = "启用" if states[name] else "停用"
        print(_paint(f"[{state}] {name} - {TOOL_DESCRIPTIONS[name]}",
                     "green" if states[name] else "red"))
    print(_paint("提示：停用工具仍可能显示在 Agent 的工具列表中，但调用会返回 TOOL_DISABLED。", "yellow"))


def _print_cookie_group(title: str, data: dict[str, Any]) -> None:
    print(_paint(title, "cyan"))
    if data.get("path"):
        print(f"位置: {data['path']}")
    if data.get("error"):
        print(_paint(f"状态: 错误：{data['error']}", "red"))
        return
    profiles = data.get("profiles", [])
    if not profiles:
        print(_paint("暂无配置", "yellow"))
        return
    for item in profiles:
        names = ", ".join(item.get("cookie_names", [])) or "（无名称）"
        domains = ", ".join(item.get("allowed_domains") or item.get("cookie_domains") or []) or "（未知）"
        status = item.get("status")
        status_text = f" | 状态: {status}" if status else ""
        print(f"- {item.get('name')}: {item.get('cookie_count', 0)} 个 Cookie{status_text}")
        print(f"  域名: {domains}")
        print(f"  名称: {names}")


def cookies_data() -> dict[str, Any]:
    return {"cookie_profiles": _summarise_cookie_file(), "auth_profiles": _auth_state_file_summary(),
            "values_hidden": True}


def logs_data() -> dict[str, Any]:
    paths = log_paths()
    return {
        "directory": str(paths["directory"]),
        "runtime_log": str(paths["runtime"]),
        "calls_log": str(paths["calls"]),
        "runtime": read_recent("runtime"),
        "calls": read_recent("calls"),
        "values_hidden": True,
    }


def _print_logs(data: dict[str, Any], kind: str | None = None) -> None:
    print(_paint("=== 日志位置 ===", "cyan"))
    print(f"目录: {data['directory']}\n运行日志: {data['runtime_log']}\n调用日志: {data['calls_log']}")
    if kind in {None, "runtime"}:
        print("\n" + _paint("=== 最近运行日志 ===", "cyan"))
        runtime_lines = data["runtime"]
        print("\n".join(runtime_lines[-20:]) if runtime_lines else "暂无记录")
    if kind in {None, "calls"}:
        print("\n" + _paint("=== 最近工具调用日志 ===", "cyan"))
        calls = data["calls"]
        if not calls:
            print("暂无记录")
        for item in calls[-20:]:
            caller = item.get("caller") or {}
            caller_name = caller.get("name", "unknown") if isinstance(caller, dict) else str(caller)
            caller_pid = caller.get("pid") if isinstance(caller, dict) else None
            event = item.get("event", "unknown")
            tool = item.get("tool", "unknown")
            suffix = f" | PID {caller_pid}" if caller_pid else ""
            if event == "tool_call_started":
                print(_paint(f"{item.get('timestamp', '')} | {caller_name}{suffix} -> {tool} | 开始调用", "yellow"))
            elif event == "tool_call_finished":
                status = "成功" if item.get("success") is True else "失败"
                error = f" | 错误码: {item['error_code']}" if item.get("error_code") else ""
                color = "green" if item.get("success") is True else "red"
                print(_paint(f"{item.get('timestamp', '')} | {caller_name}{suffix} -> {tool} | {status} | "
                             f"耗时 {item.get('duration_ms', '?')} ms{error}", color))
            else:
                print(f"{item.get('timestamp', '')} | {caller_name}{suffix} -> {tool} | {event}")


def _help_data() -> list[dict[str, str]]:
    return [
        {"command": "status", "description": "查看程序运行状态、依赖、浏览器和进程信息"},
        {"command": "cookies", "description": "查看已有登录配置；只显示名称、域名和数量，不显示 Cookie 值"},
        {"command": "tools", "description": "查看所有 MCP 工具当前是启用还是停用"},
        {"command": "guide", "description": "显示可复制给 AI Agent 的完整 MCP 使用说明"},
        {"command": "logs", "description": "查看日志文件位置和最近的运行/调用记录"},
        {"command": "logs calls", "description": "只查看最近是谁调用了什么工具"},
        {"command": "logs runtime", "description": "只查看最近的程序运行记录"},
        {"command": "restart", "description": "停止当前项目 MCP 进程，让 MCP 客户端自动重新拉起服务"},
        {"command": "tool enable NAME", "description": "启用指定工具，例如：tool enable scrape"},
        {"command": "tool disable NAME", "description": "停用指定工具，例如：tool disable scrape_batch"},
        {"command": "help", "description": "显示这份命令说明"},
        {"command": "exit", "description": "退出管理终端（quit 和 q 也可以）"},
    ]


def _print_help() -> None:
    print(_paint("=== Scrapling MCP 管理终端命令 ===", "cyan"))
    for item in _help_data():
        print(_paint(f"{item['command'] + ':':<28}", "blue") + f" {item['description']}")
    print()
    print("工具名称可以先用 tools 查看。常用示例：")
    print("  guide                       显示并复制给 Agent 的使用说明")
    print("  tool disable scrape_batch   暂停批量抓取")
    print("  tool enable scrape_batch    恢复批量抓取")
    print("  restart                     重启 MCP 服务（不会删除 Cookie 或日志）")
    print("  logs calls                  查看最近是谁调用了什么工具")
    print("说明：Cookie 值、localStorage 值、密码和验证码始终不会显示。")


def _print_agent_guide() -> None:
    """Print the copyable Agent guide without exposing local credentials."""
    data = agent_guide_data()
    _print_connection_guide()
    print()
    print(_paint(f"=== {data['title']} ===", "cyan"))
    print(data["copy_text"])
    print(_paint("提示：MCP 初始化时也会自动把这份说明发送给支持 instructions 的 Agent。", "yellow"))


def _print_connection_guide() -> None:
    """Print a ready-to-paste stdio configuration for another Agent client."""
    data = connection_config_data(
        python_executable=sys.executable,
        project_root=str(PROJECT_ROOT),
    )
    print(_paint("=== MCP 连接方式（请复制给 AI Agent 客户端）===", "magenta"))
    print(_paint("在支持 stdio MCP 的客户端中添加下面 JSON；不要启动 --terminal 作为 MCP 服务：", "yellow"))
    print(_paint(json.dumps(data, ensure_ascii=False, indent=2), "magenta"))
    print(_paint("连接命令：", "cyan"))
    entrypoint = PROJECT_ROOT / "main.py"
    print(_paint(f'"{sys.executable}" "{entrypoint}"', "blue"))


def _dispatch(command: list[str], *, json_output: bool = False) -> int:
    # Only record the command category; never write arbitrary terminal input.
    runtime_event("terminal_command", command=command[0] if command else "")
    if not command or command[0] in {"help", "-h", "--help"}:
        if json_output:
            _print_json({"commands": _help_data(), "values_hidden": True})
        else:
            _print_help()
        return 0
    name = command[0].lower()
    if name in {"guide", "agent-guide", "agent_guide", "prompt"} and len(command) == 1:
        data = agent_guide_data()
        if json_output:
            _print_json(data)
        else:
            _print_agent_guide()
        return 0
    if name in {"exit", "quit", "q"}:
        return 1
    if name == "restart" and len(command) == 1:
        data = restart_mcp()
        if json_output:
            _print_json(data)
        else:
            print(_paint("=== MCP 服务重启 ===", "cyan"))
            print(_paint(data["message"], "green" if data.get("success") else "red"))
            print(f"发现: {data.get('found', 0)} | 请求停止: {data.get('requested', 0)} | "
                  f"已退出: {data.get('terminated', 0)}")
        return 0 if data.get("success") else 1
    if name == "status" and len(command) == 1:
        data = status_data()
        _print_json(data) if json_output else _print_status(data)
        return 0
    if name == "cookies" and len(command) == 1:
        data = cookies_data()
        if json_output:
            _print_json(data)
        else:
            _print_cookie_group("=== 手动 Cookie profiles（值已隐藏）===", data["cookie_profiles"])
            _print_cookie_group("=== 浏览器登录 profiles（值已隐藏）===", data["auth_profiles"])
        return 0
    if name == "logs" and len(command) in {1, 2}:
        kind = command[1].lower() if len(command) == 2 else None
        if kind not in {None, "runtime", "calls"}:
            print(_paint("logs 后只能使用 runtime 或 calls。", "red"), file=sys.stderr)
            return 2
        data = logs_data()
        if json_output:
            if kind:
                data = {**data, "records": data[kind]}
            _print_json(data)
        else:
            _print_logs(data, kind)
        return 0
    if name == "tools" and len(command) == 1:
        try:
            states = tool_states()
        except ConfigError as exc:
            print(_paint(f"配置错误：{exc}", "red"), file=sys.stderr)
            return 2
        _print_json(states) if json_output else _print_tools(states)
        return 0
    if name == "tool" and len(command) == 3 and command[1].lower() in {"enable", "disable"}:
        tool_name = command[2]
        try:
            path = set_tool_enabled(tool_name, command[1].lower() == "enable")
            enabled = is_tool_enabled(tool_name)
        except ConfigError as exc:
            print(_paint(f"配置错误：{exc}", "red"), file=sys.stderr)
            return 2
        data = {"tool": tool_name, "enabled": enabled, "config_file": str(path)}
        if json_output:
            _print_json(data)
        else:
            print(_paint(f"已{'启用' if enabled else '停用'}工具：{tool_name}",
                         "green" if enabled else "yellow"))
            print(f"配置已保存：{path}")
            print(_paint("后续调用立即生效；Agent 工具列表可能仍保留该工具名称。", "yellow"))
        return 0
    print(_paint("命令无效。输入 help 查看用法。", "red"), file=sys.stderr)
    return 2


def run_terminal(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
    parser = argparse.ArgumentParser(description="Scrapling MCP 本地管理终端")
    parser.add_argument("--json", action="store_true", help="以脱敏 JSON 输出")
    parser.add_argument("--no-color", action="store_true", help="关闭终端颜色")
    parser.add_argument("command", nargs="*", help="status/cookies/tools/guide/restart/tool enable NAME")
    args = parser.parse_args(argv)
    _configure_colors(disabled=args.json or args.no_color)
    if args.command:
        result = _dispatch(args.command, json_output=args.json)
        # ``1`` is the interactive exit sentinel, but is an actual failure
        # status for one-shot commands such as ``restart``.
        if result == 1 and args.command[0].lower() in {"exit", "quit", "q"}:
            return 0
        return result
    print(_paint("Scrapling MCP 管理终端（输入 help 查看命令，输入 exit 退出）", "cyan"))
    print("输入 help 查看每条命令的中文说明；Agent 使用说明会由 MCP 自动发送；Cookie 值始终隐藏。")
    _print_connection_guide()
    while True:
        try:
            line = input("scrapling> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        try:
            command = shlex.split(line)
        except ValueError:
            print("命令引号不匹配，请重试。", file=sys.stderr)
            continue
        result = _dispatch(command, json_output=args.json)
        if result == 1:
            return 0
