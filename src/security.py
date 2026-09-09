"""Strict URL parsing and DNS validation, shared by engine and egress proxy."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = (80, 443)


class UnsafeUrlError(ValueError):
    pass


class DnsError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        # Translation/tunnel ranges can conceal a private IPv4 destination.
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if any(address in ipaddress.ip_network(net) for net in (
            "64:ff9b::/96", "64:ff9b:1::/48", "2001::/23",
        )):
            return False
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


def normalize_url(url: str, allowed_ports=DEFAULT_PORTS) -> str:
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise UnsafeUrlError("URL 必须是 1–8192 字符的 HTTP(S) 地址")
    if any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
        raise UnsafeUrlError("URL 包含空白、控制字符或反斜杠")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not host:
            raise UnsafeUrlError("仅允许完整的 HTTP(S) URL")
        if parts.username is not None or parts.password is not None or "%" in host:
            raise UnsafeUrlError("URL 不允许凭据、编码主机名或 IPv6 zone ID")
        port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
        if port not in allowed_ports:
            raise UnsafeUrlError("目标端口未被服务端允许")
        host = host.rstrip(".").lower().encode("idna").decode("ascii")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".localdomain")):
            raise UnsafeUrlError("禁止访问本机和内部域名")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise UnsafeUrlError("主机名格式无效")
        else:
            if not _is_public_ip(str(address)):
                raise UnsafeUrlError("禁止访问非公网 IP 地址")
            host = f"[{address}]" if address.version == 6 else str(address)
        # Keep the effective port explicit so every layer shares one canonical URL.
        authority = host + f":{port}"
        return urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, UnsafeUrlError):
            raise
        raise UnsafeUrlError("URL 格式无效") from exc


def _check_addresses(records) -> tuple[str, ...]:
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise DnsError("DNS 未返回可用地址")
    if any(not _is_public_ip(ip) for ip in addresses):
        raise UnsafeUrlError("DNS 返回了非公网地址")
    return addresses


async def resolve_public(host: str, port: int) -> tuple[str, ...]:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            async with asyncio.timeout(5):
                records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, TimeoutError) as exc:
            raise DnsError("DNS 解析失败或超时") from exc
        return _check_addresses(records)
    if not _is_public_ip(str(address)):
        raise UnsafeUrlError("禁止访问非公网 IP 地址")
    return (str(address),)


async def validate_url(url: str, allowed_ports=DEFAULT_PORTS) -> str:
    normalized = normalize_url(url, allowed_ports)
    parts = urlsplit(normalized)
    await resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    return normalized


def validate_public_url(url: str) -> str:
    """Synchronous compatibility helper; browser sockets use resolve_public."""
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    try:
        _check_addresses(socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM))
    except OSError as exc:
        raise DnsError("DNS 解析失败") from exc
    return normalized
