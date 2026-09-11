# Scrapling MCP

面向 AI Agent 的安全通用网页抓取 MCP 服务，基于 Crawl4AI 和 Scrapling，
通过 **stdio MCP** 提供单页、批量抓取和本地交互式登录，也可以直接从 Python 异步调用。

## 项目是什么

Scrapling MCP 是一个让 AI Agent 能够安全调用网页抓取能力的通用工具服务。
Agent 不需要为每个网站单独编写 requests、Playwright 或网页解析代码，
只需要调用统一的 `scrape` 或 `scrape_batch` 工具，就可以获得标题、状态码、最终地址、
Markdown 正文、链接和可判断的错误信息。

项目使用两套抓取引擎：

- Crawl4AI：适合普通网页和快速抓取。
- Scrapling：适合需要浏览器渲染和更强隐身能力的网页。

`auto` 模式会优先使用 Crawl4AI，在可重试的失败场景下切换 Scrapling。
隐身模式不承诺绕过所有反爬挑战，挑战页会被识别并以结构化结果返回。

## 解决什么问题

AI Agent 在访问网页时通常会遇到以下问题：

1. 不同网站的抓取方式不统一，需要重复编写浏览器、请求和解析逻辑。
2. 很多页面依赖 JavaScript 渲染，普通 HTTP 请求拿不到正文。
3. 网页可能发生重定向、加载子资源或打开弹窗，容易带来 SSRF 和内网访问风险。
4. 浏览器任务超时后可能残留进程、临时文件或占满并发资源。
5. 抓取失败时只返回一段异常文本，Agent 难以判断是超时、限流、被拦截还是参数错误。
6. 网页正文可能包含提示词注入，不能被 Agent 误认为系统指令。

Scrapling MCP 将这些能力统一封装为 MCP 工具，并提供公网 URL 校验、DNS 防护、出口代理、
并发队列、超时控制、进程清理和结构化错误，让 Agent 可以更稳定地读取公开网页内容。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| MCP 接入 | 通过 stdio 接入支持 MCP 的 AI Agent |
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
| Agent 友好结果 | 返回 `success`、`error_code`、`retryable`、`attempts` 等字段 |

## 适用场景

- AI Agent 阅读公开网页并回答问题。
- 研究助手抓取多个公开资料页面。
- 自动化提取文章、产品信息、文档和新闻正文。
- 为其他 Agent 提供统一的网页读取工具。
- 需要浏览器渲染但又不希望每个业务重复维护浏览器代码的项目。

当前项目定位为“公开网页读取服务”，也支持通过本地交互式登录或 Cookie profile 读取你有权限访问的登录页面，
但不是完整的搜索引擎或整站爬虫。暂不支持任意用户脚本、文件下载、PDF 解析、POST 页面和递归整站爬取。

## 安装和启动

需要 Python 3.11+。建议使用独立虚拟环境。

```powershell
python -m venv scrapling_env
.\scrapling_env\Scripts\python.exe -m pip install -r requirements.txt
.\scrapling_env\Scripts\python.exe -m playwright install chromium
.\scrapling_env\Scripts\python.exe -m patchright install chromium
.\scrapling_env\Scripts\python.exe main.py --check
```

Linux/macOS 将解释器路径换成 `scrapling_env/bin/python`；
Linux 首次安装浏览器可能还需要 `python -m playwright install --with-deps chromium`。
直接依赖版本已固定，传递依赖没有完整锁定。

`--check` 只检查依赖版本和浏览器文件是否存在，不访问网络，也不保证网页抓取一定成功。
`--help` 查看命令说明；不带参数启动 stdio 服务，等待 MCP 客户端消息。

## MCP 客户端接入

使用支持 stdio MCP 的客户端配置：

```json
{
  "mcpServers": {
    "scrapling": {
      "command": "D:\\Scrapling\\scrapling_env\\Scripts\\python.exe",
      "args": ["D:\\Scrapling\\main.py"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "SCRAPLING_MAX_CONCURRENCY": "3",
        "SCRAPLING_MAX_QUEUE": "24",
        "SCRAPLING_MIN_INTERVAL": "1",
        "SCRAPLING_COOKIE_FILE": "D:\\Scrapling\\cookie_profiles.json",
        "SCRAPLING_AUTH_DIR": "D:\\Scrapling\\auth_profiles"
      }
    }
  }
}
```

