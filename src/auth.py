"""Interactive local login profiles for preset and user-approved sites."""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    auth_cookie_markers: frozenset[str] = frozenset()


SITE_PRESETS = {
    "bilibili": SitePreset(
        "https://passport.bilibili.com/login", ("bilibili.com",),
        frozenset({"SESSDATA", "bili_jct", "DedeUserID"})),
    "youtube": SitePreset(
        "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F",
        ("youtube.com", "google.com"), frozenset({"SID", "SAPISID", "LOGIN_INFO"})),
    "github": SitePreset(
        "https://github.com/login", ("github.com",), frozenset({"user_session"})),
    "zhihu": SitePreset(
        "https://www.zhihu.com/signin", ("zhihu.com",), frozenset({"z_c0"})),
    "weibo": SitePreset(
        "https://passport.weibo.com/", ("weibo.com", "weibo.cn"),
        frozenset({"SUB", "SUBP"})),
    "xiaohongshu": SitePreset(
        "https://www.xiaohongshu.com/", ("xiaohongshu.com",), frozenset({"web_session"})),
}

# Compatibility export for callers that used the old mapping.  The registry
# above is the source of truth, so adding a preset cannot silently forget the
# corresponding authenticated-cookie check.
AUTH_COOKIE_MARKERS = {
    site: set(preset.auth_cookie_markers) for site, preset in SITE_PRESETS.items()
}

SUPPORTED_SITES = tuple(SITE_PRESETS)
MAX_STATE_BYTES = 10_000_000
MAX_STATE_COOKIES = 512
MAX_STATE_ORIGINS = 64
MAX_LOCAL_STORAGE_ITEMS = 2000
MAX_LOCAL_STORAGE_CHARS = 1_000_000
LOGIN_CLEANUP_TIMEOUT = 5.0
MAX_CUSTOM_DOMAINS = 16
AUTH_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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
    allowed_domains: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


_sessions: dict[str, LoginSession] = {}

LOGIN_BROWSER_FLAGS = [
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
    # The local proxy wraps an upstream HTTP proxy; HTTP/1.1 is more reliable
    # than Chromium HTTP/2 for this nested CONNECT path.
    "--disable-http2",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
]


def _login_browser_api():
    """Load the browser driver used for interactive login.

    Patchright is preferred because Google may reject Playwright's bundled
    Chromium during an interactive sign-in.  ``playwright`` remains an
    explicit fallback for sites that do not need this compatibility mode.
    """
    requested = os.environ.get("SCRAPLING_LOGIN_BROWSER", "auto").strip().lower()
    if requested not in {"auto", "patchright", "playwright"}:
        raise AuthProfileError("SCRAPLING_LOGIN_BROWSER 只能是 auto、patchright 或 playwright")
    if requested in {"auto", "patchright"}:
        try:
            from patchright.async_api import TimeoutError as BrowserTimeoutError
            from patchright.async_api import async_playwright
            return BrowserTimeoutError, async_playwright, "patchright"
        except ImportError as exc:
            if requested == "patchright":
                raise AuthProfileError("未安装 Patchright，请执行 python -m pip install patchright") from exc
    try:
        from playwright.async_api import TimeoutError as BrowserTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise AuthProfileError("未安装 Playwright，无法打开登录浏览器") from exc
    return BrowserTimeoutError, async_playwright, "playwright"


def _login_browser_channel(driver: str) -> str | None:
    """Return a safe browser channel override for interactive login."""
    value = os.environ.get("SCRAPLING_LOGIN_CHANNEL", "chrome").strip().lower()
    if value in {"", "default", "none"}:
        return None
    if value not in {"chrome", "chromium"}:
        raise AuthProfileError("SCRAPLING_LOGIN_CHANNEL 只能是 chrome、chromium 或留空")
    # Real Chrome is the default compatibility path for Patchright.  A
    # Playwright fallback still uses its normal bundled browser unless the
    # operator explicitly asks for a channel.
    return value if driver == "patchright" or "SCRAPLING_LOGIN_CHANNEL" in os.environ else None


