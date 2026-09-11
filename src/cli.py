"""Installed command-line entry point for Scrapling MCP."""
from __future__ import annotations

import json
import os
import sys
import asyncio
from urllib.parse import urlsplit

from src.agent_guide import render_agent_guide
from src.audit_log import close_logging, configure_logging, run_cli_call, runtime_event
from src.auth import (SITE_PRESETS, AuthProfileError,
                      login_status as get_login_status,
                      start_custom_login, start_login)
from src.config import ConfigError, is_tool_enabled
from src.engine import ScraplingEngine
from src.models import failure
from src.terminal import APP_VERSION, run_terminal


def _utf8_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _print_help() -> None:
    _utf8_stdout()
    print("Scrapling MCP CLI")
    print()
    print("用法：")
    print("  scrapling-mcp terminal                启动本地管理终端（交互模式）")
    print("  scrapling-mcp status                  查看运行状态")
    print("  scrapling-mcp cookies                 查看脱敏登录配置")
    print("  scrapling-mcp profiles                cookies 的快捷别名")
    print("  scrapling-mcp tools                   查看工具开关")
    print("  scrapling-mcp guide                   查看连接配置和 Agent 说明")
    print("  scrapling-mcp logs calls              查看工具调用日志")
    print("  scrapling-mcp restart                 重启 MCP 服务")
    print("  scrapling-mcp doctor                  检查依赖、浏览器和配置")
    print("  scrapling-mcp scrape URL              通过 CLI 抓取一个网页")
    print("  scrapling-mcp scrape_batch URL...     通过 CLI 批量抓取网页")
    print("  scrapling-mcp login SITE              打开预设网站登录窗口并等待完成")
    print("  scrapling-mcp login_status SITE      查询/确认预设登录状态")
    print("  scrapling-mcp login_custom PROFILE URL  登录未预设的网站")
    print("  scrapling-mcp login_custom_status PROFILE  查询/确认自定义登录状态")
    print("  scrapling-mcp --check                 检查依赖和浏览器")
    print("  scrapling-mcp --agent-guide           输出可复制给 Agent 的说明")
    print("  scrapling-mcp --version               查看版本")
    print()
    print("核心工具默认按终端自动选择人类可读输出；脚本请使用 --format json。")
    print()
    print("注意：底层 Scrapling 库已经占用 scrapling 命令，本项目使用 scrapling-mcp 避免冲突。")


def _run_check() -> int:
    from src.diagnostics import check

    _utf8_stdout()
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


def _run_management_terminal(argv: list[str]) -> int:
    try:
        log_directory = configure_logging()
        runtime_event("terminal_started", log_directory=str(log_directory), pid=os.getpid(),
                      cli="scrapling-mcp")
    except OSError:
        pass
    try:
        return run_terminal(argv)
    finally:
        runtime_event("terminal_stopped", pid=os.getpid(), cli="scrapling-mcp")
        close_logging()