各客户端配置文件位置可能不同，但启动命令相同。服务由客户端启动，修改代码后重启连接。
无需设置工作目录。当前不提供 HTTP 监听、远程认证或多租户服务。

### 交互式登录（推荐）

不想手动复制 Cookie 时，直接调用 `login` 工具。当前预设网站为：
`bilibili`、`github`、`zhihu`、`weibo`、`xiaohongshu`。

调用：

```json
{
  "site": "bilibili",
  "timeout": 300
}
```

服务会打开可见 Chromium 窗口并立即返回，不会占用 MCP 调用等待几分钟。请在窗口中像正常访问网站一样完成密码、扫码、验证码和二次验证，
完成后调用：

```json
{
  "site": "bilibili",
  "finalize": true
}
```

这会保存登录状态并关闭登录窗口。后续抓取只传 profile 名称：

```json
{
  "url": "https://www.bilibili.com/",
  "mode": "stealth",
  "auth_profile": "bilibili",
  "timeout": 30,
  "max_chars": 5000
}
```

认证状态包含浏览器 Cookie 和站点存储数据，但不会进入 MCP 参数、工具返回值或 Git 仓库。
`SCRAPLING_AUTH_DIR` 可指定保存目录；不设置时使用当前操作系统的用户数据目录。
重新登录同一网站会覆盖该网站的本机状态。登录状态过期后，再调用一次 `login` 即可更新。

登录工具会话只用于用户明确授权的账号和网站，不会代替用户输入密码或验证码，也不承诺绕过网站风控。

### 自定义网站登录

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

### 手动 Cookie profile

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
"SCRAPLING_COOKIE_FILE": "D:\\Scrapling\\cookie_profiles.json"
```

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

### scrape

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
隐身模式不保证解决所有反爬挑战；HTTP错误、429、安全拦截、页面过大不会自动再试另一个引擎。

工具返回 `structuredContent` 和内容相同的 JSON 文本，兼容不同客户端。
抓取失败同时设置 MCP `isError=true`，正文保持为空，防止 Agent 把错误页当成正文。

```json
{
  "schema_version": "1.0",
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
  "metadata": {}
}
```

实际 `attempts` 包含每次引擎尝试、耗时和错误码。正文截断时 `truncated=true`，
`metadata.original_markdown_length` 保留原字符数；提示文字不占用正文字符额度。
未开始导航的失败结果 `final_url=null`，不会伪造最终地址。

### scrape_batch

参数为 `urls`、`mode`、`timeout`、`max_chars`、`cookie_profile`、`auth_profile`。
最多10个URL，每页正文最多10000字符；同样共享服务端并发、队列和域名限速。
每个URL的超时包含排队，所以批量较大、预算较小时，部分URL可能在队列中超时。
返回结果顺序与输入一致，包含 `total/succeeded/failed/results`。
部分失败保留所有结果，全部失败时 MCP `isError=true`。

### 错误处理

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

## Python 调用

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

## 安全和资源边界

- 两个浏览器都使用每次尝试独立的、带随机凭据的本机出口代理。
  每次建立目标连接前验证全部 DNS 地址，直接连接已验证的数字IP；
  重定向、子资源和弹窗不会因为换了目标而跳过出口检查。
- 拒绝内网、回环、链路本地、云元数据地址、CGNAT、组播、保留地址和
  可嵌入IPv4的部分IPv6过渡地址；拒绝URL凭据、反斜杠和控制字符。
- 默认仅允许80/443端口。部署者可通过 `SCRAPLING_ALLOWED_PORTS=80,443,8080`
  允许额外端口；这不会允许内网IP，也不是Agent工具参数。
- 代理不解密HTTPS，不关闭证书验证；关闭浏览器的本机代理绕过、QUIC及非代理WebRTC UDP。
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
支持预设和自定义网站的本地交互式登录，以及命名 Cookie profile；尚不支持任意用户脚本、文件下载、PDF解析、递归整站爬取和上游代理。
没有自动处理robots.txt；使用者需遵守目标网站的访问规则。

## 验证

```powershell
.\scrapling_env\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scrapling_env\Scripts\python.exe -m unittest discover -s tests -v
```

默认测试使用模拟引擎和本机套接字，不依赖外网；包含真实stdio MCP通信和子进程回收测试。
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
