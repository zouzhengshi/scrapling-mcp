# -*- coding: utf-8 -*-
"""Deterministic orchestration tests; browser behavior is covered separately."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.engine import ScraplingEngine
from src.models import ScrapeResult
from src.security import UnsafeUrlError, normalize_url


class SecurityTests(unittest.TestCase):
    def test_rejects_private_and_non_http_urls(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("http://127.0.0.1:8000")
        with self.assertRaises(UnsafeUrlError):
            normalize_url("ftp://example.com/file")

    def test_normalizes_public_url(self):
        self.assertEqual(normalize_url("https://EXAMPLE.com/a#fragment"), "https://example.com:443/a")


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def _result(self, engine, success, code=None, markdown=""):
        return ScrapeResult("https://example.com/", engine, markdown, success, error_code=code)

    async def test_auto_fallback_records_attempts_and_truncates(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0)
        attempts = AsyncMock(side_effect=[
            self._result("crawl4ai", False, "BLOCKED"),
            self._result("scrapling", True, markdown="x" * 100),
        ])
        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch.object(engine, "_attempt", attempts):
            result = await engine.scrape("https://example.com", "auto", 2, 20)
        self.assertTrue(result.success)
        self.assertEqual(result.engine_used, "scrapling")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.markdown), 20)
        self.assertEqual([a["engine"] for a in result.attempts], ["crawl4ai", "scrapling"])

    async def test_fast_does_not_fallback(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0)
        attempt = AsyncMock(return_value=self._result("crawl4ai", False, "BLOCKED"))
        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch.object(engine, "_attempt", attempt):
            result = await engine.scrape("https://example.com", "fast", 2)
        attempt.assert_awaited_once()
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "BLOCKED")

    async def test_retryable_engine_failure_is_retried_within_budget(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0)
        first = self._result("crawl4ai", False, "ENGINE_ERROR")
        first.retryable = True
        attempt = AsyncMock(side_effect=[first, self._result("crawl4ai", True, markdown="ok")])
        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch.object(engine, "_attempt", attempt):
            result = await engine.scrape("https://example.com", "fast", 3)
        self.assertTrue(result.success)
        self.assertEqual(attempt.await_count, 2)
        self.assertEqual([item["retry"] for item in result.attempts], [0, 1])

    async def test_public_success_is_cached_but_authenticated_content_is_not(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0, cache_ttl=30)
        attempt = AsyncMock(return_value=self._result("crawl4ai", True, markdown="cached"))
        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch("src.engine.load_auth_state", return_value=None), \
             patch.object(engine, "_attempt", attempt):
            first = await engine.scrape("https://example.com", "fast", 3)
            second = await engine.scrape("https://example.com", "fast", 3)
            private = await engine.scrape("https://example.com", "fast", 3, auth_profile="github")
        self.assertFalse(first.metadata.get("cache_hit", False))
        self.assertTrue(second.metadata["cache_hit"])
        self.assertEqual(attempt.await_count, 2)
        self.assertFalse(private.metadata.get("cache_hit", False))

    async def test_queue_is_bounded(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0)
        engine._pending = engine._capacity
        result = await engine.scrape("https://example.com", "fast", 2)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "BUSY")

    async def test_timeout_releases_slot_for_following_request(self):
        engine = ScraplingEngine(max_concurrency=1, max_queue=0, min_interval=0)

        async def stalled(*args):
            await asyncio.Event().wait()

        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch.object(engine, "_attempt", new=stalled):
            result = await engine.scrape("https://example.com", "fast", 0.05)
        self.assertEqual(result.error_code, "TIMEOUT")
        self.assertEqual(engine._pending, 0)

        with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com/")), \
             patch.object(engine, "_attempt", new=AsyncMock(return_value=self._result("crawl4ai", True, "ok"))):
            following = await engine.scrape("https://example.com", "fast", 2)
        self.assertTrue(following.success)

    async def test_invalid_options_do_not_start_work(self):
        engine = ScraplingEngine()
        with patch.object(engine, "_attempt", new=AsyncMock()) as attempt:
            result = await engine.scrape("https://example.com", "fast", float("nan"))
        self.assertEqual(result.error_code, "INVALID_ARGUMENT")
        attempt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
