"""Management terminal output must remain useful without leaking secrets."""
from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.agent_guide import connection_config_data
from src.terminal import cookies_data, run_terminal


class TerminalTests(unittest.TestCase):
    def test_cookie_summary_hides_cookie_and_storage_values(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-terminal-") as directory:
            root = Path(directory)
            cookie_file = root / "cookies.json"
            auth_dir = root / "auth"
            cookie_file.write_text(json.dumps({"profiles": {"demo": {
                "allowed_domains": ["example.com"],
                "cookies": [{"name": "session", "value": "COOKIE-VALUE-SECRET",
                             "domain": ".example.com"}],
            }}}), encoding="utf-8")
            auth_dir.mkdir()
            (auth_dir / "github.state.json").write_text(json.dumps({
                "cookies": [{"name": "user_session", "value": "AUTH-VALUE-SECRET",
                             "domain": ".github.com"}],
                "origins": [{"origin": "https://github.com", "localStorage": [
                    {"name": "token", "value": "LOCAL-STORAGE-SECRET"},
                ]}],
            }), encoding="utf-8")
            environment = {
                "SCRAPLING_COOKIE_FILE": str(cookie_file),
                "SCRAPLING_AUTH_DIR": str(auth_dir),
            }
            with patch.dict(os.environ, environment, clear=False):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(run_terminal(["--json", "cookies"]), 0)
                text = output.getvalue()
                data = json.loads(text)
            self.assertTrue(data["values_hidden"])
            self.assertIn("demo", text)
            self.assertIn("session", text)
            self.assertIn("user_session", text)
            self.assertNotIn("COOKIE-VALUE-SECRET", text)
            self.assertNotIn("AUTH-VALUE-SECRET", text)
            self.assertNotIn("LOCAL-STORAGE-SECRET", text)

    def test_terminal_can_toggle_a_tool(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-terminal-") as directory:
            path = Path(directory) / "config.json"
            with patch.dict(os.environ, {"SCRAPLING_CONFIG_FILE": str(path)}, clear=False):
                self.assertEqual(run_terminal(["tool", "disable", "scrape"]), 0)
                self.assertEqual(run_terminal(["--json", "tools"]), 0)
                document = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(document["enabled_tools"]["scrape"])

    def test_restart_reports_when_no_client_owned_server_is_found(self):
        output = StringIO()
        with patch("src.terminal._server_process_objects", return_value=[]), redirect_stdout(output):
            self.assertEqual(run_terminal(["--json", "restart"]), 1)
        data = json.loads(output.getvalue())
        self.assertFalse(data["success"])
        self.assertEqual(data["found"], 0)
        self.assertIn("stdio", data["message"])

    def test_help_documents_restart_command(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_terminal(["--json", "help"]), 0)
        data = json.loads(output.getvalue())
        commands = {item["command"] for item in data["commands"]}
        self.assertIn("restart", commands)

    def test_agent_guide_is_available_without_local_secrets(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_terminal(["guide"]), 0)
        text = output.getvalue()
        self.assertIn("Scrapling MCP", text)
        self.assertIn("scrape", text)
        self.assertIn("login_custom", text)
        self.assertIn("不要要求用户把密码", text)
        self.assertNotIn("COOKIE-VALUE-SECRET", text)

    def test_agent_guide_json_is_copyable(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_terminal(["--json", "agent-guide"]), 0)
        data = json.loads(output.getvalue())
        self.assertTrue(data["values_hidden"])
        self.assertIn("scrape", data["copy_text"])
        config = data["connection_config"]["mcpServers"]["scrapling"]
        self.assertEqual(config["args"][-1], str(Path(__file__).resolve().parent.parent / "main.py"))
        self.assertEqual(config["env"]["PYTHONIOENCODING"], "utf-8")

    def test_connection_config_uses_the_target_machine_paths(self):
        config = connection_config_data(
            python_executable=r"E:\portable\python.exe",
            project_root=r"F:\checkouts\scrapling-mcp",
        )["mcpServers"]["scrapling"]
        self.assertEqual(config["command"], r"E:\portable\python.exe")
        self.assertEqual(config["args"], [r"F:\checkouts\scrapling-mcp\main.py"])

    def test_interactive_startup_only_shows_connection_info(self):
        output = StringIO()
        with patch("builtins.input", return_value="exit"), redirect_stdout(output):
            self.assertEqual(run_terminal([]), 0)
        text = output.getvalue()
        self.assertIn("MCP 连接方式", text)
        self.assertIn("scrapling_env", text)
        self.assertNotIn("不要要求用户把密码", text)


if __name__ == "__main__":
    unittest.main()
