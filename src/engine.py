"""Bounded scheduler and hard-deadline orchestration for both browser engines."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import math
import os
import time
from urllib.parse import urlsplit
import weakref

from src.content import _truncate_markdown
from src.cookies import CookieProfileError, load_cookies
from src.egress import EgressProxy
from src.models import ScrapeResult, failure
from src.processes import run_worker
from src.security import DEFAULT_PORTS, DnsError, UnsafeUrlError, normalize_url, validate_url

logger = logging.getLogger(__name__)


class ScraplingEngine:
    MAX_TIMEOUT_SECONDS = 120.0
    MAX_OUTPUT_CHARS = 200000

    def __init__(self, max_concurrency=3, default_max_chars=50000, *,
                 max_queue=24, min_interval=1.0, allowed_ports=DEFAULT_PORTS, cookie_file=None):
        for name, value, low, high in (
            ("max_concurrency", max_concurrency, 1, 8),
            ("default_max_chars", default_max_chars, 1, self.MAX_OUTPUT_CHARS),
            ("max_queue", max_queue, 0, 128),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须是 {low}–{high} 之间的整数")
        if isinstance(min_interval, bool) or not isinstance(min_interval, (int, float)) or not math.isfinite(min_interval) or not 0 <= min_interval <= 60:
            raise ValueError("min_interval 必须在 0–60 秒之间")
        if not allowed_ports or any(type(p) is not int or not 1 <= p <= 65535 for p in allowed_ports):
            raise ValueError("allowed_ports 必须是有效端口列表")
        self._capacity = max_concurrency + max_queue
        self._pending = 0
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._default_max_chars = default_max_chars
        self._min_interval = min_interval
        self._host_next = OrderedDict()
        self.allowed_ports = tuple(allowed_ports)
        self.cookie_file = cookie_file or os.environ.get("SCRAPLING_COOKIE_FILE")

    def _validate_options(self, mode, timeout, max_chars, css_selector, wait_for, main_content, include_links,
                          cookie_profile):
        if mode not in ("auto", "fast", "stealth"):
            raise ValueError("mode 必须是 auto、fast 或 stealth")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= self.MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout 必须大于 0 且不超过 120 秒")
        if type(max_chars) is not int or not 1 <= max_chars <= self.MAX_OUTPUT_CHARS:
            raise ValueError("max_chars 必须是 1–200000 的整数")
        if type(main_content) is not bool or type(include_links) is not bool:
            raise ValueError("main_content 和 include_links 必须是布尔值")
        if cookie_profile is not None and (not isinstance(cookie_profile, str) or not cookie_profile.strip() or len(cookie_profile) > 64):
            raise ValueError("cookie_profile 必须是 1–64 字符的配置名称")
        for selector in (css_selector, wait_for):
            if selector is not None:
                if not isinstance(selector, str) or not selector.strip() or len(selector) > 1000:
                    raise ValueError("CSS 选择器必须是 1–1000 字符")
                from cssselect import GenericTranslator, SelectorError
                try:
                    GenericTranslator().css_to_xpath(selector)
                except SelectorError as exc:
                    raise ValueError("仅支持合法的 CSS 选择器（不接受 JavaScript）") from exc

    async def _throttle(self, host):
        now = time.perf_counter()
        due = max(now, self._host_next.get(host, now))
        self._host_next[host] = due + self._min_interval
        self._host_next.move_to_end(host)
        while len(self._host_next) > 1024:
            self._host_next.popitem(last=False)
        if due > now:
            await asyncio.sleep(due - now)

    async def scrape(self, url: str, mode="auto", timeout=30.0, max_chars=None, *,
                     css_selector=None, wait_for=None, main_content=True, include_links=True,
                     cookie_profile=None) -> ScrapeResult:
        """One total budget for queue, DNS, host pacing, startup and all attempts.

        Process termination/reaping happens before the concurrency slot is released.
        The short OS cleanup interval may extend wall time beyond the fetch deadline.
        """
        start = time.perf_counter()
        limit = self._default_max_chars if max_chars is None else max_chars
        safe_url = url if isinstance(url, str) else ""
        attempts = []
        current_engine = "validation"
        cookies = []
        try:
            self._validate_options(mode, timeout, limit, css_selector, wait_for, main_content, include_links,
                                   cookie_profile)
            safe_url = normalize_url(url, self.allowed_ports)
            cookies = load_cookies(cookie_profile, self.cookie_file, safe_url)
        except CookieProfileError as exc:
            return self._finish(failure(safe_url, "validation", "COOKIE_ERROR", str(exc)), start, [], limit=0)
        except ValueError as exc:
            code = "UNSAFE_URL" if isinstance(exc, UnsafeUrlError) else "INVALID_ARGUMENT"
            return self._finish(failure(safe_url, "validation", code, str(exc)), start, [], limit=0)
        if self._pending >= self._capacity:
            return self._finish(failure(safe_url, "scheduler", "BUSY", "抓取队列已满，请稍后重试", True), start, [], limit=0)
        self._pending += 1
        deadline = start + timeout
        options = dict(max_chars=limit, css_selector=css_selector, wait_for=wait_for,
                       main_content=main_content, include_links=include_links)
        try:
            async with asyncio.timeout_at(asyncio.get_running_loop().time() + max(0, deadline - time.perf_counter())):
                current_engine = "scheduler"
                async with self._semaphore:
                    current_engine = "validation"
                    safe_url = await validate_url(safe_url, self.allowed_ports)
                    engines = ["crawl4ai", "scrapling"] if mode == "auto" else ["crawl4ai" if mode == "fast" else "scrapling"]
                    for index, current_engine in enumerate(engines):
                        attempt_start = time.perf_counter()
                        await self._throttle(urlsplit(safe_url).hostname)
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            raise TimeoutError
                        # Reserve half the remaining budget for stealth in auto mode.
                        budget = remaining / 2 if mode == "auto" and index == 0 else remaining
                        try:
                            async with asyncio.timeout(budget):
                                result = await self._attempt(safe_url, current_engine, budget, options, cookies)
                        except TimeoutError:
                            result = failure(safe_url, current_engine, "TIMEOUT", "引擎抓取预算已耗尽", True)
                        attempts.append({"engine": current_engine, "success": result.success,
                                         "error_code": result.error_code,
                                         "elapsed_ms": round((time.perf_counter() - attempt_start) * 1000, 2)})
                        if result.success or result.error_code in {
                            "UNSAFE_URL", "INVALID_ARGUMENT", "HTTP_ERROR", "RATE_LIMITED",
                            "CONTENT_TOO_LARGE", "SELECTOR_NOT_FOUND", "DNS_ERROR",
                        }:
                            break
                    return self._finish(result, start, attempts, limit)
        except TimeoutError:
            result = failure(safe_url, current_engine, "TIMEOUT", "总抓取预算已耗尽（含排队、DNS 和引擎切换）", True)
            if current_engine in {"crawl4ai", "scrapling"} and (not attempts or attempts[-1]["engine"] != current_engine):
                attempts.append({"engine": current_engine, "success": False, "error_code": "TIMEOUT",
                                 "elapsed_ms": round((time.perf_counter() - attempt_start) * 1000, 2)})
        except UnsafeUrlError as exc:
            result = failure(safe_url, "validation", "UNSAFE_URL", str(exc))
        except DnsError as exc:
            result = failure(safe_url, "validation", "DNS_ERROR", str(exc), True)
        except Exception:
            # Keep URLs, request headers, raw browser traces and proxy credentials out of logs.
            logger.error("scrape internal failure engine=%s", current_engine)
            result = failure(safe_url, current_engine, "ENGINE_ERROR", "抓取服务内部错误", True)
        finally:
            self._pending -= 1
        return self._finish(result, start, attempts, limit)

    async def _attempt(self, url, engine, timeout, options, cookies=None):
        async with EgressProxy(self.allowed_ports) as proxy:
            try:
                raw = await run_worker(dict(url=url, engine=engine, timeout=timeout,
                                            options=options, proxy=proxy.browser_proxy,
                                            cookies=cookies or [],
                                            allowed_ports=self.allowed_ports))
                result = ScrapeResult(**raw)
            except (OSError, ValueError, RuntimeError, TypeError):
                result = failure(url, engine, "ENGINE_ERROR", "浏览器工作进程异常退出", True)
            if proxy.limit_exceeded:
                result = failure(url, engine, "CONTENT_TOO_LARGE", "页面网络传输超过 20 MB 限制")
            elif proxy.blocked_requests and not result.success:
                result = failure(url, engine, "UNSAFE_URL", "已阻止页面访问非公网目标")
            result.metadata["blocked_requests"] = proxy.blocked_requests
            result.metadata["network_bytes"] = proxy.bytes_transferred
            return result

    def _finish(self, result, start, attempts, limit):
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        result.attempts = attempts
        if limit and result.markdown:
            markdown, truncated, original = _truncate_markdown(str(result.markdown), limit)
            result.markdown = markdown
            result.truncated = result.truncated or truncated
            result.metadata.setdefault("original_markdown_length", original)
            result.metadata["truncated"] = result.truncated
        if not result.success:
            result.markdown = ""
        logger.info("scrape engine=%s success=%s code=%s elapsed_ms=%.0f",
                    result.engine_used, result.success, result.error_code, result.elapsed_ms)
        return result


_engines = weakref.WeakKeyDictionary()


async def scrape(url: str, mode="auto", timeout=30.0, max_chars=None, **options) -> ScrapeResult:
    """Convenience API, usable across separate asyncio.run calls."""
    loop = asyncio.get_running_loop()
    if loop not in _engines:
        _engines[loop] = ScraplingEngine()
    return await _engines[loop].scrape(url, mode, timeout, max_chars, **options)