def get_site_preset(site: str) -> SitePreset:
    if not isinstance(site, str) or site not in SITE_PRESETS:
        choices = "、".join(SUPPORTED_SITES)
        raise AuthProfileError(f"不支持的网站预设，可选：{choices}")
    return SITE_PRESETS[site]


def _validate_profile_name(profile: str) -> str:
    if not isinstance(profile, str) or not AUTH_PROFILE_NAME.fullmatch(profile):
        raise AuthProfileError("auth_profile 名称无效")
    return profile


def _normalise_allowed_domains(values, *, field="allowed_domains") -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_CUSTOM_DOMAINS:
        raise AuthProfileError(f"{field} 必须包含 1–{MAX_CUSTOM_DOMAINS} 个域名")
    result = []
    for value in values:
        if not isinstance(value, str) or value.startswith("*"):
            raise AuthProfileError(f"{field} 只能包含明确域名，不支持通配符")
        try:
            domain = _domain(value, field)
        except CookieProfileError as exc:
            raise AuthProfileError(f"{field} 包含无效域名") from exc
        if "." not in domain or domain.endswith((".local", ".internal", ".localhost", ".localdomain")):
            raise AuthProfileError(f"{field} 必须是公网域名")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise AuthProfileError(f"{field} 不支持 IP 地址")
        if domain not in result:
            result.append(domain)
    return tuple(result)


def _domain_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    return any(_host_matches(host, domain) for domain in allowed_domains)


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
    return _auth_root(auth_dir) / f"{_validate_profile_name(site)}.state.json"


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


def _custom_metadata(profile: str, document: dict) -> dict:
    raw = document.get("scrapling_auth")
    if not isinstance(raw, dict) or raw.get("kind") != "custom" or raw.get("profile") != profile:
        raise AuthProfileError("自定义登录配置无效，请重新登录")
    allowed_domains = _normalise_allowed_domains(raw.get("allowed_domains"))
    login_url = raw.get("login_url")
    try:
        login_url = normalize_url(login_url)
    except (TypeError, ValueError) as exc:
        raise AuthProfileError("自定义登录网址无效") from exc
    if urlsplit(login_url).scheme != "https":
        raise AuthProfileError("自定义登录只允许 HTTPS 登录网址")
    parts = urlsplit(login_url)
    safe_login_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    return {"kind": "custom", "profile": profile,
            "login_url": safe_login_url, "allowed_domains": allowed_domains}


def _profile_allowed_domains(profile: str, document: dict | None = None,
                             auth_dir: str | os.PathLike | None = None) -> tuple[str, ...]:
    if profile in SITE_PRESETS:
        return SITE_PRESETS[profile].allowed_domains
    if document is None:
        document = _read_state(_state_path(profile, auth_dir))
    return _custom_metadata(profile, document)["allowed_domains"]


def _state_is_ready(profile: str, document: dict) -> bool:
    if profile in AUTH_COOKIE_MARKERS:
        return _state_has_authenticated_entries(profile, document)
    try:
        _custom_metadata(profile, document)
    except AuthProfileError:
        return False
    return _state_has_entries(document)


def _filter_state(document: dict, allowed_domains: tuple[str, ...]) -> dict:
    safe = dict(document)
    cookies = []
    for cookie in document.get("cookies", []):
        if not isinstance(cookie, dict) or not isinstance(cookie.get("domain"), str):
            continue
        try:
            domain = _domain(cookie["domain"], "Cookie domain")
        except CookieProfileError:
            continue
        if _domain_allowed(domain, allowed_domains):
            cookies.append(cookie)
    origins = []
    for origin in document.get("origins", []):
        if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
            continue
        try:
            host = _domain(urlsplit(origin["origin"]).hostname, "origin")
        except (CookieProfileError, TypeError, ValueError, UnicodeError):
            continue
        if _domain_allowed(host, allowed_domains):
            origins.append(origin)
    safe["cookies"] = cookies
    safe["origins"] = origins
    return safe


def _state_has_authenticated_entries(site: str, document: dict) -> bool:
    preset = SITE_PRESETS.get(site)
    markers = preset.auth_cookie_markers if preset else frozenset()
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


