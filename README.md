# 🕷️ Scrapling MCP

> 🚀 面向 AI Agent 的安全、通用、可扩展网页抓取 MCP 服务。

面向 AI Agent 的安全通用网页抓取 MCP 服务，基于 Crawl4AI 和 Scrapling，
通过 **stdio MCP** 提供单页、批量抓取和本地交互式登录，也可以直接从 Python 异步调用。

## 📌 项目是什么

Scrapling MCP 是一个让 AI Agent 能够安全调用网页抓取能力的通用工具服务。
Agent 不需要为每个网站单独编写 requests、Playwright 或网页解析代码，
只需要调用统一的 `scrape` 或 `scrape_batch` 工具，就可以获得标题、状态码、最终地址、
Markdown 正文、链接和可判断的错误信息。

项目使用两套抓取引擎：

- Crawl4AI：适合普通网页和快速抓取。
- Scrapling：适合需要浏览器渲染和更强隐身能力的网页。

`auto` 模式会优先使用 Crawl4AI，在可重试的失败场景下切换 Scrapling。
隐身模式不承诺绕过所有反爬挑战，挑战页会被识别并以结构化结果返回。

## 🧩 解决什么问题

AI Agent 在访问网页时通常会遇到以下问题：

1. 不同网站的抓取方式不统一，需要重复编写浏览器、请求和解析逻辑。
2. 很多页面依赖 JavaScript 渲染，普通 HTTP 请求拿不到正文。
3. 网页可能发生重定向、加载子资源或打开弹窗，容易带来 SSRF 和内网访问风险。
4. 浏览器任务超时后可能残留进程、临时文件或占满并发资源。
5. 抓取失败时只返回一段异常文本，Agent 难以判断是超时、限流、被拦截还是参数错误。
6. 网页正文可能包含提示词注入，不能被 Agent 误认为系统指令。

Scrapling MCP 将这些能力统一封装为 MCP 工具，并提供公网 URL 校验、DNS 防护、出口代理、
并发队列、超时控制、进程清理和结构化错误，让 Agent 可以更稳定地读取公开网页内容。

## ✨ 核心功能

| 功能 | 说明 |
| --- | --- |
| MCP 接入 | 通过 stdio 接入支持 MCP 的 AI Agent |
| 本地管理终端 | 查看脱敏 Cookie 概况、工具开关、依赖和运行状态 |
| 运行与调用日志 | 分别记录服务状态和谁在什么时候调用了什么 MCP 工具 |
| 双引擎抓取 | Crawl4AI 快速模式 + Scrapling 隐身模式 |
| 交互式登录 | 预设或自定义网站，打开可见浏览器完成正常登录并保存本机状态 |
| 自动回退 | `auto` 模式在可重试失败时切换备用引擎 |
| 单页抓取 | 获取标题、状态码、最终 URL 和 Markdown 正文 |
| 批量抓取 | 一次最多抓取 10 个 URL，并保持输入顺序 |
| 正文提取 | 优先提取 `main/article`，支持 CSS 选择器 |
| 动态等待 | 支持等待指定 CSS 元素出现 |
| 链接处理 | 保留 Markdown 链接并解析相对链接 |
| SSRF 防护 | 阻止内网、回环、云元数据和非公网 DNS 地址 |
| 浏览器隔离 | 每次请求独立进程和临时浏览器资料目录 |
| 资源限制 | 限制并发、队列、超时、HTML 大小、正文大小和网络流量 |
| Agent 友好结果 | 返回 `success`、`error_code`、`retryable`、`attempts`、`summary` 等字段 |

## 🎯 适用场景

- AI Agent 阅读公开网页并回答问题。
- 研究助手抓取多个公开资料页面。
- 自动化提取文章、产品信息、文档和新闻正文。
- 为其他 Agent 提供统一的网页读取工具。
- 需要浏览器渲染但又不希望每个业务重复维护浏览器代码的项目。

当前项目定位为“公开网页读取服务”，也支持通过本地交互式登录或 Cookie profile 读取你有权限访问的登录页面，
但不是完整的搜索引擎或整站爬虫。暂不支持任意用户脚本、文件下载、PDF 解析、POST 页面和递归整站爬取。

## 🚀 安装和启动

