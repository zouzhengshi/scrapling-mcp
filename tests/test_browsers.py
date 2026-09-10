"""Opt-in local browser regressions, with zero public network dependency.

Only fixture.test is mapped to a controlled local origin by the TEST proxy.
Production URL checks and all other proxy destinations retain their real policy.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from src.egress import EgressProxy
from src.engine import ScraplingEngine


@unittest.skipUnless(os.environ.get("SCRAPLING_BROWSER_TESTS") == "1", "set SCRAPLING_BROWSER_TESTS=1")
class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hits = []
        self.connections = set()
        handle, cookie_name = tempfile.mkstemp(prefix="scrapling-browser-cookies-", suffix=".json")
        os.close(handle)
        self.cookie_file = Path(cookie_name)
        self.cookie_file.write_text(json.dumps({"profiles": {"fixture": {
            "allowed_domains": ["fixture.test"],
            "cookies": [{"name": "session", "value": "ok", "domain": "fixture.test",
                          "path": "/", "secure": False}],
        }}}), encoding="utf-8")

        async def origin(reader, writer):
            self.connections.add(writer)
            try:
                raw = await reader.readuntil(b"\r\n\r\n")
                path = raw.split(b" ")[1].decode()
                self.hits.append(path)
                has_cookie = b"cookie: session=ok" in raw.lower()
                secret = f"http://127.0.0.1:{self.port}/secret"
                status = "200 OK"
                extra = ""
                if path == "/redirect":
                    status = "302 Found"
                    extra = f"Location: {secret}\r\n"
                    body = ""
                elif path == "/attacks":
                    body = f"""<html><title>Fixture</title><main>Safe content</main>
                    <script>
                    fetch('{secret}').catch(()=>{{}});
                    fetch('http://fixture.test:{self.port}/resource-redirect').catch(()=>{{}});
                    window.open('{secret}');
                    const img = new Image(); img.src = '{secret}';
                    try {{ new WebSocket('ws://127.0.0.1:{self.port}/secret'); }} catch(e) {{}}
                    setTimeout(()=>{{let p=document.createElement('p'); p.id='ready';
                    p.textContent='Dynamic ready'; document.querySelector('main').append(p)}},350);
                    </script></html>"""
                elif path == "/resource-redirect":
                    status = "302 Found"
                    extra = f"Location: {secret}\r\n"
                    body = ""
                elif path == "/private":
                    if has_cookie:
                        body = "<title>Private</title><main><h1>Authenticated content</h1></main>"
                    else:
                        status = "401 Unauthorized"
                        body = "<title>Login required</title><main>Login required</main>"
                else:
                    body = "<title>Fixture</title><main><h1>Fixture page</h1><p>Visible text</p></main>"
                encoded = body.encode()
                writer.write(f"HTTP/1.1 {status}\r\n{extra}Content-Type: text/html; charset=utf-8\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode() + encoded)
                await writer.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                self.connections.discard(writer)

        self.server = await asyncio.start_server(origin, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        port = self.port

        class FixtureProxy(EgressProxy):
            async def _connect(self, host, target_port):
                if host == "fixture.test" and target_port == port:
                    return await asyncio.open_connection("127.0.0.1", port)
                return await super()._connect(host, target_port)

        self.proxy_patch = patch("src.engine.EgressProxy", FixtureProxy)
        self.validation_patch = patch("src.engine.validate_url", AsyncMock(side_effect=lambda url, ports: url))
        self.proxy_patch.start()
        self.validation_patch.start()
        self.engine = ScraplingEngine(allowed_ports=(80, 443, port), min_interval=0,
                                      cookie_file=self.cookie_file)

    async def asyncTearDown(self):
        self.proxy_patch.stop()
        self.validation_patch.stop()
        self.server.close()
        await self.server.wait_closed()
        self.cookie_file.unlink(missing_ok=True)
        for writer in list(self.connections):
            writer.close()
            await writer.wait_closed()

    async def test_both_engines_block_redirect_before_destination_is_contacted(self):
        for mode in ("fast", "stealth"):
            with self.subTest(mode=mode):
                self.hits.clear()
                result = await self.engine.scrape(f"http://fixture.test:{self.port}/redirect", mode, 25)
                self.assertIn("/redirect", self.hits, result.to_dict())
                self.assertFalse(result.success, result.to_dict())
                self.assertEqual(result.error_code, "UNSAFE_URL", result.to_dict())
                self.assertNotIn("/secret", self.hits)

    async def test_both_engines_block_subresources_popups_and_wait_for_css(self):
        for mode in ("fast", "stealth"):
            with self.subTest(mode=mode):
                self.hits.clear()
                result = await self.engine.scrape(
                    f"http://fixture.test:{self.port}/attacks", mode, 25, 1000,
                    wait_for="#ready", css_selector="main")
                self.assertTrue(result.success, result.to_dict())
                self.assertIn("Dynamic ready", result.markdown)
                self.assertNotIn("/secret", self.hits)

    async def test_both_engines_use_scoped_cookie_profile(self):
        for mode in ("fast", "stealth"):
            with self.subTest(mode=mode):
                self.hits.clear()
                result = await self.engine.scrape(
                    f"http://fixture.test:{self.port}/private", mode, 25, 1000,
                    cookie_profile="fixture")
                self.assertTrue(result.success, result.to_dict())
                self.assertIn("Authenticated content", result.markdown)

    async def test_timeout_cleanup_releases_capacity_for_following_request(self):
        result = await self.engine.scrape(f"http://fixture.test:{self.port}/", "stealth", 0.1)
        self.assertEqual(result.error_code, "TIMEOUT")
        self.assertEqual(self.engine._pending, 0)
        result = await self.engine.scrape(f"http://fixture.test:{self.port}/", "stealth", 25)
        self.assertTrue(result.success, result.to_dict())
