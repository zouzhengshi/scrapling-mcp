"""Interactive local login profiles for a small set of known sites."""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Any
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

AUTH_COOKIE_MARKERS = {
    "bilibili": {"SESSDATA", "bili_jct", "DedeUserID"},
    "github": {"user_session"},
    "zhihu": {"z_c0"},
    "weibo": {"SUB", "SUBP"},
    "xiaohongshu": {"web_session"},
}

SUPPORTED_SITES = tuple(SITE_PRESETS)
MAX_STATE_BYTES = 10_000_000
MAX_STATE_COOKIES = 512
MAX_STATE_ORIGINS = 64
MAX_LOCAL_STORAGE_ITEMS = 2000
MAX_LOCAL_STORAGE_CHARS = 1_000_000


@dataclass
class LoginSession:
    site: str
    destination: Path
    proxy: Any
    playwright: Any
    context: Any
    browser_dir: Any
    deadline: float
    monitor: asyncio.Task | None = None


_sessions: dict[str, LoginSession] = {}

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


def _state_has_authenticated_entries(site: str, document: dict) -> bool:
    markers = AUTH_COOKIE_MARKERS.get(site, set())
    now = time.time()
    for cookie in document.get("cookies", []):
        if not isinstance(cookie, dict) or cookie.get("name") not in markers:
            continue
        if not isinstance(cookie.get("value"), str) or not cookie["value"]:
            continue
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and expires <= now:
            continue
        return True
    return False


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


def _login_result(site: str, status: str, ready: bool, message: str) -> dict:
    return {"success": True, "site": site, "auth_profile": site,
            "status": status, "ready": ready, "message": message}


async def _close_login_session(session: LoginSession, *, save: bool) -> None:
    current = asyncio.current_task()
    if session.monitor and session.monitor is not current:
        session.monitor.cancel()
        await asyncio.gather(session.monitor, return_exceptions=True)
    save_error = None
    save_cause = None
    if save and not session.context.is_closed():
        try:
            await _save_state(session.context, session.destination)
        except Exception as exc:
            save_error = AuthProfileError("登录状态保存失败")
            save_cause = exc
    if not session.context.is_closed():
        with contextlib.suppress(Exception):
            await session.context.close()
    try:
        with contextlib.suppress(Exception):
            await session.playwright.stop()
    finally:
        with contextlib.suppress(Exception):
            await session.proxy.__aexit__(None, None, None)
        with contextlib.suppress(Exception):
            session.browser_dir.cleanup()
        _sessions.pop(session.site, None)
    if save_error:
        raise save_error from save_cause


async def _monitor_login_session(session: LoginSession) -> None:
    timed_out = False
    try:
        while _sessions.get(session.site) is session:
            if session.context.is_closed() or not session.context.pages:
                break
            remaining = session.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                await _save_state(session.context, session.destination)
            except Exception:
                # Keep the visible login window alive; status/finalize will
                # report a safe error if the state cannot be persisted.
                logger.error("interactive login state snapshot failed site=%s", session.site)
            await asyncio.sleep(min(1, max(0.1, remaining)))
    except asyncio.CancelledError:
        raise
    finally:
        if (_sessions.get(session.site) is session and
                (session.context.is_closed() or not session.context.pages or timed_out)):
            await _close_login_session(session, save=timed_out and not session.context.is_closed())


