"""Cookie profile validation and engine hand-off tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.cookies import CookieProfileError, load_cookies
from src.engine import ScraplingEngine
from src.models import ScrapeResult


class CookieProfileTests(unittest.TestCase):
    def setUp(self):
        handle, name = tempfile.mkstemp(prefix="scrapling-cookies-", suffix=".json")
        self.path = Path(name)
        import os
        os.close(handle)
        Path(name).write_text(json.dumps({
            "profiles": {
                "demo": {
                    "allowed_domains": ["example.com"],
                    "cookies": [
                        {"name": "session", "value": "secret", "domain": ".example.com",
                         "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax"},
                        {"name": "other", "value": "not-for-target", "domain": "other.test"},
                    ],
                }
            }
        }), encoding="utf-8")

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_scopes_cookies_to_declared_target_domain(self):
        cookies = load_cookies("demo", self.path, "https://sub.example.com:443/")
        self.assertEqual([cookie["name"] for cookie in cookies], ["session"])
        self.assertEqual(cookies[0]["value"], "secret")

    def test_rejects_unauthorized_domain_and_missing_profile(self):
        with self.assertRaises(CookieProfileError):
            load_cookies("demo", self.path, "https://other.example.net:443/")
        with self.assertRaises(CookieProfileError):
            load_cookies("missing", self.path, "https://example.com:443/")


class CookieEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_passes_local_profile_cookies_to_worker(self):
        handle, name = tempfile.mkstemp(prefix="scrapling-cookies-", suffix=".json")
        path = Path(name)
        import os
        os.close(handle)
        path.write_text(json.dumps({"profiles": {"demo": {
            "allowed_domains": ["example.com"],
            "cookies": [{"name": "session", "value": "secret", "domain": "example.com"}],
        }}}), encoding="utf-8")
        try:
            engine = ScraplingEngine(max_queue=0, min_interval=0, cookie_file=path)
            worker_result = ScrapeResult("https://example.com:443/", "crawl4ai", "ok", True)
            with patch("src.engine.validate_url", new=AsyncMock(return_value="https://example.com:443/")), \
                 patch.object(engine, "_attempt", new=AsyncMock(return_value=worker_result)) as attempt:
                result = await engine.scrape("https://example.com", "fast", 2,
                                            cookie_profile="demo")
            self.assertTrue(result.success)
            self.assertEqual(attempt.await_args.args[4][0]["name"], "session")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
