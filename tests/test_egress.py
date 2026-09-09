"""Real local sockets; no external network. Forbidden destinations must see zero hits."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from src.egress import EgressProxy
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
