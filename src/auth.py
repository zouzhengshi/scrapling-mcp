"""Interactive local login profiles for a small set of known sites."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from src.cookies import CookieProfileError, _domain, _host_matches, _normalise_cookie
from src.egress import EgressProxy
from src.security import normalize_url

logger = logging.getLogger(__name__)


class AuthProfileError(ValueError):
    """A site preset or local browser login state is invalid or unavailable."""


@dataclass(frozen=True)
class SitePreset:
    login_url: str
    allowed_domains: tuple[str, ...]


SITE_PRESETS = {
    "bilibili": SitePreset(
        "https://passport.bilibili.com/login", ("bilibili.com",)),
    "github": SitePreset(
        "https://github.com/login", ("github.com",)),
    "zhihu": SitePreset(
        "https://www.zhihu.com/signin", ("zhihu.com",)),
    "weibo": SitePreset(
        "https://passport.weibo.com/", ("weibo.com", "weibo.cn")),
    "xiaohongshu": SitePreset(
        "https://www.xiaohongshu.com/", ("xiaohongshu.com",)),
}

SUPPORTED_SITES = tuple(SITE_PRESETS)
MAX_STATE_BYTES = 10_000_000
MAX_STATE_COOKIES = 512
MAX_STATE_ORIGINS = 64
MAX_LOCAL_STORAGE_ITEMS = 2000
MAX_LOCAL_STORAGE_CHARS = 1_000_000

LOGIN_BROWSER_FLAGS = [
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
]


def get_site_preset(site: str) -> SitePreset:
    if not isinstance(site, str) or site not in SITE_PRESETS:
        choices = "、".join(SUPPORTED_SITES)
        raise AuthProfileError(f"不支持的网站预设，可选：{choices}")
    return SITE_PRESETS[site]


def _auth_root(auth_dir: str | os.PathLike | None = None) -> Path:
    raw = auth_dir or os.environ.get("SCRAPLING_AUTH_DIR")
    if raw:
        path = Path(raw).expanduser()
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ScraplingMCP" / "auth-profiles"
    else:
        path = Path.home() / ".local" / "share" / "scrapling-mcp" / "auth-profiles"
    try:
        if path.is_symlink():
            raise AuthProfileError("认证状态目录不能是符号链接")
        path.mkdir(parents=True, exist_ok=True)
        path = path.resolve()
        if os.name != "nt":
            os.chmod(path, 0o700)
    except AuthProfileError:
        raise
    except OSError as exc:
        raise AuthProfileError("认证状态目录无法创建或访问") from exc
    return path


def _state_path(site: str, auth_dir: str | os.PathLike | None = None) -> Path:
    get_site_preset(site)
    return _auth_root(auth_dir) / f"{site}.state.json"


def _read_state(path: Path) -> dict:
    try:
        if not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
            raise AuthProfileError("登录状态不存在或过大，请重新登录")
        document = json.loads(path.read_text(encoding="utf-8"))
    except AuthProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthProfileError("登录状态无法读取，请重新登录") from exc
    if not isinstance(document, dict):
        raise AuthProfileError("登录状态格式无效，请重新登录")
    return document


def _state_has_entries(document: dict) -> bool:
    return bool(document.get("cookies")) or bool(document.get("origins"))


async def _save_state(context, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        await context.storage_state(path=str(temporary), indexed_db=True)
        os.replace(temporary, destination)
        if os.name != "nt":
            os.chmod(destination, 0o600)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


async def interactive_login(site: str, timeout: float = 300.0,
                            auth_dir: str | os.PathLike | None = None) -> dict:
    """Open a visible browser and save the user's completed login locally.

    The user closes the browser window after completing login. Snapshots are
    written periodically so closing the window also acts as confirmation.
    """
    preset = get_site_preset(site)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 10 <= timeout <= 600:
        raise AuthProfileError("登录等待时间必须在 10–600 秒之间")
    destination = _state_path(site, auth_dir)
    login_url = normalize_url(preset.login_url)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise AuthProfileError("未安装 Playwright，无法打开登录浏览器") from exc

    logger.info("interactive login started site=%s; close the browser after login", site)
    try:
        async with EgressProxy(max_bytes=50_000_000) as proxy:
            async with async_playwright() as playwright:
                with tempfile.TemporaryDirectory(prefix="scrapling-login-") as browser_dir:
                    context = await playwright.chromium.launch_persistent_context(
                        browser_dir,
                        headless=False,
                        proxy=proxy.browser_proxy,
                        args=LOGIN_BROWSER_FLAGS,
                        ignore_https_errors=False,
                        accept_downloads=False,
                        service_workers="allow",
                    )
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        await page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
                        loop = asyncio.get_running_loop()
                        deadline = loop.time() + float(timeout)
                        while loop.time() < deadline:
                            if context.is_closed() or not context.pages:
                                break
                            try:
                                await _save_state(context, destination)
                            except Exception as exc:
                                if context.is_closed():
                                    break
                                raise AuthProfileError("登录状态保存失败") from exc
                            await asyncio.sleep(min(1.0, max(0.1, deadline - loop.time())))
                        if not context.is_closed():
                            await _save_state(context, destination)
                    finally:
                        if not context.is_closed():
                            await context.close()
    except AuthProfileError:
        raise
    except Exception as exc:
        text = str(exc).lower()
        if "executable doesn't exist" in text or "browser was not found" in text:
            raise AuthProfileError("未安装 Chromium，请按 README 安装浏览器") from exc
        raise AuthProfileError("登录浏览器启动或访问失败") from exc

    document = _read_state(destination)
    if not _state_has_entries(document):
        raise AuthProfileError("没有保存到登录状态；请完成登录后再关闭浏览器")
    return {
        "success": True,
        "site": site,
        "auth_profile": site,
        "message": "登录状态已保存在本机。后续抓取请使用相同的 auth_profile 名称。",
    }


def _same_origin(origin: str, target_url: str) -> bool:
    try:
        source = urlsplit(origin)
        target = urlsplit(target_url)
        if source.scheme.lower() not in {"http", "https"} or not source.hostname:
            return False
        if source.username is not None or source.password is not None or "\\" in origin:
            return False
        source_host = _domain(source.hostname, "origin")
        target_host = _domain(target.hostname, "目标域名")
        source_port = source.port or (443 if source.scheme.lower() == "https" else 80)
        target_port = target.port or (443 if target.scheme.lower() == "https" else 80)
        return (source.scheme.lower() == target.scheme.lower() and
                source_port == target_port and source_host == target_host)
    except (CookieProfileError, TypeError, ValueError, UnicodeError):
        return False


def load_auth_state(site: str | None, target_url: str,
                    auth_dir: str | os.PathLike | None = None) -> dict | None:
    """Load only the authenticated state applicable to target_url."""
    if site is None:
        return None
    preset = get_site_preset(site)
    try:
        target = urlsplit(target_url)
        target_host = _domain(target.hostname, "目标域名")
    except (CookieProfileError, TypeError, ValueError, UnicodeError) as exc:
        raise AuthProfileError("目标域名无效") from exc
    if not any(_host_matches(target_host, domain) for domain in preset.allowed_domains):
        raise AuthProfileError("auth_profile 未授权该目标域名")

    document = _read_state(_state_path(site, auth_dir))
    raw_cookies = document.get("cookies", [])
    if not isinstance(raw_cookies, list) or len(raw_cookies) > MAX_STATE_COOKIES:
        raise AuthProfileError("登录状态 Cookie 数量无效")
    cookies = []
    try:
        allowed_domains = tuple(_domain(domain, "allowed_domains") for domain in preset.allowed_domains)
        for entry in raw_cookies:
            cookie = _normalise_cookie(entry, target_host, target.scheme.lower(), allowed_domains)
            if cookie is not None:
                cookies.append(cookie)
    except CookieProfileError as exc:
        raise AuthProfileError("登录状态 Cookie 格式无效") from exc

    raw_origins = document.get("origins", [])
    if not isinstance(raw_origins, list) or len(raw_origins) > MAX_STATE_ORIGINS:
        raise AuthProfileError("登录状态 origin 数量无效")
    origins = []
    local_storage_chars = 0
    for entry in raw_origins:
        if not isinstance(entry, dict) or not isinstance(entry.get("origin"), str):
            raise AuthProfileError("登录状态 origin 格式无效")
        origin = entry["origin"]
        if not _same_origin(origin, target_url):
            continue
        local_storage = entry.get("localStorage", [])
        if not isinstance(local_storage, list) or len(local_storage) > MAX_LOCAL_STORAGE_ITEMS:
            raise AuthProfileError("登录状态 localStorage 数量无效")
        safe_storage = []
        for item in local_storage:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("value"), str):
                raise AuthProfileError("登录状态 localStorage 格式无效")
            if len(item["name"]) > 4096 or len(item["value"]) > 100_000:
                raise AuthProfileError("登录状态 localStorage 项过大")
            local_storage_chars += len(item["name"]) + len(item["value"])
            if local_storage_chars > MAX_LOCAL_STORAGE_CHARS:
                raise AuthProfileError("登录状态 localStorage 过大")
            safe_storage.append({"name": item["name"], "value": item["value"]})
        safe_origin = {"origin": origin, "localStorage": safe_storage}
        if "indexedDB" in entry:
            indexed_db = entry["indexedDB"]
            if not isinstance(indexed_db, list) or len(indexed_db) > 128:
                raise AuthProfileError("登录状态 IndexedDB 格式无效")
            safe_origin["indexedDB"] = indexed_db
        origins.append(safe_origin)

    if not cookies and not origins:
        raise AuthProfileError("未找到适用于该目标域名的登录状态，请重新登录")
    return {"cookies": cookies, "origins": origins}
