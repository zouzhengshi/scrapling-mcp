"""Per-request HTTP/CONNECT proxy with optional system/upstream proxy routing.

Redirects and subresources are checked before opening sockets. TLS is tunneled
without interception; browsers still verify certificates. A configured
upstream proxy is treated as trusted deployment configuration, while target
URL validation remains enabled.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass
import hmac
import ipaddress
import os
import secrets
import socket
import urllib.request
from urllib.parse import unquote, urlsplit

from src.security import DEFAULT_PORTS, DnsError, UnsafeUrlError, normalize_url, resolve_public


class UpstreamProxyError(ValueError):
    """An explicitly configured upstream proxy is invalid."""


@dataclass(frozen=True)
class UpstreamProxy:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def display(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


def parse_upstream_proxy(raw: str | None) -> UpstreamProxy | None:
    """Parse a local HTTP or SOCKS5 proxy without retaining its raw URI."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise UpstreamProxyError("上游代理地址必须是字符串")
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) > 2048:
        raise UpstreamProxyError("上游代理地址过长")
    if any(ord(char) < 33 or ord(char) == 127 for char in raw) or "\\" in raw:
        raise UpstreamProxyError("上游代理地址包含空白、控制字符或反斜杠")
    try:
        parts = urlsplit(raw.strip())
        scheme = parts.scheme.lower()
        if scheme not in {"http", "socks5", "socks5h"}:
            raise UpstreamProxyError("上游代理只支持 http、socks5 或 socks5h")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise UpstreamProxyError("上游代理地址不能包含路径、查询参数或片段")
        host = parts.hostname
        if not host or "%" in host:
            raise UpstreamProxyError("上游代理主机无效")
        host = host.rstrip(".").lower().encode("idna").decode("ascii")
        port = parts.port or (1080 if scheme.startswith("socks5") else 80)
        if not 1 <= port <= 65535:
            raise UpstreamProxyError("上游代理端口无效")
        username = unquote(parts.username) if parts.username is not None else None
        password = unquote(parts.password) if parts.password is not None else None
        if username is None and password is not None:
            raise UpstreamProxyError("上游代理密码不能没有用户名")
        for value in (username, password):
            if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise UpstreamProxyError("上游代理凭据包含控制字符")
    except UpstreamProxyError:
        raise
    except (TypeError, UnicodeError, ValueError) as exc:
        raise UpstreamProxyError("上游代理地址无效") from exc
    return UpstreamProxy(scheme, host, port, username, password)


def _parse_proxy_value(raw: str, *, scheme: str = "http") -> UpstreamProxy | None:
    value = raw.strip()
    if not value or value.upper() == "DIRECT":
        return None
    if "://" not in value:
        value = f"{scheme}://{value}"
    return parse_upstream_proxy(value)


def _parse_proxy_server(server: str) -> dict[str, UpstreamProxy]:
    routes: dict[str, UpstreamProxy] = {}
    for item in server.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            scheme, value = item.split("=", 1)
            scheme = scheme.strip().lower()
            if scheme not in {"http", "https", "all"}:
                continue
            route_name = "*" if scheme == "all" else scheme
        else:
            route_name, value = "*", item
        proxy = _parse_proxy_value(value, scheme="http")
        if proxy is not None:
            routes[route_name] = proxy
    return routes


def _read_system_proxy() -> tuple[dict[str, UpstreamProxy], list[str], dict[str, object]]:
    """Read static OS/environment proxy settings without executing PAC code."""
    routes: dict[str, UpstreamProxy] = {}
    bypass: list[str] = []
    metadata: dict[str, object] = {"source": None, "pac": False, "pac_url": None}
    if os.name == "nt":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                server = winreg.QueryValueEx(key, "ProxyServer")[0] if enabled else ""
                override = winreg.QueryValueEx(key, "ProxyOverride")[0] if enabled else ""
                pac_url = winreg.QueryValueEx(key, "AutoConfigURL")[0]
            if server:
                routes = _parse_proxy_server(str(server))
                metadata["source"] = "windows_system_proxy"
            if override:
                bypass.extend(str(override).split(";"))
            if pac_url:
                metadata["pac"] = True
                metadata["pac_url"] = "已检测到 PAC/WPAD 配置（未执行）"
        except (OSError, ImportError, UpstreamProxyError):
            # A malformed system setting must not prevent direct mode.
            routes = {}
    if not routes:
        # ``urllib`` combines standard environment variables and the native
        # Windows proxy settings, while remaining usable on Linux/macOS.  It
        # does not execute PAC/WPAD scripts, which keeps routing predictable.
        try:
            standard_proxies = urllib.request.getproxies()
        except Exception:
            standard_proxies = {}
        environment_values = {
            "http": standard_proxies.get("http") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
            "https": standard_proxies.get("https") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            "*": standard_proxies.get("all") or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"),
        }
        for scheme, value in environment_values.items():
            if not value:
                continue
            try:
                proxy = _parse_proxy_value(value, scheme="http")
            except UpstreamProxyError:
                continue
            if proxy is not None:
                routes[scheme] = proxy
        if routes:
            metadata["source"] = "proxy_environment"
    environment_bypass = (os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
                          or (standard_proxies.get("no") if "standard_proxies" in locals() else None))
    if environment_bypass:
        bypass.extend(environment_bypass.split(","))
    bypass = [item.strip() for item in bypass if item.strip()]
    return routes, bypass, metadata