需要 Python 3.11+。建议在项目目录中使用独立虚拟环境。

下面的命令均使用项目目录下的相对路径，不依赖项目被克隆到哪个盘符、目录或用户名目录。

| 平台 | 支持情况 | 推荐启动方式 |
| --- | --- | --- |
| 🪟 Windows | 完整支持 | `scrapling.bat` 或 `scrapling-mcp` |
| 🐧 Linux | 完整支持 | `scrapling-mcp` |
| 🍎 macOS | 完整支持 | `scrapling-mcp` |

### 1️⃣ 从 GitHub 获取项目

```powershell
git clone https://github.com/zouzhengshi/scrapling-mcp.git
cd scrapling-mcp
```

### 2️⃣ Windows：创建环境并安装依赖

```powershell
python -m venv scrapling_env
.\scrapling_env\Scripts\python.exe -m pip install -e .
.\scrapling_env\Scripts\python.exe -m playwright install chromium
.\scrapling_env\Scripts\python.exe -m patchright install chromium
.\scrapling_env\Scripts\python.exe main.py --check
```

### 3️⃣ Linux / macOS：创建环境并安装依赖

```bash
python3 -m venv scrapling_env
./scrapling_env/bin/python -m pip install --upgrade pip
./scrapling_env/bin/python -m pip install -e .
./scrapling_env/bin/python -m playwright install chromium
./scrapling_env/bin/python -m patchright install chromium
./scrapling_env/bin/python main.py --check
```

Linux 服务器首次安装 Playwright 浏览器时，可能还需要：

```bash
./scrapling_env/bin/python -m playwright install --with-deps chromium
```

如果系统没有 `python3` 命令，也可以使用发行版提供的 Python 3.11+ 命令。
直接依赖版本已固定，传递依赖没有完整锁定。

`--check` 只检查依赖版本、浏览器文件和运行环境变量，不访问网络，也不保证网页抓取一定成功。
`--help` 查看命令说明；stdio 服务应由 MCP 客户端启动，不要把管理终端配置成 MCP 服务。

### 4️⃣ Windows 快速启动

安装完成后，先激活虚拟环境：

```powershell
.\scrapling_env\Scripts\Activate.ps1
```

然后可以使用 CLI 命令启动管理终端：

```powershell
scrapling-mcp terminal
```

常用命令也可以直接跟在 CLI 命令后面：

```powershell
scrapling-mcp status
scrapling-mcp guide
scrapling-mcp restart
scrapling-mcp doctor
scrapling-mcp --check
scrapling-mcp --agent-guide
```

也提供短别名 `smcp`，例如 `smcp status`。如果不想激活虚拟环境，仍可以使用项目根目录的
`.\scrapling.bat status`。脚本会根据自身所在位置自动定位项目目录和 `scrapling_env`、`.venv` 或 `venv`，
所以项目放在其他磁盘或目录也不需要修改脚本；也可以用 `SCRAPLING_PYTHON` 指定解释器。
`scrapling-mcp --mcp` 可以手动启动 stdio 服务，但通常应让 MCP 客户端按配置自动启动。
`scrapling-mcp` 在真正的交互终端中仍会自动进入管理终端；在脚本、管道或 CI 中只显示帮助，不会阻塞等待输入。

注意：依赖库本身已经提供了 `scrapling` 命令（用于 Scrapling 库的其他功能），
因此本项目 CLI 使用 `scrapling-mcp`，避免覆盖或混淆已有命令。

### 5️⃣ Linux / macOS 快速启动

激活虚拟环境：

```bash
source scrapling_env/bin/activate
```

启动管理终端：

```bash
scrapling-mcp terminal
```

常用命令：

```bash
scrapling-mcp status
scrapling-mcp cookies
scrapling-mcp profiles
scrapling-mcp tools
scrapling-mcp guide
scrapling-mcp restart
scrapling-mcp --check
scrapling-mcp doctor
```

Linux/macOS 不使用 `scrapling.bat`；如果不想激活虚拟环境，可以直接调用：

```bash
./scrapling_env/bin/scrapling-mcp status
```

### 🖥️ 通过 CLI 调用核心 MCP 工具

Agent 如果拥有终端权限，也可以直接调用全部核心功能。工具命令在交互终端默认输出人类可读文本，
在管道/脚本中默认输出 JSON；也可以显式使用 `--format human` 或 `--format json`：

