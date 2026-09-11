"""Tests for the installed command-line entry point."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from src.cli import _tool_parser, main


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
        with patch("src.cli._run_tool_command", return_value=0) as run_tool:
            self.assertEqual(main(["login", "bilibili"]), 0)
        run_tool.assert_called_once_with(["login", "bilibili"])


if __name__ == "__main__":
    unittest.main()
