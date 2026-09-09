"""Exercise initialize/list/call/ping on the actual stdio wire protocol."""
import asyncio
import json
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
            reply = await scrape("https://example.com/")
        self.assertEqual(json.loads(reply.content[0].text), reply.structuredContent)
        self.assertFalse(reply.isError)
        self.assertTrue(reply.structuredContent["content_is_untrusted"])

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
        self.assertEqual([r["url"] for r in reply.structuredContent["results"]],
                         ["https://one.test/", "https://two.test/"])

    async def test_real_stdio_schema_validation_and_error_marker(self):
        root = Path(__file__).resolve().parent.parent
        parameters = StdioServerParameters(command=sys.executable, args=[str(root / "main.py")],
                                           env={"PYTHONIOENCODING": "utf-8"})
        async with asyncio.timeout(15):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    self.assertEqual(set(tools), {"scrape", "scrape_batch"})
                    self.assertIn("error_code", tools["scrape"].outputSchema["properties"])
                    denied, ping = await asyncio.gather(
                        session.call_tool("scrape", {"url": "http://127.0.0.1/"}), session.send_ping())
                    self.assertTrue(denied.isError)
                    self.assertEqual(denied.structuredContent["error_code"], "UNSAFE_URL")
                    invalid = await session.call_tool("scrape", {"url": "https://example.com", "max_chars": True})
                    self.assertTrue(invalid.isError)
                    oversized = await session.call_tool("scrape_batch", {"urls": ["https://example.com"] * 11})
                    self.assertTrue(oversized.isError)
