@echo off
setlocal EnableExtensions

rem Scrapling MCP 快速启动脚本。
rem 默认打开本地管理终端；参数会转发给管理终端命令。
rem 示例：scrapling.bat status / scrapling.bat guide / scrapling.bat restart

set "ROOT=%~dp0"
set "PYTHON=%ROOT%scrapling_env\Scripts\python.exe"
set "ENTRYPOINT=%ROOT%main.py"

if not exist "%PYTHON%" (
    echo [错误] 未找到虚拟环境：%PYTHON%
    echo 请先按 README.md 完成安装，或运行：python -m venv scrapling_env
    exit /b 1
)

rem --mcp 用于手动启动 stdio 服务；正常情况下应由 MCP 客户端启动。
if /I "%~1"=="--mcp" (
    shift
    "%PYTHON%" "%ENTRYPOINT%" %*
    exit /b %ERRORLEVEL%
)

rem 这些参数属于入口程序本身，不应被转发到管理终端。
if /I "%~1"=="--check" (
    "%PYTHON%" "%ENTRYPOINT%" %*
    exit /b %ERRORLEVEL%
)
if /I "%~1"=="--agent-guide" (
    "%PYTHON%" "%ENTRYPOINT%" %*
    exit /b %ERRORLEVEL%
)
if /I "%~1"=="--version" (
    "%PYTHON%" "%ENTRYPOINT%" %*
    exit /b %ERRORLEVEL%
)

"%PYTHON%" "%ENTRYPOINT%" --terminal %*
exit /b %ERRORLEVEL%