async def start_login(site: str, timeout: float = 300.0,
                      auth_dir: str | os.PathLike | None = None,
                      force: bool = False) -> dict:
    """Open a visible login browser and return without waiting for the user."""
    preset = get_site_preset(site)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 10 <= timeout <= 600:
        raise AuthProfileError("登录等待时间必须在 10–600 秒之间")
    existing = _sessions.get(site)
    if existing and not force and not existing.context.is_closed():
        return _login_result(site, "already_running", False,
                             "该网站的登录窗口已经打开，请完成登录后调用 login_status 并设置 finalize=true。")
    if existing:
        await _close_login_session(existing, save=True)
    destination = _state_path(site, auth_dir)
    with contextlib.suppress(AuthProfileError):
        if _state_has_authenticated_entries(site, _read_state(destination)) and not force:
            return _login_result(site, "ready", True,
                                 "本机已有有效登录状态，无需重复打开登录窗口。")
    login_url = normalize_url(preset.login_url)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise AuthProfileError("未安装 Playwright，无法打开登录浏览器") from exc

    logger.info("interactive login started site=%s; use login_status(finalize=true) after login", site)
    proxy = None
    playwright = None
    browser_dir = None
    context = None
    try:
        proxy = EgressProxy(max_bytes=50_000_000)
        await proxy.__aenter__()
        playwright = await async_playwright().start()
        browser_dir = tempfile.TemporaryDirectory(prefix="scrapling-login-")
        context = await playwright.chromium.launch_persistent_context(
            browser_dir.name,
            headless=False,
            proxy=proxy.browser_proxy,
            args=LOGIN_BROWSER_FLAGS,
            ignore_https_errors=False,
            accept_downloads=False,
            service_workers="allow",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
        session = LoginSession(site, destination, proxy, playwright, context, browser_dir,
                               asyncio.get_running_loop().time() + float(timeout))
        _sessions[site] = session
        await _save_state(context, destination)
        session.monitor = asyncio.create_task(_monitor_login_session(session))
        return _login_result(site, "waiting", False,
                             "登录浏览器已打开。请完成登录；完成后调用 login_status，并设置 finalize=true。")
    except AuthProfileError:
        if context is not None and not context.is_closed():
            await context.close()
        if playwright is not None:
            await playwright.stop()
        if proxy is not None:
            await proxy.__aexit__(None, None, None)
        if browser_dir is not None:
            browser_dir.cleanup()
        raise
    except Exception as exc:
        text = str(exc).lower()
        if context is not None and not context.is_closed():
            await context.close()
        if playwright is not None:
            await playwright.stop()
        if proxy is not None:
            await proxy.__aexit__(None, None, None)
        if browser_dir is not None:
            browser_dir.cleanup()
        if "executable doesn't exist" in text or "browser was not found" in text:
            raise AuthProfileError("未安装 Chromium，请按 README 安装浏览器") from exc
        raise AuthProfileError("登录浏览器启动或访问失败") from exc


async def finish_login(site: str, auth_dir: str | os.PathLike | None = None) -> dict:
    """Save and close an active login window, then mark the profile ready."""
    get_site_preset(site)
    session = _sessions.get(site)
    if session:
        await _close_login_session(session, save=not session.context.is_closed())
    document = _read_state(_state_path(site, auth_dir))
    if not _state_has_authenticated_entries(site, document):
        raise AuthProfileError("未检测到有效的登录状态；请在弹出的浏览器中完成登录后再确认")
    return _login_result(site, "ready", True,
                         "登录状态已保存在本机。后续抓取请使用相同的 auth_profile 名称。")


async def login_status(site: str, auth_dir: str | os.PathLike | None = None,
                       finalize: bool = False) -> dict:
    """Report login progress; finalize=True closes the browser and saves state."""
    get_site_preset(site)
    if finalize:
        return await finish_login(site, auth_dir)
    session = _sessions.get(site)
    if session and not session.context.is_closed() and session.context.pages:
        try:
            await _save_state(session.context, session.destination)
        except Exception as exc:
            raise AuthProfileError("登录状态保存失败") from exc
        return _login_result(site, "waiting", False,
                             "登录窗口仍在运行。完成登录后再次调用 login_status，并设置 finalize=true。")
    document = _read_state(_state_path(site, auth_dir))
    if _state_has_authenticated_entries(site, document):
        return _login_result(site, "ready", True,
                             "已找到本机登录状态；抓取时使用相同的 auth_profile 名称。")
    raise AuthProfileError("尚未检测到有效登录状态，请在登录窗口中完成登录后再确认")


async def interactive_login(site: str, timeout: float = 300.0,
                            auth_dir: str | os.PathLike | None = None) -> dict:
    """Compatibility helper for callers that intentionally want a blocking flow."""
    result = await start_login(site, timeout, auth_dir)
    if result["status"] == "already_running":
        return result
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(timeout)
    while loop.time() < deadline and site in _sessions:
        await asyncio.sleep(1)
    return await finish_login(site, auth_dir)


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
    if site == "bilibili" and not _state_has_authenticated_entries(site, document):
        raise AuthProfileError("未检测到 Bilibili 有效登录状态，请先完成登录")
    return {"cookies": cookies, "origins": origins}
