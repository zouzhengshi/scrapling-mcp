"""Local, non-secret configuration for the Scrapling MCP service.

The configuration intentionally contains only feature switches.  Cookie values
and browser login state live in their existing, separate files and are never
copied into this file or returned by the terminal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


TOOL_NAMES = (
    "login",
    "login_status",
    "login_custom",
    "login_custom_status",
    "scrape",
    "scrape_batch",
)

TOOL_DESCRIPTIONS = {
    "login": "预设网站交互式登录",
    "login_status": "查询预设网站登录状态",
    "login_custom": "自定义网站交互式登录",
    "login_custom_status": "查询自定义网站登录状态",
    "scrape": "单页网页抓取",
    "scrape_batch": "批量网页抓取",
}

MAX_CONFIG_BYTES = 256_000


class ConfigError(ValueError):
    """The local management configuration is missing or malformed."""


def _default_config() -> dict[str, Any]:
    return {"version": 1, "enabled_tools": {name: True for name in TOOL_NAMES}}


def config_path() -> Path:
    """Return the configured local path without creating or reading it."""
    raw = os.environ.get("SCRAPLING_CONFIG_FILE")
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return root / "ScraplingMCP" / "config.json"
    return Path.home() / ".config" / "scrapling-mcp" / "config.json"


def _check_path(path: Path) -> Path:
    if path.is_symlink():
        raise ConfigError("配置文件不能是符号链接")
    return path


def _normalise(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ConfigError("配置文件格式无效")
    enabled = document.get("enabled_tools", {})
    if not isinstance(enabled, dict):
        raise ConfigError("enabled_tools 必须是对象")
    result = _default_config()
    for name in TOOL_NAMES:
        value = enabled.get(name, True)
        if not isinstance(value, bool):
            raise ConfigError(f"工具开关 {name} 必须是布尔值")
        result["enabled_tools"][name] = value
    return result


def load_config() -> dict[str, Any]:
    """Load the switches, using all-enabled defaults when no file exists."""
    path = _check_path(config_path())
    if not path.exists():
        return _default_config()
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigError("配置文件不存在、不是普通文件或过大")
        document = json.loads(path.read_text(encoding="utf-8"))
    except ConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("配置文件无法读取或不是有效 JSON") from exc
    return _normalise(document)


def save_config(document: dict[str, Any]) -> Path:
    """Atomically save the non-secret configuration and return its path."""
    path = _check_path(config_path())
    normalised = _normalise(document)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise ConfigError("配置目录不能是符号链接")
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".scrapling-config-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(normalised, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except ConfigError:
        raise
    except OSError as exc:
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise ConfigError("配置文件无法保存") from exc
    return path


def tool_states() -> dict[str, bool]:
    """Return a stable mapping of every known MCP tool to its enabled state."""
    return dict(load_config()["enabled_tools"])


def is_tool_enabled(name: str) -> bool:
    if name not in TOOL_NAMES:
        raise ConfigError("未知 MCP 工具")
    return tool_states()[name]


def set_tool_enabled(name: str, enabled: bool) -> Path:
    if name not in TOOL_NAMES:
        raise ConfigError(f"未知 MCP 工具：{name}")
    if not isinstance(enabled, bool):
        raise ConfigError("工具开关必须是布尔值")
    document = load_config()
    document["enabled_tools"][name] = enabled
    return save_config(document)
