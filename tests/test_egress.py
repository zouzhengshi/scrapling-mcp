"""Real local sockets; no external network. Forbidden destinations must see zero hits."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from src.egress import (EgressProxy, UpstreamProxyError, parse_upstream_proxy,
                        resolve_proxy_configuration, upstream_proxy_info)
from src.security import UnsafeUrlError


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        async def origin(reader, writer):
            try:
                self.requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        self.origin = await asyncio.start_server(origin, "127.0.0.1", 0)
        self.port = self.origin.sockets[0].getsockname()[1]
        self.proxy = await EgressProxy(allowed_ports=(80, 443, self.port)).__aenter__()

    async def asyncTearDown(self):
        await self.proxy.__aexit__()
        self.origin.close()
        await self.origin.wait_closed()

    async def request(self, request, auth=True):
        proxy = urlsplit(self.proxy.browser_proxy["server"])
        reader, writer = await asyncio.open_connection(proxy.hostname, proxy.port)
        headers = f"Proxy-Authorization: {self.proxy._auth}\r\n" if auth else ""
        writer.write((request + "\r\n" + headers + "\r\n").encode())
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), 2)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_authentication_precedes_dns_and_connect(self):
        with patch("src.egress.resolve_public", new_callable=AsyncMock) as dns:
            response = await self.request("CONNECT fixture.test:443 HTTP/1.1", auth=False)
        self.assertIn(b"407", response)
        dns.assert_not_awaited()
        self.assertEqual(self.requests, [])

    async def test_private_connect_and_http_are_blocked_before_socket(self):
        for request in (
            f"CONNECT 127.0.0.1:{self.port} HTTP/1.1",
            f"GET http://127.0.0.1:{self.port}/secret HTTP/1.1",
            "CONNECT 169.254.169.254:80 HTTP/1.1",
            "CONNECT [::ffff:127.0.0.1]:443 HTTP/1.1",
        ):
            with self.subTest(request=request):
                self.assertIn(b"403", await self.request(request))
        self.assertEqual(self.requests, [])

    async def test_private_dns_never_connects(self):
        with patch("src.egress.resolve_public", AsyncMock(side_effect=UnsafeUrlError("private"))):
            self.assertIn(b"403", await self.request("CONNECT private.test:443 HTTP/1.1"))
        self.assertEqual(self.requests, [])

    async def test_connect_uses_validated_numeric_address(self):
        reader, writer = object(), object()
        with patch("src.egress.resolve_public", AsyncMock(return_value=("93.184.216.34",))) as dns, patch(
            "src.egress.asyncio.open_connection", AsyncMock(return_value=(reader, writer))
        ) as connect:
            self.assertEqual(await self.proxy._connect("fixture.test", 443), (reader, writer))
        dns.assert_awaited_once_with("fixture.test", 443)
        self.assertEqual(connect.await_args.args, ("93.184.216.34", 443))

    async def test_http_strips_credentials_rewrites_host_and_rejects_smuggling(self):
        original_connect = asyncio.open_connection

        async def fixture_connect(host, port):
            self.assertEqual(host, "fixture.test")
            return await original_connect("127.0.0.1", self.port)

        with patch.object(self.proxy, "_connect", side_effect=fixture_connect):
            response = await self.request("GET http://fixture.test/path HTTP/1.1\r\nHost: wrong.test")
            self.assertTrue(response.endswith(b"ok"))
            bad = await self.request("GET http://fixture.test/ HTTP/1.1\r\nContent-Length: 1\r\nTransfer-Encoding: chunked")
            self.assertIn(b"502", bad)
        self.assertEqual(len(self.requests), 1)
        self.assertIn(b"Host: fixture.test", self.requests[0])
        self.assertNotIn(self.proxy._password.encode(), self.requests[0])
        self.assertNotIn(b"proxy-authorization", self.requests[0].lower())

    async def test_byte_limit_stops_stream(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"abcd")
        reader.feed_eof()
        self.proxy.max_bytes = 3
        writer = unittest.mock.Mock()
        with self.assertRaises(ValueError):
            await self.proxy._copy(reader, writer)
        self.assertTrue(self.proxy.limit_exceeded)
        writer.write.assert_not_called()

    async def test_http_upstream_proxy_forwards_http_request_without_credentials(self):
        upstream_requests = []

        async def upstream(reader, writer):
            try:
                upstream_requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        local_proxy = await EgressProxy(
            allowed_ports=(80, 443),
            upstream_proxy=f"http://proxy-user:proxy-password@127.0.0.1:{upstream_port}",
        ).__aenter__()
        original_proxy = self.proxy
        self.proxy = local_proxy
        try:
            with patch("src.egress.resolve_public", new=AsyncMock(return_value=("93.184.216.34",))):
                response = await self.request("GET http://example.com/path?q=1 HTTP/1.1")
            self.assertTrue(response.endswith(b"ok"))
            self.assertEqual(len(upstream_requests), 1)
            self.assertIn(b"GET http://example.com:80/path?q=1 HTTP/1.1", upstream_requests[0])
            self.assertIn(b"Host: example.com:80", upstream_requests[0])
            self.assertNotIn(b"proxy-password", upstream_requests[0])
            self.assertNotIn(b"proxy-authorization", upstream_requests[0].lower())
        finally:
            await local_proxy.__aexit__()
            self.proxy = original_proxy
            upstream_server.close()
            await upstream_server.wait_closed()

    async def test_http_upstream_proxy_tunnels_https_connect(self):
        upstream_requests = []

        async def upstream(reader, writer):
            try:
                upstream_requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        proxy = EgressProxy(upstream_proxy=f"http://127.0.0.1:{upstream_port}")
        try:
            with patch("src.egress.resolve_public", new=AsyncMock(return_value=("93.184.216.34",))):
                reader, writer = await proxy._connect("www.youtube.com", 443)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.01)
        finally:
            upstream_server.close()
            await upstream_server.wait_closed()
        self.assertEqual(len(upstream_requests), 1)
        self.assertIn(b"CONNECT www.youtube.com:443 HTTP/1.1", upstream_requests[0])

    async def test_socks5_upstream_uses_remote_domain_resolution(self):
        handshake = {}

        async def upstream(reader, writer):
            try:
                handshake["greeting"] = await reader.readexactly(3)
                writer.write(b"\x05\x00")
                await writer.drain()
                header = await reader.readexactly(4)
                length = (await reader.readexactly(1))[0]
                handshake["request"] = header + bytes((length,)) + await reader.readexactly(length) + await reader.readexactly(2)
                writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01")
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        proxy = EgressProxy(upstream_proxy=f"socks5://127.0.0.1:{upstream_port}")
        try:
            with patch("src.egress.resolve_public", new=AsyncMock(return_value=("93.184.216.34",))):
                reader, writer = await proxy._connect("www.youtube.com", 443)
            writer.close()
            await writer.wait_closed()
        finally:
            upstream_server.close()
            await upstream_server.wait_closed()
        self.assertEqual(handshake["greeting"], b"\x05\x01\x00")
        self.assertEqual(handshake["request"][0:4], b"\x05\x01\x00\x03")
        host_length = len(b"www.youtube.com")
        self.assertEqual(handshake["request"][5:5 + host_length], b"www.youtube.com")
        self.assertEqual(handshake["request"][5 + host_length:], b"\x01\xbb")

    def test_proxy_configuration_is_validated_and_redacted(self):
        proxy = parse_upstream_proxy("http://proxy-user:proxy-password@127.0.0.1:7890")
        self.assertEqual(proxy.display, "http://127.0.0.1:7890")
        self.assertNotIn("proxy-password", proxy.display)
        self.assertTrue(upstream_proxy_info("http://user:secret@example.com:8080")["authentication"])
        with self.assertRaises(UpstreamProxyError):
            parse_upstream_proxy("ftp://127.0.0.1:21")

    def test_auto_mode_reads_proxy_environment_without_exposing_credentials(self):
        values = {
            "SCRAPLING_PROXY_MODE": "auto",
            "SCRAPLING_UPSTREAM_PROXY": "",
            "HTTP_PROXY": "http://user:secret@127.0.0.1:7890",
            "HTTPS_PROXY": "socks5://127.0.0.1:7891",
            "NO_PROXY": "example.com,.internal.test",
        }
        with patch.dict(os.environ, values, clear=True):
            routes, bypass, metadata = resolve_proxy_configuration()
            info = upstream_proxy_info()
        self.assertEqual(metadata["mode"], "auto")
        self.assertEqual(metadata["source"], "proxy_environment")
        self.assertEqual(routes["http"].display, "http://127.0.0.1:7890")
        self.assertEqual(routes["https"].display, "socks5://127.0.0.1:7891")
        self.assertEqual(bypass, ["example.com", ".internal.test"])
        self.assertEqual(info["routes"]["https"], "socks5://127.0.0.1:7891")
        self.assertTrue(info["authentication"])
        self.assertNotIn("secret", str(info))

    def test_proxy_bypass_matches_domains_and_local_hosts(self):
        proxy = EgressProxy(upstream_proxy="http://127.0.0.1:7890")
        proxy.no_proxy = ["example.com", ".internal.test", "<local>"]
        self.assertIsNone(proxy._proxy_for("https", "example.com"))
        self.assertIsNone(proxy._proxy_for("https", "www.internal.test"))
        self.assertIsNone(proxy._proxy_for("http", "intranet"))
        self.assertEqual(proxy._proxy_for("https", "youtube.com").display,
                         "http://127.0.0.1:7890")
