"""Invocation logs identify the caller without recording request secrets."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.audit_log import audit_tool, close_logging, configure_logging, read_recent, runtime_event


class AuditLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_log_has_caller_and_safe_target_summary(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-logs-") as directory:
            with patch.dict(os.environ, {
                "SCRAPLING_LOG_DIR": directory,
                "SCRAPLING_CALLER_NAME": "Test Agent",
            }, clear=False):
                configure_logging()

                @audit_tool("scrape")
                async def sample(url, auth_profile=None):
                    return type("Reply", (), {
                        "structuredContent": {"success": True},
                        "isError": False,
                    })()

                try:
                    runtime_event("test_event", secret="should not be a tool input")
                    await sample("https://example.com/path?token=URL-SECRET", auth_profile="demo")
                    records = read_recent("calls", 10)
                finally:
                    close_logging()

            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["event"], "tool_call_started")
            self.assertEqual(records[0]["tool"], "scrape")
            self.assertEqual(records[0]["caller"]["name"], "Test Agent")
            self.assertEqual(records[0]["details"]["target_host"], "example.com")
            self.assertNotIn("URL-SECRET", json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
