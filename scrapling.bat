@echo off
setlocal EnableExtensions

rem Scrapling MCP 快速启动脚本。
rem 默认打开本地管理终端；参数会转发给管理终端命令。
rem 示例：scrapling.bat status / scrapling.bat guide / scrapling.bat restart

set "ROOT=%~dp0"
rem 支持自定义解释器，也兼容常见的 scrapling_env / .venv / venv 名称。
if defined SCRAPLING_PYTHON (
    set "PYTHON=%SCRAPLING_PYTHON%"
) else if exist "%ROOT%scrapling_env\Scripts\python.exe" (
    set "PYTHON=%ROOT%scrapling_env\Scripts\python.exe"
) else if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PYTHON=%ROOT%.venv\Scripts\python.exe"
) else if exist "%ROOT%venv\Scripts\python.exe" (
    set "PYTHON=%ROOT%venv\Scripts\python.exe"
) else (
    set "PYTHON="
)
set "ENTRYPOINT=%ROOT%main.py"

if not defined PYTHON goto missing_python
if not exist "%PYTHON%" goto missing_python

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

:missing_python
    echo [错误] 未找到 Python 虚拟环境。
    echo 已尝试：scrapling_env、.venv、venv
    echo 也可以设置 SCRAPLING_PYTHON 指向虚拟环境中的 python.exe
    echo 请先按 README.md 完成安装，或运行：python -m venv scrapling_env
    exit /b 1
