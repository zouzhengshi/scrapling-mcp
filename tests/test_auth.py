"""Interactive authentication profile validation and state scoping tests."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import src.auth as auth_module
from src.auth import (AuthProfileError, SUPPORTED_SITES, finish_login,
                      load_auth_state, start_custom_login, start_login)
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

    def test_youtube_state_allows_google_login_cookies(self):
        (self.directory / "youtube.state.json").write_text(json.dumps({
            "cookies": [
                {"name": "SID", "value": "secret", "domain": ".google.com"},
                {"name": "LOGIN_INFO", "value": "secret", "domain": ".youtube.com"},
            ],
            "origins": [],
        }), encoding="utf-8")
        state = load_auth_state("youtube", "https://www.youtube.com/", self.directory)
        self.assertEqual([item["name"] for item in state["cookies"]], ["SID", "LOGIN_INFO"])

    def test_domain_cookie_scope_is_preserved_for_subdomains(self):
        state = load_auth_state("github", "https://api.github.com/", self.directory)
        self.assertEqual(state["cookies"][0]["domain"], ".github.com")

    def test_state_is_scoped_to_preset_domain(self):
        with self.assertRaises(AuthProfileError):
            load_auth_state("github", "https://gist.github.io/", self.directory)
        with self.assertRaises(AuthProfileError):
            load_auth_state("missing", "https://github.com/", self.directory)

    def test_custom_profile_is_scoped_to_declared_domains(self):
        (self.directory / "example-account.state.json").write_text(json.dumps({
            "cookies": [{"name": "sessionid", "value": "secret", "domain": ".example.com"}],
            "origins": [],
            "scrapling_auth": {
                "kind": "custom", "profile": "example-account",
                "login_url": "https://example.com/login",
                "allowed_domains": ["example.com"],
            },
        }), encoding="utf-8")
        state = load_auth_state("example-account", "https://app.example.com/dashboard", self.directory)
        self.assertEqual(state["cookies"][0]["domain"], ".example.com")
        with self.assertRaises(AuthProfileError):
            load_auth_state("example-account", "https://other.example.net/", self.directory)

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
            with patch.dict(os.environ, {"SCRAPLING_LOGIN_BROWSER": "playwright"}, clear=False), \
                 patch("src.auth.EgressProxy", FakeProxy), \
                 patch("playwright.async_api.async_playwright", return_value=FakePlaywrightFactory()):
                result = await start_login("github", timeout=10, auth_dir=directory)
                self.assertEqual(result["status"], "waiting")
                self.assertFalse(result["ready"])
                self.assertIn("login_status", result["next_action"])
                finished = await finish_login("github", directory)
            self.assertEqual(finished["status"], "ready")
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_custom_login_saves_only_declared_domain_state(self):
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
                    "cookies": [
                        {"name": "sessionid", "value": "secret", "domain": ".example.com"},
                        {"name": "third_party", "value": "remove", "domain": ".tracker.test"},
                    ],
                    "origins": [{"origin": "https://app.example.com", "localStorage": []}],
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

        directory = Path(tempfile.mkdtemp(prefix="scrapling-custom-auth-"))
        context = FakeContext()
        playwright = FakePlaywright()
        try:
            with patch.dict(os.environ, {"SCRAPLING_LOGIN_BROWSER": "playwright"}, clear=False), \
                 patch("src.auth.EgressProxy", FakeProxy), \
                 patch("playwright.async_api.async_playwright", return_value=FakePlaywrightFactory()):
                result = await start_custom_login(
                    "example-account", "https://example.com/login?state=secret",
                    ["example.com"], timeout=10, auth_dir=directory,
                )
                self.assertEqual(result["status"], "waiting")
                self.assertIn("login_custom_status", result["next_action"])
                finished = await finish_login("example-account", directory)
            self.assertEqual(finished["status"], "ready")
            document = json.loads((directory / "example-account.state.json").read_text(encoding="utf-8"))
            self.assertEqual([c["name"] for c in document["cookies"]], ["sessionid"])
            self.assertEqual(document["scrapling_auth"]["login_url"], "https://example.com:443/login")
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_custom_login_requires_https_and_public_domain(self):
        with self.assertRaises(AuthProfileError):
            await start_custom_login("example-account", "http://example.com/login")
        with self.assertRaises(AuthProfileError):
            await start_custom_login("example-account", "https://example.com/login",
                                     ["127.0.0.1"])

    async def test_finalize_before_login_is_complete_keeps_window_open(self):
        class FakeContext:
            def __init__(self):
                self.closed = False
                self.pages = []

            def is_closed(self):
                return self.closed

            async def storage_state(self, path, indexed_db=True):
                Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

            async def close(self):
                self.closed = True

        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        context = FakeContext()
        session = auth_module.LoginSession(
            "github", directory / "github.state.json", object(), object(),
            context, object(), 0,
        )
        auth_module._sessions["github"] = session
        try:
            with self.assertRaises(AuthProfileError) as error:
                await finish_login("github", directory)
            self.assertIn("仍保持打开", str(error.exception))
            self.assertIn("github", auth_module._sessions)
            self.assertFalse(context.closed)
        finally:
            auth_module._sessions.pop("github", None)
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()

    async def test_login_status_keeps_session_when_page_list_is_temporarily_empty(self):
        class FakeContext:
            pages = []

            def is_closed(self):
                return False

            async def storage_state(self, path, indexed_db=True):
                Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        session = auth_module.LoginSession(
            "github", directory / "github.state.json", object(), object(),
            FakeContext(), object(), 0,
        )
        auth_module._sessions["github"] = session
        try:
            result = await auth_module.login_status("github", directory)
            self.assertEqual(result["status"], "waiting")
            self.assertIn("github", auth_module._sessions)
        finally:
            auth_module._sessions.pop("github", None)
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