def _tool_parser(command: str):
    import argparse

    parser = argparse.ArgumentParser(prog=f"scrapling-mcp {command}")
    parser.add_argument(
        "--format", dest="output_format", choices=("auto", "human", "json"),
        default="auto", help="输出格式：auto（TTY 人类可读，否则 JSON）、human 或 json",
    )
    if command == "scrape":
        parser.add_argument("url")
        parser.add_argument("--mode", choices=("auto", "fast", "stealth"), default="auto")
        parser.add_argument("--timeout", type=float, default=30.0)
        parser.add_argument("--max-chars", type=int, default=50000)
        parser.add_argument("--css-selector")
        parser.add_argument("--wait-for")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--cookie-profile")
        group.add_argument("--auth-profile")
        parser.add_argument("--no-main-content", action="store_false", dest="main_content")
        parser.set_defaults(main_content=True)
        parser.add_argument("--no-links", action="store_false", dest="include_links")
        parser.set_defaults(include_links=True)
    elif command == "scrape_batch":
        parser.add_argument("urls", nargs="+")
        parser.add_argument("--mode", choices=("auto", "fast", "stealth"), default="auto")
        parser.add_argument("--timeout", type=float, default=30.0)
        parser.add_argument("--max-chars", type=int, default=10000)
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--cookie-profile")
        group.add_argument("--auth-profile")
    elif command == "login":
        parser.add_argument("site", choices=tuple(SITE_PRESETS))
        parser.add_argument("--timeout", type=float, default=300.0)
        parser.add_argument("--force", action="store_true")
    elif command == "login_status":
        parser.add_argument("site", choices=tuple(SITE_PRESETS))
        parser.add_argument("--finalize", action="store_true")
    elif command == "login_custom":
        parser.add_argument("auth_profile")
        parser.add_argument("url")
        parser.add_argument("--allowed-domain", action="append", dest="allowed_domains")
        parser.add_argument("--timeout", type=float, default=300.0)
        parser.add_argument("--force", action="store_true")
    elif command == "login_custom_status":
        parser.add_argument("auth_profile")
        parser.add_argument("--finalize", action="store_true")
    return parser


def _tool_error(message: str, error_code: str = "CLI_ERROR") -> dict[str, object]:
    return {"success": False, "error_code": error_code, "message": message}


def _print_human_tool_result(command: str, result: dict) -> None:
    """Render a concise CLI result without exposing the protocol envelope."""
    if result.get("success") is False:
        code = result.get("error_code") or "UNKNOWN_ERROR"
        message = result.get("message") or result.get("error") or "操作失败"
        print(f"❌ 失败 [{code}]：{message}")
        return
    if command == "scrape":
        summary = result.get("summary") or {}
        print("✅ 抓取完成")
        print(f"标题：{result.get('title') or '（无标题）'}")
        print(f"状态：HTTP {result.get('status_code') or '未知'} | 地址：{result.get('final_url') or result.get('url')}")
        if summary:
            print(f"摘要：{summary.get('text') or summary.get('description') or '（无摘要）'}")
        if result.get("truncated"):
            print("⚠️ 正文已截断，若需要更多内容请提高 max-chars。")
        markdown = result.get("markdown") or ""
        if markdown:
            print("\n--- 正文 ---\n" + markdown)
        return
    if command == "scrape_batch":
        print(f"{'✅' if result.get('success') else '⚠️'} {result.get('message', '批量抓取完成')}")
        for index, item in enumerate(result.get("results") or [], start=1):
            marker = "✅" if item.get("success") else "❌"
            title = item.get("title") or item.get("url") or "未知地址"
            suffix = "" if item.get("success") else f"：{item.get('error_code') or item.get('error') or '失败'}"
            print(f"{marker} {index}. {title}{suffix}")
        return
    print("✅ 操作完成")
    if result.get("site"):
        print(f"网站：{result['site']}")
    if result.get("auth_profile"):
        print(f"登录配置：{result['auth_profile']}")
    if result.get("status"):
        print(f"状态：{result['status']}")
    if result.get("ready") is not None:
        print(f"已就绪：{'是' if result['ready'] else '否'}")
    if result.get("message"):
        print(result["message"])
    if result.get("next_action"):
        print(f"下一步：{result['next_action']}")


def _print_tool_result(command: str, result: dict, output_format: str) -> None:
    use_json = output_format == "json"
    if output_format == "auto":
        try:
            use_json = not bool(sys.stdout.isatty())
        except (AttributeError, OSError):
            use_json = True
    _utf8_stdout()
    if use_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human_tool_result(command, result)


def _target_host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _engine() -> ScraplingEngine:
    # Reuse the same trusted deployment configuration as MCP handlers.
    from src.mcp_server import _获取引擎

    return _获取引擎()


