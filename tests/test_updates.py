"""Update checks are bounded, cacheable, and never trust arbitrary links."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.update_check import check_for_update, human_message


class UpdateTests(unittest.TestCase):
    def test_detects_new_release_and_uses_canonical_github_url(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-update-") as directory:
            response = {"tag_name": "v1.2.1", "html_url": "https://evil.example/update"}
            with patch.dict("os.environ", {
                "SCRAPLING_UPDATE_CACHE_FILE": str(Path(directory) / "update.json"),
            }, clear=False), patch("src.update_check.urlopen") as urlopen:
                fake = urlopen.return_value.__enter__.return_value
                fake.status = 200
                fake.read.return_value = json.dumps(response).encode()
                result = check_for_update("1.2.0", force=True)
            self.assertEqual(result["status"], "update_available")
            self.assertEqual(result["latest_version"], "1.2.1")
            self.assertTrue(result["release_url"].startswith(
                "https://github.com/zouzhengshi/scrapling-mcp/"))
            self.assertTrue((Path(directory) / "update.json").exists())

    def test_cache_avoids_second_network_request(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-update-") as directory:
            cache = Path(directory) / "update.json"
            cache.write_text(json.dumps({
                "checked_at": 4_000_000_000,
                "latest_version": "1.2.1",
                "release_url": "https://github.com/zouzhengshi/scrapling-mcp/releases/tag/v1.2.1",
            }), encoding="utf-8")
            with patch.dict("os.environ", {
                "SCRAPLING_UPDATE_CACHE_FILE": str(cache),
                "SCRAPLING_UPDATE_CACHE_TTL": "604800",
            }, clear=False), patch("src.update_check.urlopen") as urlopen:
                result = check_for_update("1.2.0")
            self.assertEqual(result["status"], "update_available")
            urlopen.assert_not_called()

    def test_disabled_check_is_local_and_silent(self):
        with patch.dict("os.environ", {"SCRAPLING_UPDATE_CHECK": "off"}, clear=False), \
                patch("src.update_check.urlopen") as urlopen:
            result = check_for_update("1.2.0", force=True)
        self.assertEqual(result["status"], "disabled")
        self.assertIsNone(human_message(result))
        urlopen.assert_not_called()

    def test_network_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory(prefix="scrapling-update-") as directory, \
                patch.dict("os.environ", {
                    "SCRAPLING_UPDATE_CACHE_FILE": str(Path(directory) / "update.json"),
                }, clear=False), patch("src.update_check.urlopen", side_effect=TimeoutError):
            result = check_for_update("1.2.0", force=True)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