```powershell
scrapling-mcp scrape "https://example.com"
scrapling-mcp scrape "https://example.com" --format json
scrapling-mcp scrape_batch "https://example.com" "https://www.python.org"
scrapling-mcp scrape "https://example.com" --auth-profile github

scrapling-mcp login bilibili
scrapling-mcp login_status bilibili --finalize
scrapling-mcp login_custom my-site "https://example.com/login" --allowed-domain example.com
scrapling-mcp login_custom_status my-site --finalize
```

`login` 和 `login_custom` 会保持 CLI 进程运行，直到用户完成登录并关闭可见浏览器窗口，
然后自动保存本机登录状态并返回结果。之后抓取登录页面时使用对应的 `--auth-profile`。
CLI 也会把这些核心工具调用记录到调用日志；密码、验证码和 Cookie 原文不会作为命令参数要求输入。

### 🛠️ 本地管理终端

在本机终端运行：

```powershell
.\scrapling_env\Scripts\python.exe main.py --terminal
```

进入后可使用：

| 命令 | 作用 | 示例 |
| --- | --- | --- |
| `status` | 查看程序是否就绪、进程、依赖、浏览器和配置状态 | `status` |
| `cookies` | 查看已有登录配置的名称、域名、Cookie 名称和数量 | `cookies` |
| `profiles` | `cookies` 的快捷别名 | `profiles` |
| `tools` | 查看 6 个 MCP 工具当前是否启用 | `tools` |
| `guide` | 显示可直接复制给其他 AI Agent 的完整使用说明 | `guide` |
| `logs` | 查看运行日志、调用日志的位置和最近记录 | `logs` |
| `logs calls` | 查看最近什么时候、哪个程序调用了什么工具 | `logs calls` |
| `logs runtime` | 查看服务启动、停止和异常记录 | `logs runtime` |
| `restart` | 停止当前项目的 MCP 进程，让 MCP 客户端自动重新拉起服务；不会删除 Cookie 或日志 | `restart` |
| `doctor` | 检查依赖、浏览器和环境变量配置 | `doctor` |
| `tool disable NAME` | 暂停一个工具，后续调用会返回 `TOOL_DISABLED` | `tool disable scrape_batch` |
| `tool enable NAME` | 恢复一个工具 | `tool enable scrape_batch` |
| `help` | 在终端显示每条命令的中文说明 | `help` |
| `exit` | 退出管理终端；`quit`、`q` 也可以 | `exit` |

管理终端在交互模式下会自动显示 MCP 连接配置，并按重要程度使用颜色：
紫色表示连接配置，绿色表示就绪或成功，黄色表示提醒或等待，红色表示错误或停用，青色表示标题和普通信息。
如果当前终端不支持颜色，或需要复制纯文本，可使用 `--no-color`。

其中 `NAME` 是工具名称，可以先输入 `tools` 查看。当前工具名称为：
`login`、`login_status`、`login_custom`、`login_custom_status`、`scrape`、`scrape_batch`。

也支持执行一次后退出：

```powershell
.\scrapling_env\Scripts\python.exe main.py --terminal status
.\scrapling_env\Scripts\python.exe main.py --terminal cookies
.\scrapling_env\Scripts\python.exe main.py --terminal tools
.\scrapling_env\Scripts\python.exe main.py --terminal guide
.\scrapling_env\Scripts\python.exe main.py --terminal restart
.\scrapling_env\Scripts\python.exe main.py --terminal tool disable scrape_batch
```

