"""One browser attempt per isolated process. stdin is the process-owner barrier."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
import sys

from src.content import MAX_HTML_CHARS, make_result
from src.models import failure
from src.security import normalize_url, UnsafeUrlError

BROWSER_FLAGS = [
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
]


async def setup_page(page, context=None, cookies=None, **kwargs):
    context = context or page.context

    if cookies:
        await context.add_cookies(cookies)

    async def route_request(route):
        # The proxy is the SSRF boundary; this is additional protocol/method filtering.
        request = route.request
        if request.method not in {"GET", "HEAD"} or not request.url.startswith(("https://", "http://")):
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", route_request)
    if hasattr(context, "route_web_socket"):
        async def close_socket(socket):
            await socket.close()
        await context.route_web_socket("**/*", close_socket)
    return page


async def crawl(payload):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    config = BrowserConfig(headless=True, verbose=False, proxy_config=payload["proxy"],
                           extra_args=BROWSER_FLAGS, ignore_https_errors=False, accept_downloads=False,
                           cookies=payload.get("cookies", []))
    options = payload["options"]
    run = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED, page_timeout=max(1, int(payload["timeout"] * 1000)),
        wait_for=("css:" + options["wait_for"]) if options.get("wait_for") else None,
        verbose=False, max_retries=0, check_robots_txt=False,
    )
    async with AsyncWebCrawler(config=config, base_directory=payload["work_dir"]) as crawler:
        async def configure_page(page, context=None, **kwargs):
            return await setup_page(page, context=context, **kwargs)
        crawler.crawler_strategy.set_hook("on_page_context_created", configure_page)
        result = await crawler.arun(url=payload["url"], config=run)
        raw = result.html or ""
        if not result.success and not raw:
            return failure(payload["url"], "crawl4ai", "ENGINE_ERROR", "Crawl4AI 抓取失败", True)
        final = result.redirected_url or result.url or payload["url"]
        normalize_url(final, payload["allowed_ports"])
        status = result.redirected_status_code or result.status_code
        return make_result(payload["url"], "crawl4ai", raw, status, final, options, result.response_headers)


async def stealth(payload):
    from scrapling.fetchers import AsyncStealthySession

    options = payload["options"]
    async with AsyncStealthySession(
        headless=True, proxy=payload["proxy"], extra_flags=BROWSER_FLAGS,
        user_data_dir=os.path.join(payload["work_dir"], "profile"),
        block_webrtc=True, retries=1, google_search=False,
        additional_args={"service_workers": "block", "accept_downloads": False, "ignore_https_errors": False},
    ) as session:
        # Security setup occurs outside Scrapling's exception-swallowing page_setup hook.
        await setup_page(None, context=session.context, cookies=payload.get("cookies", []))
        response = await session.fetch(
            payload["url"], timeout=max(1, int(payload["timeout"] * 1000)),
            # Challenge pages are classified by the shared extractor. Automatic
            # solving can wait indefinitely on ordinary pages when service
            # workers/network-idle are intentionally restricted.
            solve_cloudflare=False, wait_selector=options.get("wait_for"),
            wait_selector_state="attached",
        )
        if len(response.body) > MAX_HTML_CHARS * 4:
            return failure(payload["url"], "scrapling", "CONTENT_TOO_LARGE", "页面响应过大")
        raw = response.body.decode(response.encoding or "utf-8", errors="replace")
        final = normalize_url(str(response.url), payload["allowed_ports"])
        return make_result(payload["url"], "scrapling", raw, response.status, final, options, response.headers)


async def run(payload):
    try:
        return await (crawl(payload) if payload["engine"] == "crawl4ai" else stealth(payload))
    except UnsafeUrlError:
        return failure(payload["url"], payload["engine"], "UNSAFE_URL", "页面导航到了不允许的地址")
    except ImportError:
        return failure(payload["url"], payload["engine"], "DEPENDENCY_ERROR", "缺少引擎依赖，请运行 --check")
    except Exception as exc:
        text = str(exc).lower()
        if "executable doesn't exist" in text or "browser was not found" in text:
            return failure(payload["url"], payload["engine"], "DEPENDENCY_ERROR", "未安装浏览器，请按 README 安装")
        code = "TIMEOUT" if "timeout" in type(exc).__name__.lower() or "timeout" in text else "ENGINE_ERROR"
        # Never return third-party exception text: it may contain proxy credentials or query secrets.
        return failure(payload["url"], payload["engine"], code, "浏览器请求失败或等待超时", True)


def main():
    # Redirect the OS descriptor too: native libraries may write directly to stdout.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    payload = json.loads(sys.stdin.buffer.readline(65536))
    result = asyncio.run(run(payload))
    output.write(json.dumps(asdict(result), ensure_ascii=False))
    output.flush()
    output.close()


if __name__ == "__main__":
    main()