async def _cli_scrape(args) -> dict:
    try:
        if not is_tool_enabled("scrape"):
            return failure(args.url, "config", "TOOL_DISABLED", "MCP 工具 scrape 已停用").to_dict()
        result = await _engine().scrape(
            args.url, args.mode, args.timeout, args.max_chars,
            css_selector=args.css_selector, wait_for=args.wait_for,
            main_content=args.main_content, include_links=args.include_links,
            cookie_profile=args.cookie_profile, auth_profile=args.auth_profile,
        )
        return result.to_dict()
    except (ConfigError, ValueError) as exc:
        return _tool_error(str(exc), "INVALID_ARGUMENT")
    except Exception:
        return _tool_error("抓取服务内部错误", "ENGINE_ERROR")


async def _cli_scrape_batch(args) -> dict:
    try:
        if not is_tool_enabled("scrape_batch"):
            return {"success": False, "total": len(args.urls), "succeeded": 0,
                    "failed": len(args.urls), "message": "MCP 工具 scrape_batch 已停用",
                    "error_code": "TOOL_DISABLED", "results": []}
        engine = _engine()
        results = await asyncio.gather(*(
            engine.scrape(
                url, args.mode, args.timeout, args.max_chars,
                cookie_profile=args.cookie_profile, auth_profile=args.auth_profile,
            ) for url in args.urls
        ))
        succeeded = sum(result.success for result in results)
        return {
            "success": succeeded == len(results), "total": len(results),
            "succeeded": succeeded, "failed": len(results) - succeeded,
            "message": f"批量抓取完成：成功 {succeeded} 个，失败 {len(results) - succeeded} 个",
            "results": [result.to_dict() for result in results],
        }
    except (ConfigError, ValueError) as exc:
        return _tool_error(str(exc), "INVALID_ARGUMENT")
    except Exception:
        return _tool_error("批量抓取服务内部错误", "ENGINE_ERROR")


async def _wait_custom_login(profile: str, timeout: float) -> dict:
    from src import auth

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and profile in auth._sessions:
        await asyncio.sleep(1)
    return await get_login_status(profile, os.environ.get("SCRAPLING_AUTH_DIR"), True)


async def _wait_login(profile: str, timeout: float) -> dict:
    """Keep the CLI alive while the visible preset login browser is in use."""
    from src import auth

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and profile in auth._sessions:
        await asyncio.sleep(1)
    return await get_login_status(profile, os.environ.get("SCRAPLING_AUTH_DIR"), True)


async def _cli_login(args) -> dict:
    try:
        if not is_tool_enabled("login"):
            return _tool_error("MCP 工具 login 已停用", "TOOL_DISABLED")
        result = await start_login(
            args.site, args.timeout, os.environ.get("SCRAPLING_AUTH_DIR"), args.force,
        )
        if result.get("status") == "ready":
            return result
        # Keep this CLI process alive while the user uses the visible browser,
        # then save and close the session safely.
        return await _wait_login(args.site, args.timeout)
    except AuthProfileError as exc:
        return _tool_error(str(exc), "AUTH_ERROR")


async def _cli_login_custom(args) -> dict:
    try:
        if not is_tool_enabled("login_custom"):
            return _tool_error("MCP 工具 login_custom 已停用", "TOOL_DISABLED")
        result = await start_custom_login(
            args.auth_profile, args.url, args.allowed_domains, args.timeout,
            os.environ.get("SCRAPLING_AUTH_DIR"), args.force,
        )
        if result.get("status") in {"ready", "already_running"}:
            return result
        return await _wait_custom_login(args.auth_profile, args.timeout)
    except AuthProfileError as exc:
        return _tool_error(str(exc), "AUTH_ERROR")


async def _cli_login_status(args, *, custom: bool = False) -> dict:
    try:
        tool_name = "login_custom_status" if custom else "login_status"
        if not is_tool_enabled(tool_name):
            return _tool_error(f"MCP 工具 {tool_name} 已停用", "TOOL_DISABLED")
        profile = args.auth_profile if custom else args.site
        return await get_login_status(
            profile, os.environ.get("SCRAPLING_AUTH_DIR"), args.finalize,
        )
    except AuthProfileError as exc:
        return _tool_error(str(exc), "AUTH_ERROR")


