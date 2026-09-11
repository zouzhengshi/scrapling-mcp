"""Interactive authentication profile validation and state scoping tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import src.auth as auth_module
from src.auth import AuthProfileError, SUPPORTED_SITES, finish_login, load_auth_state, start_login
from src.engine import ScraplingEngine
from src.models import ScrapeResult


class AuthStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        (self.directory / "github.state.json").write_text(json.dumps({
            "cookies": [
                {"name": "user_session", "value": "secret", "domain": ".github.com",
                 "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax"},
                {"name": "other", "value": "not-for-target", "domain": "other.test"},
            ],
            "origins": [{"origin": "https://github.com", "localStorage": [
                {"name": "token", "value": "local-secret"},
            ]}],
        }), encoding="utf-8")

    def tearDown(self):
        for path in self.directory.glob("*"):
            path.unlink(missing_ok=True)
        self.directory.rmdir()

    def test_known_sites_are_available(self):
        self.assertIn("bilibili", SUPPORTED_SITES)
        state = load_auth_state("github", "https://github.com/", self.directory)
        self.assertEqual([item["name"] for item in state["cookies"]], ["user_session"])
        self.assertEqual(state["origins"][0]["localStorage"][0]["name"], "token")

    def test_state_is_scoped_to_preset_domain(self):
        with self.assertRaises(AuthProfileError):
            load_auth_state("github", "https://gist.github.io/", self.directory)
        with self.assertRaises(AuthProfileError):
            load_auth_state("missing", "https://github.com/", self.directory)

    def test_bilibili_visitor_state_is_not_treated_as_logged_in(self):
        (self.directory / "bilibili.state.json").write_text(json.dumps({
            "cookies": [{"name": "buvid3", "value": "visitor", "domain": ".bilibili.com"}],
            "origins": [],
        }), encoding="utf-8")
        with self.assertRaises(AuthProfileError):
            load_auth_state("bilibili", "https://api.bilibili.com/", self.directory)


class AuthEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_login_returns_before_user_finishes(self):
        class FakeProxy:
            browser_proxy = {}

            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        class FakePage:
            async def goto(self, *args, **kwargs):
                return None

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]
                self.closed = False

            def is_closed(self):
                return self.closed

            async def storage_state(self, path, indexed_db=True):
                Path(path).write_text(json.dumps({
                    "cookies": [{"name": "user_session", "value": "secret", "domain": "github.com"}],
                    "origins": [],
                }), encoding="utf-8")

            async def close(self):
                self.closed = True

        class FakeChromium:
            async def launch_persistent_context(self, *args, **kwargs):
                return context

        class FakePlaywright:
            chromium = FakeChromium()

            async def stop(self):
                return None

        class FakePlaywrightFactory:
            async def start(self):
                return playwright

        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        context = FakeContext()
        playwright = FakePlaywright()
        try:
            with patch("src.auth.EgressProxy", FakeProxy), \
                 patch("playwright.async_api.async_playwright", return_value=FakePlaywrightFactory()):
                result = await start_login("github", timeout=10, auth_dir=directory)
                self.assertEqual(result["status"], "waiting")
                self.assertFalse(result["ready"])
                finished = await finish_login("github", directory)
            self.assertEqual(finished["status"], "ready")
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_engine_passes_auth_state_to_worker(self):
        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        (directory / "github.state.json").write_text(json.dumps({
                    "cookies": [{"name": "user_session", "value": "secret", "domain": "github.com"}],
            "origins": [],
        }), encoding="utf-8")
        try:
            engine = ScraplingEngine(max_queue=0, min_interval=0, auth_dir=directory)
            worker_result = ScrapeResult("https://github.com:443/", "crawl4ai", "ok", True)
            with patch("src.engine.validate_url", new=AsyncMock(return_value="https://github.com:443/")), \
                 patch.object(engine, "_attempt", new=AsyncMock(return_value=worker_result)) as attempt:
                result = await engine.scrape("https://github.com", "fast", 2,
                                            auth_profile="github")
            self.assertTrue(result.success)
            self.assertEqual(attempt.await_args.args[5]["cookies"][0]["name"], "user_session")
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_finalize_does_not_wait_for_stuck_browser_cleanup(self):
        class ClosedContext:
            def is_closed(self):
                return True

        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        (directory / "github.state.json").write_text(json.dumps({
            "cookies": [{"name": "user_session", "value": "secret", "domain": "github.com"}],
            "origins": [],
        }), encoding="utf-8")

        async def stuck_cleanup(*args, **kwargs):
            await asyncio.Event().wait()

        session = auth_module.LoginSession(
            "github", directory / "github.state.json", object(), object(),
            ClosedContext(), object(), 0,
        )
        auth_module._sessions["github"] = session
        try:
            with patch.object(auth_module, "_close_login_session", stuck_cleanup), \
                 patch.object(auth_module, "LOGIN_CLEANUP_TIMEOUT", 0.01):
                result = await finish_login("github", directory)
            self.assertEqual(result["status"], "ready")
            self.assertNotIn("github", auth_module._sessions)
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_cookie_and_auth_profiles_cannot_be_combined(self):
        engine = ScraplingEngine(max_queue=0, min_interval=0)
        result = await engine.scrape("https://github.com", "fast", 2,
                                    cookie_profile="cookies", auth_profile="github")
        self.assertEqual(result.error_code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