async def _save_state(context, destination: Path, *,
                      allowed_domains: tuple[str, ...] = (),
                      metadata: dict | None = None) -> None:
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        await context.storage_state(path=str(temporary), indexed_db=True)
        if allowed_domains or metadata:
            document = json.loads(temporary.read_text(encoding="utf-8"))
            if allowed_domains:
                document = _filter_state(document, allowed_domains)
            if metadata:
                document["scrapling_auth"] = metadata
            temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
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
    is_preset = site in SITE_PRESETS
    if ready:
        next_action = f"抓取需要登录的网页时使用 auth_profile=\"{site}\""
    elif is_preset:
        next_action = "用户完成登录后调用 login_status(site, finalize=true)；不要重复调用 login"
    else:
        next_action = "用户完成登录后调用 login_custom_status(auth_profile, finalize=true)；不要重复调用 login_custom"
    return {"success": True, "site": site, "auth_profile": site,
            "status": status, "ready": ready, "message": message,
            "next_action": next_action}


async def _close_login_session(session: LoginSession, *, save: bool) -> None:
    current = asyncio.current_task()
    if session.monitor and session.monitor is not current:
        session.monitor.cancel()
        await asyncio.gather(session.monitor, return_exceptions=True)
    save_error = None
    save_cause = None
    if save and not session.context.is_closed():
        try:
            await _save_state(session.context, session.destination,
                              allowed_domains=session.allowed_domains,
                              metadata=session.metadata)
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


def _log_login_cleanup(task: asyncio.Task) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        try:
            task.result()
        except Exception:
            logger.exception("interactive login cleanup failed")


async def _finalize_login_session(session: LoginSession) -> None:
    """Close a login session without letting browser cleanup block MCP calls."""
    _sessions.pop(session.site, None)
    cleanup = asyncio.create_task(
        _close_login_session(session, save=not session.context.is_closed()))
    done, _ = await asyncio.wait({cleanup}, timeout=LOGIN_CLEANUP_TIMEOUT)
    if done:
        await cleanup
        return
    cleanup.add_done_callback(_log_login_cleanup)
    logger.warning("interactive login cleanup still running site=%s", session.site)


async def _monitor_login_session(session: LoginSession) -> None:
    timed_out = False
    try:
        while _sessions.get(session.site) is session:
            # Google may briefly replace or detach the current page during
            # the email -> password redirect.  An empty pages snapshot is not
            # proof that the user finished or abandoned the login.
            if session.context.is_closed():
                break
            remaining = session.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                await _save_state(session.context, session.destination,
                                  allowed_domains=session.allowed_domains,
                                  metadata=session.metadata)
            except Exception:
                # Keep the visible login window alive; status/finalize will
                # report a safe error if the state cannot be persisted.
                logger.error("interactive login state snapshot failed site=%s", session.site)
            await asyncio.sleep(min(1, max(0.1, remaining)))
    except asyncio.CancelledError:
        raise
    finally:
        if (_sessions.get(session.site) is session and
                (session.context.is_closed() or timed_out)):
            await _close_login_session(session, save=timed_out and not session.context.is_closed())


def _custom_login_metadata(profile: str, login_url: str,
                           allowed_domains: tuple[str, ...]) -> dict:
    parts = urlsplit(login_url)
    safe_login_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    return {"kind": "custom", "profile": profile, "login_url": safe_login_url,
            "allowed_domains": list(allowed_domains)}