终端只显示 Cookie 名称和数量，始终隐藏 Cookie 值、localStorage 值和密码。
`restart` 只重启当前项目的 MCP 服务进程树，不会删除登录状态、Cookie 或日志。由于 stdio 服务由 MCP 客户端托管，
终端会停止旧进程并等待客户端自动拉起新进程；只有检测到新进程时才报告重启成功，
否则会明确提示你在客户端中重新连接该 MCP 服务。
工具开关保存在本机用户配置目录，也可通过 `SCRAPLING_CONFIG_FILE` 指定配置文件。
运行日志默认保存到 `%LOCALAPPDATA%\ScraplingMCP\logs\runtime.log`，调用日志默认保存到
`%LOCALAPPDATA%\ScraplingMCP\logs\calls.jsonl`，也可通过 `SCRAPLING_LOG_DIR` 指定日志目录。
调用日志会记录调用时间、调用方名称、调用方 PID、工具名、目标域名、结果和耗时；不会记录父进程完整命令行，
避免把客户端参数中的 Token、Cookie 或其他敏感信息带入日志。
stdio 模式无法自动可靠识别所有客户端，建议在 MCP 客户端配置中设置
`SCRAPLING_CALLER_NAME`，例如 `Codex`、`Claude Desktop` 或你的软件名称。
未设置时服务会尽量记录父进程名称和 PID，但不会记录父进程命令行。
停用工具后，后续调用会立即返回 `TOOL_DISABLED`；由于 MCP 客户端通常会缓存工具列表，
该工具名称可能仍显示在列表中，但不会执行抓取或登录操作。

### 🤖 给其他 AI Agent 的使用说明

MCP 服务初始化时会通过 MCP 的 `instructions` 字段自动发送完整的工具说明，
支持该字段的 Agent 不需要手动配置提示词。需要复制给其他 Agent 时，可以运行：

```powershell
.\scrapling_env\Scripts\python.exe main.py --agent-guide
# 或进入管理终端后输入：guide
```

`guide` 命令可以手动查看连接配置和工具使用说明；正常启动终端时不会自动显示这段详细说明，
以免刷屏。工具使用说明会通过 MCP 初始化时的 `instructions` 自动发送给支持该字段的 Agent。

直接运行 `python main.py` 启动 stdio 服务时，服务不会向 stdout 打印普通文字，
以免破坏 MCP 协议；启动提示会写到 stderr，完整说明由 MCP 初始化自动提供。

## 🔌 MCP 客户端接入

不要把某台电脑生成的绝对路径直接写进开源仓库或复制到另一台电脑。MCP 客户端需要的
`command` 和 `args` 会随安装目录、用户名、虚拟环境和操作系统变化，应在目标电脑上自动生成。

安装完成后，在项目目录执行下面任意一个命令：

```powershell
.\scrapling.bat guide
# 或激活虚拟环境后：
scrapling-mcp guide
```

终端会根据当前运行位置自动生成可复制的 stdio 配置，其中：

- `command` 使用当前实际运行的 Python 解释器；
- `args` 使用当前项目实际的 `main.py`；
- 登录状态、Cookie 和日志仍使用本机用户目录或环境变量指定的目录；
- 仓库文档只保留占位符，不保存任何机器相关绝对路径或登录信息。

生成的配置结构如下，尖括号内容只是占位符，不能原样复制：

```json
{
  "mcpServers": {
    "scrapling": {
      "command": "<本机 Python 解释器路径>",
      "args": ["<本机项目根目录>\\main.py"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "SCRAPLING_CALLER_NAME": "你的 Agent 名称",
        "SCRAPLING_MAX_CONCURRENCY": "3",
        "SCRAPLING_MAX_QUEUE": "24",
        "SCRAPLING_MIN_INTERVAL": "1",
        "SCRAPLING_CACHE_TTL": "30",
        "SCRAPLING_PROXY_MODE": "auto"
      }
    }
  }
}
```

各客户端配置文件位置可能不同，但启动命令相同。服务由客户端启动，修改代码后重启连接。
如果客户端支持直接选择本地命令，也可以选择当前虚拟环境中的 `scrapling-mcp --mcp`，
无需填写项目绝对路径。不要把 `--terminal` 管理终端配置成 MCP 服务。
认证目录可以省略，程序会自动使用本机用户目录；如果使用手动 Cookie profile，必须设置
`SCRAPLING_COOKIE_FILE`，并填写目标电脑上的实际路径。只有需要自定义登录状态目录时，才设置
`SCRAPLING_AUTH_DIR`。这些路径都不应写死在仓库配置中。
可调运行参数会在 `doctor`/`--check` 中校验：`SCRAPLING_MAX_CONCURRENCY` 为 1–8，
`SCRAPLING_MAX_QUEUE` 为 0–128，`SCRAPLING_MIN_INTERVAL` 为 0–60 秒，
`SCRAPLING_CACHE_TTL` 为 0–300 秒，`SCRAPLING_ALLOWED_PORTS` 为逗号分隔的 1–65535 端口。
认证状态文件包含浏览器登录态，属于敏感本机数据；不要提交到 Git，也不要复制给 Agent 或其他人。
无需设置工作目录。当前不提供 HTTP 监听、远程认证或多租户服务。

