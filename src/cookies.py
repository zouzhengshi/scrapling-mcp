"""Load local, named browser-cookie profiles without exposing cookie values."""
from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


class CookieProfileError(ValueError):
    """A local cookie profile is missing, malformed, or out of scope."""


PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
MAX_FILE_BYTES = 1_000_000
MAX_COOKIES = 256
MAX_COOKIE_BYTES = 256_000


def _host_matches(host: str, domain: str) -> bool:
    host = host.rstrip(".").lower()
    domain = domain.lstrip(".").rstrip(".").lower()
    return host == domain or host.endswith("." + domain)


def _domain(value, field="domain") -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise CookieProfileError(f"Cookie {field} 无效")
    value = value.lstrip(".").rstrip(".").lower()
    if not value or any(ord(c) < 33 or ord(c) == 127 for c in value) or any(c in value for c in "/\\:@"):
        raise CookieProfileError(f"Cookie {field} 无效")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise CookieProfileError(f"Cookie {field} 无效") from exc
        if len(value) > 253 or any(not DOMAIN_LABEL.fullmatch(label) for label in value.split(".")):
            raise CookieProfileError(f"Cookie {field} 无效")
    else:
        value = str(address)
    return value


def _text(value, field, maximum, *, allow_empty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        raise CookieProfileError(f"Cookie {field} 无效")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CookieProfileError(f"Cookie {field} 包含控制字符")
    return value


def _normalise_cookie(entry: dict, target_host: str, target_scheme: str, allowed_domains: tuple[str, ...]):
    if not isinstance(entry, dict):
        raise CookieProfileError("Cookie 项必须是对象")
    raw_url = entry.get("url")
    if raw_url is not None:
        raw_url = _text(raw_url, "url", 8192)
        try:
            parts = urlsplit(raw_url)
            if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname or
                    parts.username is not None or parts.password is not None or "\\" in raw_url):
                raise ValueError
            cookie_host = _domain(parts.hostname, "url")
            cookie_scheme = parts.scheme.lower()
        except (TypeError, ValueError, UnicodeError) as exc:
            raise CookieProfileError("Cookie url 无效") from exc
    else:
        cookie_host = None
        cookie_scheme = target_scheme

    raw_domain = entry.get("domain", cookie_host)
    domain = _domain(raw_domain)
    if cookie_host and not _host_matches(cookie_host, domain):
        raise CookieProfileError("Cookie domain 与 url 不匹配")
    # A cookie may belong to the declared domain or one of its subdomains;
    # never accept a broad parent such as .com for an example.com profile.
    if not any(domain == allowed or domain.endswith("." + allowed) for allowed in allowed_domains):
        return None
    if not _host_matches(target_host, domain):
        return None
    if entry.get("hostOnly", False) is not False and not isinstance(entry.get("hostOnly"), bool):
        raise CookieProfileError("Cookie hostOnly 必须是布尔值")
    if entry.get("hostOnly", False) and target_host != domain:
        return None

    name = _text(entry.get("name"), "name", 256)
    if any(c.isspace() or c in "=;," for c in name):
        raise CookieProfileError("Cookie name 无效")
    value = _text(entry.get("value", ""), "value", 8192, allow_empty=True)
    path = _text(entry.get("path", "/"), "path", 2048)
    if not path.startswith("/"):
        raise CookieProfileError("Cookie path 必须以 / 开头")
    secure = entry.get("secure", cookie_scheme == "https")
    http_only = entry.get("httpOnly", False)
    if not isinstance(secure, bool) or not isinstance(http_only, bool):
        raise CookieProfileError("Cookie secure/httpOnly 必须是布尔值")

    result = {"name": name, "value": value, "domain": domain, "path": path,
              "secure": secure, "httpOnly": http_only}
    same_site = entry.get("sameSite")
    if same_site is not None:
        if not isinstance(same_site, str):
            raise CookieProfileError("Cookie sameSite 无效")
        same_site = {"strict": "Strict", "lax": "Lax", "none": "None",
                     "no_restriction": "None", "unspecified": None}.get(same_site.lower())
        if same_site is None and entry.get("sameSite", "").lower() not in {"unspecified"}:
            raise CookieProfileError("Cookie sameSite 无效")
        if same_site:
            result["sameSite"] = same_site

    expires = entry.get("expires", entry.get("expirationDate"))
    if expires is not None:
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise CookieProfileError("Cookie expires 无效")
        if expires > 0:
            result["expires"] = float(expires)
    return result


def load_cookies(profile_name: str | None, cookie_file: str | os.PathLike | None,
                 target_url: str) -> list[dict]:
    """Return only cookies scoped to target_url; never log or return profile data."""
    if profile_name is None:
        return []
    if not isinstance(profile_name, str) or not PROFILE_NAME.fullmatch(profile_name):
        raise CookieProfileError("Cookie profile 名称无效")
    if not cookie_file:
        raise CookieProfileError("未配置 Cookie 文件，请设置 SCRAPLING_COOKIE_FILE")
    try:
        path = Path(cookie_file).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise CookieProfileError("Cookie 文件不存在或过大")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise CookieProfileError("Cookie 文件权限过宽")
        document = json.loads(path.read_text(encoding="utf-8"))
    except CookieProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CookieProfileError("Cookie 文件无法读取") from exc

    profiles = document.get("profiles") if isinstance(document, dict) and "profiles" in document else document
    profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise CookieProfileError("Cookie profile 不存在")
    allowed = profile.get("allowed_domains")
    if not isinstance(allowed, list) or not 1 <= len(allowed) <= 16:
        raise CookieProfileError("Cookie profile 必须声明 allowed_domains")
    allowed_domains = tuple(_domain(item, "allowed_domains") for item in allowed)
    target = urlsplit(target_url)
    target_host = _domain(target.hostname, "目标域名")
    target_scheme = target.scheme.lower()
    if not any(_host_matches(target_host, domain) for domain in allowed_domains):
        raise CookieProfileError("Cookie profile 未授权该目标域名")
    entries = profile.get("cookies")
    if not isinstance(entries, list) or len(entries) > MAX_COOKIES:
        raise CookieProfileError("Cookie profile cookies 数量无效")

    cookies = []
    total = 0
    for entry in entries:
        cookie = _normalise_cookie(entry, target_host, target_scheme, allowed_domains)
        if cookie is not None:
            total += len(cookie["name"]) + len(cookie["value"])
            if total > MAX_COOKIE_BYTES:
                raise CookieProfileError("Cookie profile 过大")
            cookies.append(cookie)
    if not cookies:
        raise CookieProfileError("Cookie profile 未包含该目标域名的 Cookie")
    return cookies