async def _start_login(profile: str, login_url: str,
                       allowed_domains: tuple[str, ...], timeout: float,
                       auth_dir: str | os.PathLike | None, force: bool,
                       metadata: dict | None = None) -> dict:
    """Open a visible login browser and return without waiting for the user."""
    _validate_profile_name(profile)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 10 <= timeout <= 600:
        raise AuthProfileError("登录等待时间必须在 10–600 秒之间")
    existing = _sessions.get(profile)
    if existing and not force and not existing.context.is_closed():
        return _login_result(profile, "already_running", False,
                             "该网站的登录窗口已经打开，请完成登录后调用 login_status 并设置 finalize=true。")
    if existing:
        await _finalize_login_session(existing)
    destination = _state_path(profile, auth_dir)
    with contextlib.suppress(AuthProfileError):
        if _state_is_ready(profile, _read_state(destination)) and not force:
            return _login_result(profile, "ready", True,
                                 "本机已有有效登录状态，无需重复打开登录窗口。")
    login_url = normalize_url(login_url)
    try:
        BrowserTimeoutError, async_playwright, browser_driver = _login_browser_api()
        browser_channel = _login_browser_channel(browser_driver)
    except AuthProfileError:
        raise

    logger.info("interactive login started profile=%s; use login_status(finalize=true) after login", profile)
    proxy = None
    playwright = None
    browser_dir = None
    context = None
    try:
        proxy = EgressProxy(max_bytes=50_000_000)
        await proxy.__aenter__()
        playwright = await async_playwright().start()
        browser_dir = tempfile.TemporaryDirectory(prefix="scrapling-login-")
        launch_options = {
            "headless": False,
            "proxy": proxy.browser_proxy,
            "args": LOGIN_BROWSER_FLAGS,
            "ignore_https_errors": False,
            "accept_downloads": False,
            "service_workers": "allow",
        }
        if browser_driver == "patchright":
            # Use the installed Google Chrome binary when available. This is
            # materially different from launching bundled Playwright Chromium
            # for Google's sign-in risk checks.
            launch_options.update({"no_viewport": True})
        else:
            launch_options.update({"ignore_default_args": ["--enable-automation"]})
        if browser_channel:
            launch_options["channel"] = browser_channel
        context = await playwright.chromium.launch_persistent_context(
            browser_dir.name, **launch_options,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        navigation_pending = False
        try:
            # Login pages may keep subresources open behind a proxy. The user
            # only needs a usable window, so commit is enough to begin login.
            await page.goto(login_url, wait_until="commit", timeout=20_000)
        except BrowserTimeoutError:
            # Keep the visible context alive; Chromium may still finish loading
            # after the initial navigation budget and the user can interact.
            navigation_pending = True
            logger.warning("interactive login navigation still loading profile=%s", profile)
        session = LoginSession(
            profile, destination, proxy, playwright, context, browser_dir,
            asyncio.get_running_loop().time() + float(timeout),
            allowed_domains=allowed_domains, metadata=metadata,
        )
        _sessions[profile] = session
        await _save_state(context, destination, allowed_domains=allowed_domains,
                          metadata=metadata)
        session.monitor = asyncio.create_task(_monitor_login_session(session))
        message = "登录浏览器已打开。请完成登录；完成后调用 login_status，并设置 finalize=true。"
        if navigation_pending:
            message = "登录浏览器已打开，页面仍在加载中；请等待页面出现后完成登录，再调用 login_status 并设置 finalize=true。"
        return _login_result(profile, "waiting", False, message)
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
        if "err_tunnel_connection_failed" in text:
            raise AuthProfileError("登录页面无法通过当前代理建立连接，请检查代理地址和代理软件是否正在运行") from exc
        if "timeout" in text:
            raise AuthProfileError("登录页面加载超时；请确认 VPN/代理可用后重试") from exc
        raise AuthProfileError("登录浏览器启动或访问失败") from exc


async def start_login(site: str, timeout: float = 300.0,
                      auth_dir: str | os.PathLike | None = None,
                      force: bool = False) -> dict:
    preset = get_site_preset(site)
    return await _start_login(site, preset.login_url, preset.allowed_domains,
                              timeout, auth_dir, force)


async def start_custom_login(profile: str, url: str,
                             allowed_domains=None, timeout: float = 300.0,
                             auth_dir: str | os.PathLike | None = None,
                             force: bool = False) -> dict:
    """Open a user-approved custom HTTPS login/target URL."""
    _validate_profile_name(profile)
    if profile in SITE_PRESETS:
        raise AuthProfileError("自定义 auth_profile 不能使用预设网站名称")
    try:
        login_url = normalize_url(url)
        target = urlsplit(login_url)
        target_host = _domain(target.hostname, "目标域名")
    except (CookieProfileError, TypeError, ValueError, UnicodeError) as exc:
        raise AuthProfileError("自定义登录网址无效") from exc
    if target.scheme != "https":
        raise AuthProfileError("自定义登录只允许 HTTPS 登录网址")
    domains = ((target_host,) if allowed_domains is None
               else _normalise_allowed_domains(allowed_domains))
    if not _domain_allowed(target_host, domains):
        raise AuthProfileError("allowed_domains 必须包含登录网址的域名")
    metadata = _custom_login_metadata(profile, login_url, domains)
    return await _start_login(profile, login_url, domains, timeout, auth_dir,
                              force, metadata)


async def finish_login(site: str, auth_dir: str | os.PathLike | None = None) -> dict:
    """Save and close an active login window, then mark the profile ready."""
    _validate_profile_name(site)
    session = _sessions.get(site)
    destination = _state_path(site, auth_dir)
    if session and not session.context.is_closed():
        # Never close an active browser before checking readiness.  Agents can
        # call finalize early while the user is between Google login steps.
        try:
            await _save_state(session.context, session.destination,
                              allowed_domains=session.allowed_domains,
                              metadata=session.metadata)
        except Exception as exc:
            raise AuthProfileError("登录状态保存失败") from exc
        document = _read_state(destination)
        if not _state_is_ready(site, document):
            raise AuthProfileError("登录尚未完成；登录窗口仍保持打开，请继续完成密码或验证步骤")
        await _finalize_login_session(session)
        # _finalize_login_session saves one final snapshot. Read it again so
        # the result reflects exactly what was persisted before closing.
        document = _read_state(destination)
    else:
        if session:
            _sessions.pop(site, None)
        document = _read_state(destination)
    if not _state_is_ready(site, document):
        raise AuthProfileError("未检测到有效的登录状态；请在弹出的浏览器中完成登录后再确认")
    return _login_result(site, "ready", True,
                         "登录状态已保存在本机。后续抓取请使用相同的 auth_profile 名称。")


async def login_status(site: str, auth_dir: str | os.PathLike | None = None,
                       finalize: bool = False) -> dict:
    """Report login progress; finalize=True closes the browser and saves state."""
    _validate_profile_name(site)
    if finalize:
        return await finish_login(site, auth_dir)
    session = _sessions.get(site)
    if session and not session.context.is_closed():
        try:
            await _save_state(session.context, session.destination,
                              allowed_domains=session.allowed_domains,
                              metadata=session.metadata)
        except Exception as exc:
            raise AuthProfileError("登录状态保存失败") from exc
        return _login_result(site, "waiting", False,
                             "登录窗口仍在运行。完成登录后再次调用 login_status，并设置 finalize=true。")
    document = _read_state(_state_path(site, auth_dir))
    if _state_is_ready(site, document):
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
    _validate_profile_name(site)
    try:
        target = urlsplit(target_url)
        target_host = _domain(target.hostname, "目标域名")
    except (CookieProfileError, TypeError, ValueError, UnicodeError) as exc:
        raise AuthProfileError("目标域名无效") from exc
    document = _read_state(_state_path(site, auth_dir))
    allowed_domains = _profile_allowed_domains(site, document, auth_dir)
    if not _domain_allowed(target_host, allowed_domains):
        raise AuthProfileError("auth_profile 未授权该目标域名")

    raw_cookies = document.get("cookies", [])
    if not isinstance(raw_cookies, list) or len(raw_cookies) > MAX_STATE_COOKIES:
        raise AuthProfileError("登录状态 Cookie 数量无效")
    cookies = []
    try:
        allowed_domains = tuple(_domain(domain, "allowed_domains") for domain in allowed_domains)
        for entry in raw_cookies:
            # Keep cookies for every explicitly approved login domain. The
            # browser still enforces each cookie's own domain/hostOnly scope;
            # this is needed for YouTube's Google sign-in redirect.
            cookie = _normalise_cookie(
                entry, target_host, target.scheme.lower(), allowed_domains,
                allow_cross_domain=True,
            )
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
    if site in AUTH_COOKIE_MARKERS and not _state_has_authenticated_entries(site, document):
        raise AuthProfileError("未检测到有效登录状态，请先完成登录")
    return {"cookies": cookies, "origins": origins}
