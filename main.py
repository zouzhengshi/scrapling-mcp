# -*- coding: utf-8 -*-
"""Scrapling MCP 服务器入口。

用法:
    python main.py                # stdio 传输（供 MCP 客户端调用）
    python main.py --agent-guide  # 显示可复制给 AI Agent 的使用说明
    python main.py --terminal     # 打开本地管理终端
    python main.py --terminal status
    python main.py --help         # 查看帮助
"""

import json
import sys
import os

# 确保 src/ 目录在导入路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    if "--agent-guide" in sys.argv:
        from src.agent_guide import render_agent_guide
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(render_agent_guide(
            python_executable=sys.executable,
            project_root=os.path.dirname(os.path.abspath(__file__)),
        ))
        raise SystemExit(0)
    if "--terminal" in sys.argv:
        from src.audit_log import close_logging, configure_logging, runtime_event
        from src.terminal import run_terminal
        terminal_index = sys.argv.index("--terminal")
        try:
            log_directory = configure_logging()
            runtime_event("terminal_started", log_directory=str(log_directory), pid=os.getpid())
        except OSError:
            pass
        try:
            raise SystemExit(run_terminal(sys.argv[terminal_index + 1:]))
        finally:
            runtime_event("terminal_stopped", pid=os.getpid())
            close_logging()
    # Diagnostics stay usable even when optional MCP/browser packages are absent.
    if "--check" in sys.argv:
        from src.diagnostics import check
        report = check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["ready"] else 1)
    if "--version" in sys.argv:
        print("Scrapling MCP 1.2.0")
        raise SystemExit(0)
    from src.mcp_server import main
    main()
