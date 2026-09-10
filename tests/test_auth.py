"""Interactive authentication profile validation and state scoping tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.auth import AuthProfileError, SUPPORTED_SITES, load_auth_state
from src.engine import ScraplingEngine
from src.models import ScrapeResult


class AuthStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        (self.directory / "github.state.json").write_text(json.dumps({
            "cookies": [
                {"name": "session", "value": "secret", "domain": ".github.com",
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
        self.assertEqual([item["name"] for item in state["cookies"]], ["session"])
        self.assertEqual(state["origins"][0]["localStorage"][0]["name"], "token")

    def test_state_is_scoped_to_preset_domain(self):
        with self.assertRaises(AuthProfileError):
            load_auth_state("github", "https://gist.github.io/", self.directory)
        with self.assertRaises(AuthProfileError):
            load_auth_state("missing", "https://github.com/", self.directory)


class AuthEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_passes_auth_state_to_worker(self):
        directory = Path(tempfile.mkdtemp(prefix="scrapling-auth-"))
        (directory / "github.state.json").write_text(json.dumps({
            "cookies": [{"name": "session", "value": "secret", "domain": "github.com"}],
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
            self.assertEqual(attempt.await_args.args[5]["cookies"][0]["name"], "session")
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