### 🌐 VPN / 上游代理出口

如果电脑已经连接系统级 VPN，服务会跟随系统路由直接访问，不需要额外配置。
如果希望服务像浏览器一样读取系统的静态代理设置，可以在 MCP 客户端的 `env` 中开启自动模式：

```json
"SCRAPLING_PROXY_MODE": "auto"
```

自动模式的优先级是：显式的 `SCRAPLING_UPSTREAM_PROXY` > 当前系统可读取的静态代理或常见代理环境变量 > 系统直连路由。
因此 Agent 不需要判断某个网站是否需要代理；服务会在每个目标请求发送前按目标协议选择对应出口，并应用系统代理的绕过列表。
系统级 VPN 仍然由操作系统负责路由，服务不会自动启动或关闭 VPN。

出于安全原因，服务只读取静态代理地址，不会执行 PAC/WPAD 脚本；如果终端只配置了 PAC/WPAD，终端 `status` 会提示未执行，
此时请使用代理客户端提供的本地 HTTP/SOCKS5 端口并显式配置。

如果使用 Clash、v2rayN、代理客户端等提供的本地端口，请在 MCP 客户端的 `env` 中设置
`SCRAPLING_UPSTREAM_PROXY`：

```json
"SCRAPLING_UPSTREAM_PROXY": "http://127.0.0.1:7890"
```

也支持 SOCKS5：

```json
"SCRAPLING_UPSTREAM_PROXY": "socks5://127.0.0.1:7891"
```

常见情况下 HTTP 代理端口是 `7890`，SOCKS5 端口是 `7891`，实际端口以你的代理软件为准。
显式配置后所有抓取和交互式登录都会经过该出口；不配置且未开启自动模式时保持直连。服务不会自动启动或关闭 VPN，
也不会把代理密码写入日志。即使使用代理，目标 URL 的公网地址、端口和重定向安全校验仍然有效。
如果代理需要认证，可以使用 `http://用户名:密码@主机:端口`，特殊字符应先进行 URL 编码。
可用终端的 `status` 查看当前是直连、自动代理还是已配置上游代理。

### 🔐 交互式登录（推荐）

不想手动复制 Cookie 时，直接调用 `login` 工具。当前预设网站为：
`bilibili`、`youtube`、`github`、`zhihu`、`weibo`、`xiaohongshu`。

调用：

```json
{
  "site": "youtube",
  "timeout": 300
}
```

服务会打开可见浏览器窗口并立即返回，不会占用 MCP 调用等待几分钟。YouTube/Google 登录默认使用 Patchright 的 Chrome 兼容模式，
优先调用本机已安装的 Google Chrome；请在窗口中像正常访问网站一样完成邮箱、密码、扫码、验证码和二次验证，
完成后调用：

```json
{
  "site": "youtube",
  "finalize": true
}
```

这会保存登录状态并关闭登录窗口。后续抓取只传 profile 名称：

```json
{
  "url": "https://www.youtube.com/",
  "mode": "stealth",
  "auth_profile": "youtube",
  "timeout": 30,
  "max_chars": 5000
}
```

YouTube 登录会跳转到 Google，内置配置已经包含 `youtube.com` 和 `google.com` 两个受控域名，
不需要手动填写 `allowed_domains`。

认证状态包含浏览器 Cookie 和站点存储数据，但不会进入 MCP 参数、工具返回值或 Git 仓库。
`SCRAPLING_AUTH_DIR` 可指定保存目录；不设置时使用当前操作系统的用户数据目录。
重新登录同一网站会覆盖该网站的本机状态。登录状态过期后，再调用一次 `login` 即可更新。
如果 Agent 提前调用 `login_status(finalize=true)`，服务会返回“登录尚未完成”并保留窗口，
不会打断 Google 的邮箱、密码或二次验证跳转；只有检测到有效登录状态后才会关闭窗口。