def resolve_proxy_configuration(raw: str | None = None, mode: str | None = None):
    """Resolve explicit, direct, or browser-like static system proxy mode."""
    if raw is None:
        raw = os.environ.get("SCRAPLING_UPSTREAM_PROXY")
    selected_mode = (mode or os.environ.get("SCRAPLING_PROXY_MODE", "direct")).strip().lower()
    if selected_mode not in {"direct", "auto"}:
        raise UpstreamProxyError("SCRAPLING_PROXY_MODE 只能是 direct 或 auto")
    if raw and raw.strip():
        proxy = parse_upstream_proxy(raw)
        return ({"*": proxy} if proxy else {}, [], {"mode": "explicit", "source": "SCRAPLING_UPSTREAM_PROXY", "pac": False})
    if selected_mode == "auto":
        routes, bypass, metadata = _read_system_proxy()
        metadata["mode"] = "auto"
        return routes, bypass, metadata
    return {}, [], {"mode": "direct", "source": None, "pac": False}


def upstream_proxy_info(raw: str | None = None) -> dict:
    """Return a safe proxy summary for diagnostics; never return credentials."""
    configured_raw = raw if raw is not None else os.environ.get("SCRAPLING_UPSTREAM_PROXY")
    try:
        routes, bypass, metadata = resolve_proxy_configuration(raw)
    except UpstreamProxyError as exc:
        return {"configured": bool(configured_raw and configured_raw.strip()), "active": False,
                "proxy": None, "error": str(exc)}
    ordered_routes = {name: proxy.display for name, proxy in routes.items()}
    primary = routes.get("https") or routes.get("*") or next(iter(routes.values()), None)
    return {
        "configured": bool(routes), "active": bool(routes),
        "mode": metadata.get("mode"), "source": metadata.get("source"),
        "proxy": primary.display if primary else None,
        "routes": ordered_routes,
        "bypass": bypass,
        "authentication": any(proxy.username for proxy in routes.values()),
        "pac_detected": bool(metadata.get("pac")),
        "pac_note": metadata.get("pac_url"),
    }


