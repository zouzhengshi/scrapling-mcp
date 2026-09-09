# -*- coding: utf-8 -*-
"""Scrapling MCP 服务器入口。

用法:
    python main.py                # stdio 传输（供 MCP 客户端调用）
    python main.py --help         # 查看帮助
"""

import json
import sys
import os

# 确保 src/ 目录在导入路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    # Diagnostics stay usable even when optional MCP/browser packages are absent.
    if "--check" in sys.argv:
        from src.diagnostics import check
        report = check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["ready"] else 1)
    if "--version" in sys.argv:
        print("Scrapling MCP 1.0.0")
        raise SystemExit(0)
    from src.mcp_server import main
    main()
