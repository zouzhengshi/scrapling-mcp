# Scrapling MCP

面向 AI Agent 的公网网页抓取工具。通过 **stdio MCP** 提供单页和批量抓取，
也可以直接从 Python 异步调用。支持 Windows、Linux、macOS；本次实际验证环境为 Windows / Python 3.12。

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
        "SCRAPLING_MIN_INTERVAL": "1"
      }
    }
  }
}
```

各客户端配置文件位置可能不同，但启动命令相同。服务由客户端启动，修改代码后重启连接。
无需设置工作目录。当前不提供 HTTP 监听、远程认证或多租户服务。

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

参数为 `urls`、`mode`、`timeout`、`max_chars`。
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

现有 `ScraplingEngine` / `ScrapeResult.engine_used` 接口保留。
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
- 每次尝试都有独立进程和临时浏览器资料目录，无共享登录状态。Windows 使用 Job Object，
  POSIX 使用进程组；超时或取消会终止工作进程树，并清理临时文件后释放并发槽位。
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
尚不支持登录Cookie、任意用户脚本、文件下载、PDF解析、递归整站爬取和上游代理。
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