async def _run_tool_operation(command: str, args) -> dict:
    details = {}
    if command in {"scrape", "scrape_batch"}:
        if command == "scrape":
            details = {"target_host": _target_host(args.url), "mode": args.mode,
                       "auth_profile": args.auth_profile, "cookie_profile": args.cookie_profile}
            return await run_cli_call(command, details, lambda: _cli_scrape(args))
        details = {"url_count": len(args.urls), "mode": args.mode,
                   "auth_profile": args.auth_profile, "cookie_profile": args.cookie_profile}
        return await run_cli_call(command, details, lambda: _cli_scrape_batch(args))
    if command == "login":
        details = {"site": args.site, "force": args.force}
        return await run_cli_call(command, details, lambda: _cli_login(args))
    if command == "login_status":
        details = {"site": args.site, "finalize": args.finalize}
        return await run_cli_call(command, details, lambda: _cli_login_status(args))
    if command == "login_custom":
        details = {"auth_profile": args.auth_profile, "target_host": _target_host(args.url),
                   "force": args.force}
        return await run_cli_call(command, details, lambda: _cli_login_custom(args))
    details = {"auth_profile": args.auth_profile, "finalize": args.finalize}
    return await run_cli_call(command, details, lambda: _cli_login_status(args, custom=True))


def _run_tool_command(args: list[str]) -> int:
    command = args[0].replace("-", "_")
    parser = _tool_parser(command)
    try:
        parsed = parser.parse_args(args[1:])
    except SystemExit as exc:
        return int(exc.code)
    try:
        log_directory = configure_logging()
        runtime_event("cli_tool_started", tool=command, log_directory=str(log_directory), pid=os.getpid())
    except OSError:
        pass
    try:
        result = asyncio.run(_run_tool_operation(command, parsed))
        _print_tool_result(command, result, parsed.output_format)
        return 0 if result.get("success") is True else 1
    except (AuthProfileError, ValueError, OSError) as exc:
        _print_tool_result(command, _tool_error(str(exc)), getattr(parsed, "output_format", "json"))
        return 1
    except Exception:
        _print_tool_result(command, _tool_error("CLI 工具执行失败", "CLI_ERROR"),
                           getattr(parsed, "output_format", "json"))
        return 1
    finally:
        runtime_event("cli_tool_stopped", tool=command, pid=os.getpid())
        close_logging()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        _print_help()
        return 0
    if args and args[0] == "--version":
        print(f"Scrapling MCP {APP_VERSION}")
        return 0
    if args and args[0] == "--check":
        return _run_check()
    if args and args[0] == "--agent-guide":
        _utf8_stdout()
        print(render_agent_guide(
            python_executable=sys.executable,
            project_root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ))
        return 0
    if args and args[0] == "--mcp":
        # The MCP client normally starts main.py directly. This switch is a
        # convenience for local tests and keeps the CLI from mixing protocol
        # bytes with terminal output.
        from src.mcp_server import main as mcp_main

        sys.argv = [sys.argv[0], *args[1:]]
        mcp_main()
        return 0
    if args and args[0].replace("-", "_") in {
        "scrape", "scrape_batch", "login", "login_status",
        "login_custom", "login_custom_status",
    }:
        return _run_tool_command(args)
    if args and args[0].lower() in {"terminal", "interactive"}:
        return _run_management_terminal(args[1:])
    if args and args[0].lower() == "doctor":
        return _run_check()
    if args and args[0].lower() == "profiles":
        return _run_management_terminal([*args[1:], "cookies"] if len(args) > 1 else ["cookies"])
    # Preserve the convenient no-argument terminal for a real interactive
    # console, but never block a pipe, CI job, or another program.
    if not args:
        try:
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        except (AttributeError, OSError):
            interactive = False
        if interactive:
            return _run_management_terminal([])
        _print_help()
        return 0
    return _run_management_terminal(args)


if __name__ == "__main__":
    raise SystemExit(main())
