# 🕷️ Scrapling MCP

## 项目文档

- [更新日志](https://github.com/zouzhengshi/scrapling-mcp/blob/main/CHANGELOG.md)
- [安全说明](https://github.com/zouzhengshi/scrapling-mcp/blob/main/SECURITY.md)
- [麻省理工学院许可](https://github.com/zouzhengshi/scrapling-mcp/blob/main/LICENSE)

面向 AI Agent 的安全、通用网页抓取 MCP 服务。

它把网页抓取、动态页面渲染和用户授权登录统一封装成 MCP 工具，
让支持 MCP 的 AI Agent 可以直接读取网页内容，不需要为每个网站重复编写爬虫代码。

## ✨ 核心功能

- 🕷️ **网页抓取**：读取公开网页，返回标题、正文、链接、状态码和摘要。
- 📚 **批量抓取**：一次抓取多个网页，并保持结果顺序。
- 🌐 **双引擎**：结合 Crawl4AI 和 Scrapling，支持 JavaScript 渲染和动态页面。
- 🔐 **登录抓取**：支持 Bilibili、YouTube、GitHub 等预设网站，也支持自定义 HTTPS 网站。
- 🍪 **本机登录态**：密码、验证码和 Cookie 不发送给 Agent，只保存在用户本机。
- 🛡️ **安全防护**：SSRF 防护、DNS 公网校验、域名 Cookie 隔离、资源限制和进程清理。
- 🌍 **代理支持**：支持系统代理、HTTP 代理和 SOCKS5 代理，适合需要 VPN 的网站。
- 🖥️ **CLI 管理**：查看状态、登录配置、工具开关、日志和 MCP 服务状态。
- 📝 **结构化结果**：返回成功状态、错误码、重试建议、摘要和截断信息，方便 Agent 理解。

## 🧩 提供的 MCP 工具

| 工具 | 用途 |
| --- | --- |
| `scrape` | 抓取一个网页 |
| `scrape_batch` | 批量抓取多个网页 |
| `login` | 打开预设网站的登录窗口 |
| `login_status` | 查询或保存预设网站登录状态 |
| `login_custom` | 打开自定义网站的登录窗口 |
| `login_custom_status` | 查询或保存自定义网站登录状态 |

## 🚀 Windows 安装

需要 Python 3.11 或更高版本。

### 1. 下载项目

```powershell
git clone https://github.com/zouzhengshi/scrapling-mcp.git
cd scrapling-mcp
```

### 2. 创建环境并安装依赖

```powershell
python -m venv scrapling_env
.\scrapling_env\Scripts\python.exe -m pip install --upgrade pip
.\scrapling_env\Scripts\python.exe -m pip install -e .
.\scrapling_env\Scripts\python.exe -m playwright install chromium
.\scrapling_env\Scripts\python.exe -m patchright install chromium
.\scrapling_env\Scripts\Activate.ps1
```

### 3. 检查安装

```powershell
.\scrapling_env\Scripts\scrapling-mcp.exe --check
```

## 🔌 连接 AI Agent

运行下面的命令生成当前电脑可直接使用的连接配置：

```powershell
.\scrapling_env\Scripts\scrapling-mcp.exe guide
```

把输出的 JSON 添加到支持 stdio MCP 的 Agent 客户端中。连接命令必须使用你电脑上的实际路径，
不要直接复制其他电脑的路径。

MCP 服务通常由 Agent 客户端自动启动，不要把管理终端当作 MCP 服务连接。

## 🔐 登录后抓取

### 预设网站

以 Bilibili 为例：

```powershell
scrapling-mcp login bilibili
```

在弹出的浏览器中完成登录并关闭窗口，然后使用：

```powershell
scrapling-mcp login_status bilibili --finalize
scrapling-mcp scrape "https://www.bilibili.com/" --auth-profile bilibili
```

YouTube、GitHub、知乎、微博和小红书的用法相同，把网站名称替换为对应的预设名称即可。

### 没有预设的网站

```powershell
scrapling-mcp login_custom my-site "https://example.com/login" --allowed-domain example.com
scrapling-mcp login_custom_status my-site --finalize
scrapling-mcp scrape "https://example.com/account" --auth-profile my-site
```

登录过程中不要把密码、验证码或 Cookie 原文发送给 Agent。
目标网站的登录策略、验证码和反爬机制可能导致登录失败，工具不会承诺绕过这些限制。

## 🖥️ CLI 命令

```powershell
scrapling-mcp terminal    # 启动管理终端
scrapling-mcp status      # 查看运行状态
scrapling-mcp cookies     # 查看脱敏登录配置
scrapling-mcp tools       # 查看工具开关
scrapling-mcp logs calls  # 查看工具调用日志
scrapling-mcp restart     # 请求 MCP 客户端重新启动服务
scrapling-mcp doctor      # 检查依赖和配置
```

Agent 也可以直接通过 CLI 使用全部核心功能：

```powershell
scrapling-mcp scrape "https://example.com"
scrapling-mcp scrape_batch "https://example.com" "https://www.python.org"
scrapling-mcp login bilibili
scrapling-mcp login_status bilibili --finalize
```

## ⚙️ 代理配置

如果目标网站需要代理或 VPN，可以配置本机代理软件的 HTTP 或 SOCKS5 端口：

```powershell
$env:SCRAPLING_UPSTREAM_PROXY="http://127.0.0.1:7890"
```

## 🛡️ 安全边界

- 默认只允许访问公网 HTTP/HTTPS 地址，阻止回环、内网和云元数据地址。
- Cookie 和浏览器登录状态按域名隔离，不会发送到未授权域名。
- 每次浏览器任务使用独立进程和临时目录，超时或取消后会清理进程树。
- 日志只记录脱敏的调用信息，不记录密码、Cookie 值或完整敏感参数。
- 网页内容是不可信外部数据，Agent 不应把网页中的指令当成系统指令。
- 只抓取你有权访问的内容，并遵守目标网站的服务条款和适用法律。

## ⚠️ 当前限制

本项目定位是网页读取和用户授权登录抓取工具，目前不支持：

- 递归整站爬取；
- 文件下载；
- PDF 解析；
- POST 页面和任意用户脚本；
- 保证绕过验证码或反爬挑战。
