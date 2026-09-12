"""Safe, lightweight update checks for the local CLI.

The checker only reads the public latest-release endpoint for this repository.
It never downloads code, runs an installer, sends credentials, or participates
in the MCP stdio process. A small local cache keeps offline starts fast.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from src.config import config_path


LATEST_RELEASE_API = "https://api.github.com/repos/zouzhengshi/scrapling-mcp/releases/latest"
RELEASES_BASE_URL = "https://github.com/zouzhengshi/scrapling-mcp/releases/tag/"
DEFAULT_CACHE_TTL = 24 * 60 * 60
MIN_CACHE_TTL = 5 * 60
MAX_CACHE_TTL = 7 * 24 * 60 * 60
MAX_CACHE_BYTES = 32_000
MAX_RESPONSE_BYTES = 256_000
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _cache_ttl() -> float:
    raw = os.environ.get("SCRAPLING_UPDATE_CACHE_TTL", str(DEFAULT_CACHE_TTL)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CACHE_TTL
    if not math.isfinite(value):
        return DEFAULT_CACHE_TTL
    return min(MAX_CACHE_TTL, max(MIN_CACHE_TTL, value))


def _disabled() -> bool:
    return os.environ.get("SCRAPLING_UPDATE_CHECK", "auto").strip().lower() in {
        "0", "false", "no", "off", "disable", "disabled",
    }


def cache_path() -> Path:
    raw = os.environ.get("SCRAPLING_UPDATE_CACHE_FILE")
    if raw:
        return Path(raw).expanduser()
    return config_path().parent / "update-check.json"


def _read_cache() -> dict[str, Any] | None:
    path = cache_path()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("checked_at"), (int, float)):
        return None
    return data


def _write_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            return
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".scrapling-update-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        if temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _release_url(tag: str, supplied: Any) -> str:
    # Do not trust a redirect URL returned by the API. Keep the link on the
    # repository's canonical GitHub host and only interpolate a valid tag.
    if isinstance(supplied, str):
        parsed = urlsplit(supplied)
        if parsed.scheme == "https" and parsed.netloc.casefold() == "github.com":
            prefix = "/zouzhengshi/scrapling-mcp/releases/tag/"
            if parsed.path.startswith(prefix) and parsed.query == "" and parsed.fragment == "":
                return supplied
    return RELEASES_BASE_URL + quote(tag, safe="v.-_+")


def _fetch_latest() -> dict[str, Any]:
    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "scrapling-mcp-update-check",
        },
    )
    with urlopen(request, timeout=3.0) as response:
        if getattr(response, "status", response.getcode()) != 200:
            raise URLError("GitHub release endpoint returned a non-success status")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("release response too large")
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("release response format invalid")
    tag = document.get("tag_name")
    if not isinstance(tag, str) or _version_tuple(tag) is None:
        raise ValueError("release version invalid")
    version = tag[1:] if tag.startswith("v") else tag
    return {
        "latest_version": version,
        "release_url": _release_url(tag, document.get("html_url")),
    }


def _result(current_version: str, status: str, *, cached: bool = False,
            latest_version: str | None = None, release_url: str | None = None,
            checked_at: float | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": status,
        "current_version": current_version,
        "latest_version": latest_version,
        "release_url": release_url,
        "cached": cached,
    }
    if checked_at is not None:
        data["checked_at"] = checked_at
    return data


def _from_cache(current_version: str, data: dict[str, Any]) -> dict[str, Any] | None:
    checked_at = data.get("checked_at")
    if not isinstance(checked_at, (int, float)) or time.time() - float(checked_at) >= _cache_ttl():
        return None
    latest = data.get("latest_version")
    latest_tuple = _version_tuple(latest) if isinstance(latest, str) else None
    current_tuple = _version_tuple(current_version)
    if current_tuple is None or latest_tuple is None:
        return _result(current_version, "unavailable", cached=True, checked_at=float(checked_at))
    status = "update_available" if latest_tuple > current_tuple else "up_to_date"
    return _result(
        current_version, status, cached=True, latest_version=latest,
        release_url=data.get("release_url"), checked_at=float(checked_at),
    )


def check_for_update(current_version: str, *, force: bool = False) -> dict[str, Any]:
    """Return update state without ever raising a network error to the CLI."""
    if _disabled():
        return _result(current_version, "disabled")
    if _version_tuple(current_version) is None:
        return _result(current_version, "unavailable")
    if not force:
        cached = _read_cache()
        if cached:
            cached_result = _from_cache(current_version, cached)
            if cached_result:
                return cached_result
    checked_at = time.time()
    try:
        latest = _fetch_latest()
    except Exception:
        # An update check must never make scraping, login, or diagnostics fail.
        _write_cache({"checked_at": checked_at, "latest_version": current_version})
        return _result(current_version, "unavailable", checked_at=checked_at)
    latest_version = latest["latest_version"]
    current_tuple = _version_tuple(current_version)
    latest_tuple = _version_tuple(latest_version)
    status = "update_available" if latest_tuple and current_tuple and latest_tuple > current_tuple else "up_to_date"
    _write_cache({"checked_at": checked_at, **latest})
    return _result(
        current_version, status, latest_version=latest_version,
        release_url=latest["release_url"], checked_at=checked_at,
    )


def human_message(data: dict[str, Any]) -> str | None:
    """Return a user-facing message only when an update is available."""
    if data.get("status") != "update_available":
        return None
    return (f"🎉 发现新版本 Scrapling MCP {data.get('latest_version')} "
            f"（当前 {data.get('current_version')}），请查看：{data.get('release_url')}")

