"""Local feature-switch configuration tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.config import (ConfigError, TOOL_NAMES, is_tool_enabled, load_config,
                         runtime_options, set_tool_enabled)


class ConfigTests(unittest.TestCase):
    def test_defaults_enable_every_tool_without_creating_a_file(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-config-") as directory:
            path = Path(directory) / "config.json"
            with patch.dict(os.environ, {"SCRAPLING_CONFIG_FILE": str(path)}, clear=False):
                self.assertEqual(load_config()["enabled_tools"], {name: True for name in TOOL_NAMES})
                self.assertFalse(path.exists())

    def test_switch_is_saved_atomically_and_loaded_again(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-config-") as directory:
            path = Path(directory) / "config.json"
            with patch.dict(os.environ, {"SCRAPLING_CONFIG_FILE": str(path)}, clear=False):
                set_tool_enabled("scrape", False)
                self.assertFalse(is_tool_enabled("scrape"))
                self.assertTrue(is_tool_enabled("scrape_batch"))
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(document["enabled_tools"]["scrape"])

    def test_invalid_switch_value_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-config-") as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"enabled_tools": {"scrape": "yes"}}), encoding="utf-8")
            with patch.dict(os.environ, {"SCRAPLING_CONFIG_FILE": str(path)}, clear=False):
                with self.assertRaises(ConfigError):
                    load_config()

    def test_runtime_options_reject_invalid_environment_before_startup(self):
        with patch.dict(os.environ, {"SCRAPLING_MAX_CONCURRENCY": "99"}, clear=True):
            with self.assertRaises(ConfigError):
                runtime_options()

    def test_runtime_options_normalises_ports_and_numbers(self):
        with patch.dict(os.environ, {
            "SCRAPLING_MAX_CONCURRENCY": "4",
            "SCRAPLING_MAX_QUEUE": "10",
            "SCRAPLING_MIN_INTERVAL": "0.5",
            "SCRAPLING_CACHE_TTL": "60",
            "SCRAPLING_ALLOWED_PORTS": "443, 443, 8443",
        }, clear=True):
            options = runtime_options()
        self.assertEqual(options["max_concurrency"], 4)
        self.assertEqual(options["allowed_ports"], (443, 8443))


if __name__ == "__main__":
    unittest.main()
