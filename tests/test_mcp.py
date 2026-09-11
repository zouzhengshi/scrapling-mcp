"""Exercise initialize/list/call/ping on the actual stdio wire protocol."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from src.models import ScrapeResult
from src.mcp_server import scrape, scrape_batch


class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_and_text_results_agree(self):
        result = ScrapeResult("https://example.com/", "crawl4ai", "# 中文", True)
        engine = unittest.mock.Mock()
        engine.scrape = AsyncMock(return_value=result)
        with patch("src.mcp_server._获取引擎", return_value=engine):
            reply = await scrape("https://example.com/", cookie_profile="demo")
        self.assertEqual(json.loads(reply.content[0].text), reply.structuredContent)
        self.assertFalse(reply.isError)
        self.assertTrue(reply.structuredContent["content_is_untrusted"])
        self.assertEqual(reply.structuredContent["schema_version"], "1.1")
        self.assertIn("summary", reply.structuredContent)
        self.assertEqual(engine.scrape.await_args.kwargs["cookie_profile"], "demo")

    async def test_auth_profile_is_forwarded(self):
        result = ScrapeResult("https://github.com/", "crawl4ai", "# GitHub", True)
        engine = unittest.mock.Mock()
        engine.scrape = AsyncMock(return_value=result)
        with patch("src.mcp_server._获取引擎", return_value=engine):
            reply = await scrape("https://github.com/", auth_profile="github")
        self.assertFalse(reply.isError)
        self.assertEqual(engine.scrape.await_args.kwargs["auth_profile"], "github")

    async def test_disabled_tool_returns_structured_marker_without_running_engine(self):
        engine = unittest.mock.Mock()
        engine.scrape = AsyncMock()
        with patch("src.mcp_server.is_tool_enabled", return_value=False), \
                patch("src.mcp_server._获取引擎", return_value=engine):
            reply = await scrape("https://example.com/")
        self.assertTrue(reply.isError)
        self.assertEqual(reply.structuredContent["error_code"], "TOOL_DISABLED")
        engine.scrape.assert_not_awaited()

    async def test_batch_preserves_order_and_partial_failure(self):
        engine = unittest.mock.Mock()
        engine.scrape = AsyncMock(side_effect=[
            ScrapeResult("https://one.test/", "crawl4ai", "first", True),
            ScrapeResult("https://two.test/", "crawl4ai", "", False, error_code="HTTP_ERROR"),
        ])
        with patch("src.mcp_server._获取引擎", return_value=engine):
            reply = await scrape_batch(["https://one.test/", "https://two.test/"])
        self.assertFalse(reply.isError)
        self.assertEqual(reply.structuredContent["failed"], 1)
        self.assertEqual(reply.structuredContent["message"], "批量抓取完成：成功 1 个，失败 1 个")
        self.assertEqual([r["url"] for r in reply.structuredContent["results"]],
                         ["https://one.test/", "https://two.test/"])

    async def test_real_stdio_schema_validation_and_error_marker(self):
        root = Path(__file__).resolve().parent.parent
        parameters = StdioServerParameters(command=sys.executable, args=[str(root / "main.py")],
                                           env={"PYTHONIOENCODING": "utf-8"})
        # Cold imports and process startup are noticeably slower on hosted
        # runners, especially on Windows. Keep the local test fast while
        # giving CI enough room to exercise the real stdio protocol.
        startup_timeout = 45 if os.environ.get("CI") else 15
        async with asyncio.timeout(startup_timeout):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialize_result = await session.initialize()
                    self.assertIn("scrape", initialize_result.instructions)
                    self.assertIn("login_custom", initialize_result.instructions)
                    self.assertIn("Cookie 原文", initialize_result.instructions)
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    self.assertEqual(set(tools), {
                        "login", "login_status", "login_custom", "login_custom_status",
                        "scrape", "scrape_batch",
                    })
                    self.assertIn("error_code", tools["scrape"].outputSchema["properties"])
                    self.assertIn("summary", tools["scrape"].outputSchema["properties"])
                    self.assertIn("message", tools["scrape_batch"].outputSchema["properties"])
                    self.assertIn("cookie_profile", tools["scrape"].inputSchema["properties"])
                    self.assertIn("auth_profile", tools["scrape"].inputSchema["properties"])
                    self.assertEqual(tools["login"].inputSchema["properties"]["site"]["enum"],
                                     ["bilibili", "youtube", "github", "zhihu", "weibo", "xiaohongshu"])
                    self.assertIn("finalize", tools["login_status"].inputSchema["properties"])
                    self.assertIn("url", tools["login_custom"].inputSchema["properties"])
                    self.assertIn("allowed_domains", tools["login_custom"].inputSchema["properties"])
                    self.assertIn("finalize", tools["login_custom_status"].inputSchema["properties"])
                    denied, ping = await asyncio.gather(
                        session.call_tool("scrape", {"url": "http://127.0.0.1/"}), session.send_ping())
                    self.assertTrue(denied.isError)
                    self.assertEqual(denied.structuredContent["error_code"], "UNSAFE_URL")
                    invalid = await session.call_tool("scrape", {"url": "https://example.com", "max_chars": True})
                    self.assertTrue(invalid.isError)
                    oversized = await session.call_tool("scrape_batch", {"urls": ["https://example.com"] * 11})
                    self.assertTrue(oversized.isError)
