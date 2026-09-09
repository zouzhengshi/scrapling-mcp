"""Scrapling - 双引擎网页抓取器，专为 AI Agent 设计。

将 Crawl4AI（快速、AI 友好）和 Scrapling（隐身、适合反爬场景）
整合为一个 AI Agent 可直接调用的工具。
"""

__all__ = ["ScraplingEngine", "scrape"]


def __getattr__(name):
    if name in __all__:
        from src.engine import ScraplingEngine, scrape
        return {"ScraplingEngine": ScraplingEngine, "scrape": scrape}[name]
    raise AttributeError(name)
