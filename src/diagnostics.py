"""Read-only installation checks; no network or browser windows."""
import importlib.metadata
import sys
from pathlib import Path

from src.config import ConfigError, runtime_options


def check():
    dependencies = {}
    for package in ("crawl4ai", "scrapling", "mcp", "pydantic", "html2text", "lxml",
                    "cssselect", "psutil", "patchright"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    browsers = {}
    for package in ("playwright", "patchright"):
        try:
            module = __import__(package + ".sync_api", fromlist=["sync_playwright"])
            with module.sync_playwright() as runtime:
                executable = Path(runtime.chromium.executable_path)
                browsers[package] = {"installed": executable.is_file(), "executable": str(executable)}
        except Exception:
            browsers[package] = {"installed": False}
    try:
        runtime_options()
        configuration = {"ready": True}
    except ConfigError as exc:
        configuration = {"ready": False, "error": str(exc)}
    ready = (sys.version_info >= (3, 11) and all(dependencies.values())
             and all(item["installed"] for item in browsers.values())
             and configuration["ready"])
    return {"ready": ready, "python": sys.version.split()[0],
            "dependencies": dependencies, "browsers": browsers,
            "configuration": configuration}
