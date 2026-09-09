"""Per-request HTTP/CONNECT proxy: authenticate, validate DNS, connect numeric IP.

Redirects and subresources are checked before opening sockets. TLS is tunneled
without interception; browsers still verify certificates.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import secrets
import socket
from urllib.parse import urlsplit

from src.security import DEFAULT_PORTS, DnsError, UnsafeUrlError, normalize_url, resolve_public


class EgressProxy:
    def __init__(self, allowed_ports=DEFAULT_PORTS, max_bytes=20_000_000):
        self.allowed_ports = allowed_ports
        self.max_bytes = max_bytes
        self.bytes_transferred = 0
        self.blocked_requests = 0
        self.limit_exceeded = False
        self.tasks: set[asyncio.Task] = set()
        self._server = None
        self._password = secrets.token_urlsafe(24)
        self._auth = "Basic " + base64.b64encode(f"scrapling:{self._password}".encode()).decode()

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=16384)
        port = self._server.sockets[0].getsockname()[1]
        self.browser_proxy = {"server": f"http://127.0.0.1:{port}", "username": "scrapling", "password": self._password}
        return self

    async def __aexit__(self, *args):
        self._server.close()
        await self._server.wait_closed()
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _accept(self, reader, writer):
        if len(self.tasks) >= 32:
            writer.close()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _connect(self, host, port):
        ips = await resolve_public(host, port)
        last_error = None
        for ip in ips:
            try:
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                async with asyncio.timeout(4):
                    return await asyncio.open_connection(ip, port, family=family)
            except (OSError, TimeoutError) as exc:
                last_error = exc
        raise OSError("无法连接目标服务") from last_error

    async def _reply(self, writer, status):
        extra = 'Proxy-Authenticate: Basic realm="scrapling"\r\n' if status == "407 Proxy Authentication Required" else ""
        writer.write(f"HTTP/1.1 {status}\r\n{extra}Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()

    async def _copy(self, reader, writer):
        while chunk := await reader.read(65536):
            self.bytes_transferred += len(chunk)
            if self.bytes_transferred > self.max_bytes:
                self.limit_exceeded = True
                raise ValueError("network byte limit")
            writer.write(chunk)
            await writer.drain()

    async def _tunnel(self, client_reader, client_writer, remote_reader, remote_writer):
        tasks = [asyncio.create_task(self._copy(client_reader, remote_writer)),
                 asyncio.create_task(self._copy(remote_reader, client_writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle(self, reader, writer):
        remote_writer = None
        established = False
        try:
            async with asyncio.timeout(120):
                async with asyncio.timeout(5):
                    raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode("latin1").split("\r\n")
                method, target, version = lines[0].split(" ")
                if version not in {"HTTP/1.0", "HTTP/1.1"}:
                    raise ValueError("invalid HTTP version")
                headers = {}
                for line in lines[1:-2]:
                    name, value = line.split(":", 1)
                    key = name.lower()
                    if not name or key in headers or any(c.isspace() for c in name):
                        raise ValueError("invalid or duplicate header")
                    headers[key] = value.strip()
                if not hmac.compare_digest(headers.get("proxy-authorization", ""), self._auth):
                    await self._reply(writer, "407 Proxy Authentication Required")
                    return
                if self.limit_exceeded:
                    raise ValueError("network byte limit")
                if method == "CONNECT":
                    if any(c in target for c in "/?#@"):
                        raise UnsafeUrlError("invalid CONNECT authority")
                    url = normalize_url("https://" + target, self.allowed_ports)
                elif method in {"GET", "HEAD"}:
                    url = normalize_url(target, self.allowed_ports)
                    if not url.startswith("http://"):
                        raise UnsafeUrlError("HTTPS must use CONNECT")
                else:
                    await self._reply(writer, "405 Method Not Allowed")
                    return
                if "transfer-encoding" in headers or headers.get("content-length", "0") != "0":
                    raise ValueError("request body not supported")
                parts = urlsplit(url)
                port = parts.port or (443 if parts.scheme == "https" else 80)
                remote_reader, remote_writer = await self._connect(parts.hostname, port)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                    established = True
                    await self._tunnel(reader, writer, remote_reader, remote_writer)
                else:
                    path = parts.path + ("?" + parts.query if parts.query else "")
                    excluded = {"host", "connection", "proxy-connection", "proxy-authorization", "keep-alive", "upgrade", "te", "trailer", "content-length"}
                    excluded.update(x.strip().lower() for x in headers.get("connection", "").split(","))
                    forwarded = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k not in excluded)
                    request = f"{method} {path} HTTP/1.1\r\nHost: {parts.netloc}\r\n{forwarded}Connection: close\r\n\r\n"
                    remote_writer.write(request.encode("latin1"))
                    await remote_writer.drain()
                    established = True
                    # No pipelining into this socket; every new target is checked.
                    await self._copy(remote_reader, writer)
        except UnsafeUrlError:
            self.blocked_requests += 1
            if not established:
                with contextlib.suppress(OSError):
                    await self._reply(writer, "403 Forbidden")
        except (OSError, ValueError, DnsError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not established:
                with contextlib.suppress(OSError):
                    await self._reply(writer, "502 Bad Gateway")
        finally:
            for stream in (remote_writer, writer):
                if stream:
                    stream.close()
                    with contextlib.suppress(OSError):
                        await stream.wait_closed()