登录工具会话只用于用户明确授权的账号和网站，不会代替用户输入密码或验证码，也不承诺绕过网站风控。
如果 Google 仍显示“此浏览器或应用可能不安全”，通常是 Google 对当前账号、网络出口或自动化登录会话的风控结果，
不能通过反复点击“重试”解决。此时请在日常 Chrome 中完成登录，再使用手动 Cookie profile；不要把 Cookie 值或密码发给 Agent。

登录浏览器可通过环境变量调整：`SCRAPLING_LOGIN_BROWSER=auto`（默认，优先 Patchright）、
`SCRAPLING_LOGIN_BROWSER=patchright`（强制 Patchright）或 `SCRAPLING_LOGIN_BROWSER=playwright`（兼容回退）。
Patchright 默认使用 `SCRAPLING_LOGIN_CHANNEL=chrome`；只有明确需要时才改成 `chromium`。

### 🧩 自定义网站登录

没有预设的网站可以使用 `login_custom`。传入目标页面或登录页面，以及一个自定义的
`auth_profile` 名称；MCP 会打开可见 Chromium，你在其中手动完成登录，状态只保存在本机。

```json
{
  "auth_profile": "example-account",
  "url": "https://example.com/dashboard",
  "allowed_domains": ["example.com"],
  "timeout": 300
}
```

如果登录页和目标页属于不同域名，`allowed_domains` 必须明确列出所有需要保存登录状态的公网域名，
不支持通配符。完成登录后调用：

```json
{
  "auth_profile": "example-account",
  "finalize": true
}
```

对应工具为 `login_custom_status`。随后抓取时使用 `auth_profile: "example-account"`。
自定义登录只允许 HTTPS；不会把密码、验证码或 Cookie 原文返回给 Agent。通用网站无法可靠判断
登录业务是否成功，因此请在确认登录完成后再调用 `finalize=true`。

### 🍪 手动 Cookie profile

如需读取你有权限访问的登录页面，先在本机创建 Cookie 配置文件，推荐从
`cookie_profiles.example.json` 复制一份为 `cookie_profiles.json`，再填入浏览器导出的 Cookie。
不要把真实 Cookie 文件提交到 Git，也不要通过聊天发送 Cookie 值。

配置文件格式：

```json
{
  "profiles": {
    "example-account": {
      "allowed_domains": ["example.com"],
      "cookies": [
        {
          "name": "session",
          "value": "在本机填写真实值",
          "domain": ".example.com",
          "path": "/",
          "secure": true,
          "httpOnly": true,
          "sameSite": "Lax"
        }
      ]
    }
  }
}
```

然后在 MCP 客户端的 `env` 中设置：

```json
"SCRAPLING_COOKIE_FILE": "<本机 cookie_profiles.json 的实际路径>"
```

这里的路径必须填写当前电脑上的实际路径，不要复制其他电脑的路径；也不要把真实 Cookie 文件提交到仓库。

调用时只传配置名称，不传 Cookie 原文：

```json
{
  "url": "https://example.com/dashboard",
  "mode": "stealth",
  "cookie_profile": "example-account",
  "timeout": 30,
  "max_chars": 5000
}
```

服务只会注入与目标域名匹配的 Cookie，每次抓取使用独立浏览器环境，
不会把 Cookie 返回给 Agent。Cookie 仅适用于读取型 GET/HEAD 请求；如果网站需要登录表单、POST、验证码或二次认证，可能仍然无法抓取。

### 📄 scrape

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| url | 必填 | 完整的公网 HTTP(S) URL，最多8192字符 |
| mode | auto | auto / fast / stealth |
| timeout | 30 | 总抓取预算，0–120秒且必须大于0，不含终止进程后的短暂系统回收时间 |
| max_chars | 50000 | 正文字符上限，1–200000 |
| css_selector | null | 只提取匹配的区域，如 article |
| wait_for | null | 等待 CSS 元素出现，如 #content |
| main_content | true | 优先提取 main/article，过滤常见导航内容 |
| include_links | true | 保留 Markdown 链接，并解析相对链接 |
| cookie_profile | null | 使用服务端本地 Cookie 配置名称，不传递 Cookie 原文 |
| auth_profile | null | 使用 login 或 login_custom 工具保存的本机登录状态名称 |

