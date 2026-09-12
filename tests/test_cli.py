"""Tests for the installed command-line entry point."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from src.cli import _print_tool_result, _tool_parser, main


class CliTests(unittest.TestCase):
    def test_help_is_available(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--help"]), 0)
        self.assertIn("scrapling-mcp status", output.getvalue())

    def test_version_is_available(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--version"]), 0)
        self.assertIn("Scrapling MCP 1.2.0", output.getvalue())

    def test_agent_guide_is_available(self):
        output = StringIO()
        with patch("src.cli.render_agent_guide", return_value="guide"), redirect_stdout(output):
            self.assertEqual(main(["--agent-guide"]), 0)
        self.assertEqual(output.getvalue().strip(), "guide")

    def test_core_tool_commands_are_recognised(self):
        parsed = _tool_parser("scrape").parse_args([
            "https://example.com", "--auth-profile", "github",
        ])
        self.assertEqual(parsed.auth_profile, "github")
        self.assertEqual(parsed.output_format, "auto")
        with patch("src.cli._run_tool_command", return_value=0) as run_tool:
            self.assertEqual(main(["login", "bilibili"]), 0)
        run_tool.assert_called_once_with(["login", "bilibili"])

    def test_human_tool_output_is_concise(self):
        output = StringIO()
        result = {
            "success": True, "title": "Example", "status_code": 200,
            "final_url": "https://example.com", "markdown": "正文",
            "summary": {"text": "摘要"}, "truncated": False,
        }
        with redirect_stdout(output):
            _print_tool_result("scrape", result, "human")
        text = output.getvalue()
        self.assertIn("✅ 抓取完成", text)
        self.assertIn("标题：Example", text)
        self.assertIn("正文", text)
        self.assertNotIn('"success": true', text)

    def test_no_arguments_does_not_start_an_interactive_terminal_when_piped(self):
        output = StringIO()
        with redirect_stdout(output), patch("sys.stdin.isatty", return_value=False), \
                patch("sys.stdout.isatty", return_value=False):
            self.assertEqual(main([]), 0)
        self.assertIn("scrapling-mcp terminal", output.getvalue())

    def test_update_command_reports_available_release(self):
        output = StringIO()
        result = {
            "status": "update_available", "current_version": "1.2.0",
            "latest_version": "1.2.1",
            "release_url": "https://github.com/zouzhengshi/scrapling-mcp/releases/tag/v1.2.1",
        }
        with patch("src.cli.check_for_update", return_value=result), redirect_stdout(output):
            self.assertEqual(main(["update"]), 0)
        self.assertIn("1.2.1", output.getvalue())

    def test_update_command_reports_current_release(self):
        output = StringIO()
        result = {"status": "up_to_date", "current_version": "1.2.0"}
        with patch("src.cli.check_for_update", return_value=result), redirect_stdout(output):
            self.assertEqual(main(["update"]), 0)
        self.assertIn("最新版本", output.getvalue())


if __name__ == "__main__":
    unittest.main()