class EgressProxy:
    def __init__(self, allowed_ports=DEFAULT_PORTS, max_bytes=20_000_000,
                 upstream_proxy: str | None = None):
        self.allowed_ports = allowed_ports
        self.max_bytes = max_bytes
        self.bytes_transferred = 0
        self.blocked_requests = 0
        self.limit_exceeded = False
        self.tasks: set[asyncio.Task] = set()
        self._server = None
        self._password = secrets.token_urlsafe(24)
        self._auth = "Basic " + base64.b64encode(f"scrapling:{self._password}".encode()).decode()
        self.proxy_routes, self.no_proxy, self.proxy_metadata = resolve_proxy_configuration(upstream_proxy)

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

    def _proxy_for(self, scheme, host):
        host = host.rstrip(".").lower()
        for item in self.no_proxy:
            item = item.strip().lower()
            if item == "*":
                return None
            if item == "<local>" and "." not in host:
                return None
            if item.startswith("[") and "]" in item:
                item = item[1:item.index("]")]
            elif item.count(":") == 1:
                item = item.rsplit(":", 1)[0]
            item = item.lstrip("*")
            if item.startswith("."):
                if host == item[1:] or host.endswith(item):
                    return None
            elif host == item:
                return None
        return self.proxy_routes.get(scheme) or self.proxy_routes.get("*")

    async def _open_upstream(self, proxy):
        async with asyncio.timeout(5):
            return await asyncio.open_connection(proxy.host, proxy.port)

    @staticmethod
    async def _read_exact(reader, size):
        async with asyncio.timeout(5):
            return await reader.readexactly(size)

    @staticmethod
    def _authority(host, port):
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    async def _connect_http_upstream(self, host, port, proxy):
        reader, writer = await self._open_upstream(proxy)
        authority = self._authority(host, port)
        headers = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}", "Proxy-Connection: keep-alive"]
        if proxy.username is not None:
            credentials = f"{proxy.username}:{proxy.password or ''}".encode("utf-8")
            headers.append("Proxy-Authorization: Basic " + base64.b64encode(credentials).decode("ascii"))
        try:
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("latin1"))
            await writer.drain()
            async with asyncio.timeout(5):
                response = await reader.readuntil(b"\r\n\r\n")
            first_line = response.split(b"\r\n", 1)[0].decode("latin1")
            status = first_line.split(" ", 2)
            if len(status) < 2 or not status[1].isdigit() or not 200 <= int(status[1]) < 300:
                raise OSError("上游 HTTP 代理拒绝了 CONNECT 请求")
            return reader, writer
        except BaseException:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            raise

    async def _connect_socks5_upstream(self, host, port, proxy):
        reader, writer = await self._open_upstream(proxy)
        try:
            methods = [0x00, 0x02] if proxy.username is not None else [0x00]
            writer.write(bytes((5, len(methods), *methods)))
            await writer.drain()
            version, method = await self._read_exact(reader, 2)
            if version != 5 or method == 0xFF:
                raise OSError("上游 SOCKS5 代理不支持认证方式")
            if method == 0x02:
                username = (proxy.username or "").encode("utf-8")
                password = (proxy.password or "").encode("utf-8")
                if not 1 <= len(username) <= 255 or len(password) > 255:
                    raise OSError("上游 SOCKS5 代理凭据长度无效")
                writer.write(bytes((1, len(username))) + username + bytes((len(password),)) + password)
                await writer.drain()
                auth_version, auth_status = await self._read_exact(reader, 2)
                if auth_version != 1 or auth_status != 0:
                    raise OSError("上游 SOCKS5 代理认证失败")
            elif method != 0x00:
                raise OSError("上游 SOCKS5 代理要求未配置的认证方式")

            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                encoded_host = host.encode("idna")
                if not 1 <= len(encoded_host) <= 255:
                    raise OSError("目标域名长度无效")
                address_part = bytes((3, len(encoded_host))) + encoded_host
            else:
                address_part = bytes((1 if address.version == 4 else 4,)) + address.packed
            writer.write(bytes((5, 1, 0)) + address_part + port.to_bytes(2, "big"))
            await writer.drain()
            response = await self._read_exact(reader, 4)
            if response[0] != 5 or response[1] != 0:
                raise OSError("上游 SOCKS5 代理连接目标失败")
            address_type = response[3]
            if address_type == 1:
                await self._read_exact(reader, 4)
            elif address_type == 3:
                length = (await self._read_exact(reader, 1))[0]
                await self._read_exact(reader, length)
            elif address_type == 4:
                await self._read_exact(reader, 16)
            else:
                raise OSError("上游 SOCKS5 代理返回了无效地址")
            await self._read_exact(reader, 2)
            return reader, writer
        except BaseException:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            raise

    async def _connect(self, host, port, *, tunnel=True, scheme=None):
        ips = await resolve_public(host, port)
        proxy = self._proxy_for(scheme or ("https" if port == 443 else "http"), host)
        if proxy is not None:
            if proxy.scheme == "http":
                if tunnel:
                    return await self._connect_http_upstream(host, port, proxy)
                return await self._open_upstream(proxy)
            return await self._connect_socks5_upstream(host, port, proxy)
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
                selected_proxy = self._proxy_for(parts.scheme, parts.hostname)
                forward_http = bool(selected_proxy and selected_proxy.scheme == "http"
                                     and parts.scheme == "http" and method in {"GET", "HEAD"})
                if selected_proxy is None:
                    # Keep the simple call shape for embedders that override _connect.
                    remote_reader, remote_writer = await self._connect(parts.hostname, port)
                else:
                    remote_reader, remote_writer = await self._connect(
                        parts.hostname, port, tunnel=not forward_http,
                        scheme=parts.scheme,
                    )
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
                    request_target = url if forward_http else path
                    request = f"{method} {request_target} HTTP/1.1\r\nHost: {parts.netloc}\r\n{forwarded}Connection: close\r\n\r\n"
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