CSS 参数使用标准 CSS 选择器，不接受 JavaScript、XPath 或 Playwright 专用选择器。
所有参数在引擎层校验；MCP 层同时提供参数范围和结果 JSON Schema。

`auto` 给第一个引擎约一半剩余预算，给隐身引擎保留兜底时间；快速成功就直接返回。
队列等待、DNS、域名限速、浏览器启动和引擎切换都计入同一预算。
`fast` 仅使用 Crawl4AI，`stealth` 仅使用 Scrapling。
隐身模式不保证解决所有反爬挑战；安全拦截、页面过大和选择器错误不会切换或重试，
临时网络错误、HTTP 5xx 和 429 最多在当前引擎内有限重试，并受同一个 timeout 总预算约束。

公开页面的成功结果默认缓存 30 秒，用于减少 Agent 重复查询；带 `cookie_profile` 或 `auth_profile` 的页面默认不缓存。
可通过 `SCRAPLING_CACHE_TTL=0` 关闭缓存，或设置 1–300 秒的缓存时间。

工具返回 `structuredContent` 和内容相同的 JSON 文本，兼容不同客户端。
抓取失败同时设置 MCP `isError=true`，正文保持为空，防止 Agent 把错误页当成正文。

```json
{
  "schema_version": "1.1",
  "success": true,
  "url": "https://example.com:443/",
  "final_url": "https://example.com:443/",
  "engine": "crawl4ai",
  "status_code": 200,
  "title": "Example Domain",
  "markdown": "# Example Domain",
  "elapsed_ms": 1250,
  "truncated": false,
  "error": null,
  "error_code": null,
  "retryable": false,
  "content_is_untrusted": true,
  "attempts": [],
  "summary": {
    "title": "Example Domain",
    "status_code": 200,
    "content_chars": 14,
    "link_count": 0,
    "image_count": 0,
    "paragraph_count": 0
  },
  "metadata": {}
}
```

实际 `attempts` 包含每次引擎尝试、重试序号、耗时和错误码；`summary` 提供标题、状态码、正文字符数、链接、图片和段落数量。
正文截断时 `truncated=true`，
`metadata.original_markdown_length` 保留原字符数；提示文字不占用正文字符额度。
未开始导航的失败结果 `final_url=null`，不会伪造最终地址。

### 📚 scrape_batch

参数为 `urls`、`mode`、`timeout`、`max_chars`、`cookie_profile`、`auth_profile`。
最多10个URL，每页正文最多10000字符；同样共享服务端并发、队列和域名限速。
每个URL的超时包含排队，所以批量较大、预算较小时，部分URL可能在队列中超时。
返回结果顺序与输入一致，包含 `total/succeeded/failed/message/results`。
部分失败保留所有结果，全部失败时 MCP `isError=true`。

### ⚠️ 错误处理

| error_code | 含义 / Agent 处理方式 |
| --- | --- |
| INVALID_ARGUMENT | 修改参数后调用 |
| UNSAFE_URL | 地址或页面网络请求被策略拒绝 |
| DNS_ERROR | DNS失败；可稍后重试 |
| BUSY | 等待队列已满；延迟后重试 |
| TIMEOUT | 总预算或引擎预算耗尽 |
| HTTP_ERROR | 非2xx响应，保留状态码 |
| RATE_LIMITED | HTTP 429；有 Retry-After 时记录在 metadata |
| BLOCKED | 页面仍为反爬挑战 |
| EMPTY_CONTENT | 没有可用正文 |
| SELECTOR_NOT_FOUND | 正文选择器无匹配 |
| CONTENT_TOO_LARGE | 传输或HTML超出限制 |
| DEPENDENCY_ERROR | 引擎或浏览器缺失；运行 --check |
| COOKIE_ERROR | Cookie profile 未配置、格式错误或目标域名不匹配 |
| AUTH_ERROR | 交互式登录状态未配置、已损坏、已过期或目标域名不匹配 |
| ENGINE_ERROR | 浏览器或工作进程失败 |

`retryable` 只是提示，不会触发无限重试。

## 🐍 Python 调用

```python
import asyncio
from src import scrape

async def main():
    result = await scrape(
        "https://example.com",
        mode="auto", timeout=30, max_chars=5000,
        main_content=True, include_links=True,
    )
    print(result.to_dict())

asyncio.run(main())
```

现有 `ScraplingEngine` / `ScrapeResult.engine_used` 接口保留；Python 调用可传 `auth_profile="bilibili"`。
MCP函数返回 MCP 结果对象，Python 调用方应使用 `src.scrape`。

## 🛡️ 安全和资源边界

- 两个浏览器都使用每次尝试独立的、带随机凭据的本机出口代理。
  未配置上游代理时，每次建立目标连接前验证全部 DNS 地址并直接连接已验证的数字IP；
  配置上游代理时仍先做目标域名公网校验，再由已配置的 HTTP/SOCKS5 代理建立目标连接；
  重定向、子资源和弹窗不会因为换了目标而跳过出口检查。
- 拒绝内网、回环、链路本地、云元数据地址、CGNAT、组播、保留地址和
  可嵌入IPv4的部分IPv6过渡地址；拒绝URL凭据、反斜杠和控制字符。
- 默认仅允许80/443端口。部署者可通过 `SCRAPLING_ALLOWED_PORTS=80,443,8080`
  允许额外端口；这不会允许内网IP，也不是Agent工具参数。
- 本机代理和上游代理都不解密 HTTPS，不关闭证书验证；关闭浏览器的本机代理绕过、QUIC及非代理WebRTC UDP。
  浏览器请求额外限制为GET/HEAD，禁用WebSocket；部分依赖POST加载正文的网站可能不可用。
- 每次尝试都有独立进程和临时浏览器资料目录，无共享登录状态；若指定 Cookie profile，
  只在本次任务中注入匹配目标域名的 Cookie；若指定 auth profile，只加载匹配目标域名的本机登录状态。
  Windows 使用 Job Object，POSIX 使用进程组；
  超时或取消会终止工作进程树，并清理临时文件后释放并发槽位。
  相比复用浏览器，这会增加启动耗时。
- 默认3并发、24等待；并发可设1–8、等待可设0–128。同域名导航默认至少间隔1秒，
  `SCRAPLING_MIN_INTERVAL` 可设0–60秒。每次尝试最多20MB代理流量、400万字符HTML、
  32个同时代理连接；这些不是完整的浏览器内存硬上限。
- MCP stdout 仅用于协议，浏览器日志在子进程中隔离；服务日志不记录完整URL、Cookie、
  查询参数或代理凭据。第三方工作日志只存在于临时目录，任务结束后清理。
- 所有网页内容（包括标题和链接）标为不可信数据。该标记不能单独防住提示词注入，
  Agent仍须遵循自己的权限规则。

这些是应用层防护，不是操作系统网络沙箱。公开部署或处理不可信用户时，
仍应在容器/防火墙层限制出站网络和资源；目前没有验证浏览器漏洞、
自建会话逃逸进程组、非标准网络栈等对抗场景。
支持预设和自定义网站的本地交互式登录、命名 Cookie profile 和 HTTP/SOCKS5 上游代理；尚不支持任意用户脚本、文件下载、PDF解析和递归整站爬取。
没有自动处理robots.txt；使用者需遵守目标网站的访问规则。

## ✅ 验证

```powershell
.\scrapling_env\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scrapling_env\Scripts\python.exe -m unittest discover -s tests -v
```

默认测试使用模拟引擎和本机套接字，不依赖外网；包含真实stdio MCP通信和子进程回收测试。
仓库中的 GitHub Actions 会在 Windows、Linux 和 macOS 上自动运行这组测试，避免跨平台改动只在开发机上通过。
浏览器安装后可运行更慢的、本机网页集成测试：

```powershell
$env:SCRAPLING_BROWSER_TESTS="1"
.\scrapling_env\Scripts\python.exe -m unittest discover -s tests -p test_browsers.py -v
```

浏览器测试通过专用测试代理将 fixture.test 映射到本机受控网页；
生产代码没有此放行开关。测试用重定向、子资源、弹窗访问本机目标，
验证该目标实际收到零请求，同时验证动态CSS等待和超时后再次抓取。

联网冒烟：`.\scrapling_env\Scripts\python.exe test.py`。
它需要公网可达，不能替代回归测试。

实现参考：[MCP结构化返回与错误](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、
[Chromium代理绕过规则](https://chromium.googlesource.com/chromium/src/+/main/net/docs/proxy.md)、
[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
