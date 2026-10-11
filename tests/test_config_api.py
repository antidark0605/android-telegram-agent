import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigError, load_settings
from telegram_api import split_text


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "fake-home"
        self.home.mkdir()
        self.work = self.home / "work"
        self.work.mkdir(mode=0o700)
        folder = self.home / ".config"
        folder.mkdir(mode=0o700)
        self.conf = folder / "settings.json"
        self.data = {
            "bot_token": "100000000:FakeBotTokenOnlyForTests",
            "allowed_pairs": [{"user_id": "101", "chat_id": "-202"}],
            "agy_command": "agy",
            "workspace": str(self.work),
            "task_timeout_seconds": 40,
        }
        self.home_patch = patch("config.Path.home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def _write(self):
        self.conf.write_text(json.dumps(self.data), encoding="utf-8")
        self.conf.chmod(0o600)

    def test_valid_private_config(self):
        self._write()
        c = load_settings(self.conf)
        self.assertEqual(c.allowed_pairs, {(101, -202)})
        self.assertEqual(c.task_timeout_seconds, 40)

    def test_symlinked_config_rejected(self):
        self._write()
        link = self.conf.parent / "alias.json"
        link.symlink_to(self.conf)
        with self.assertRaises(ConfigError):
            load_settings(link)

    def test_world_readable_config_rejected(self):
        self._write()
        self.conf.chmod(0o644)
        with self.assertRaises(ConfigError):
            load_settings(self.conf)

    def test_placeholder_credentials_rejected(self):
        self.data["bot_token"] = "YOUR_BOT_TOKEN"
        self._write()
        with self.assertRaises(ConfigError):
            load_settings(self.conf)

    def test_home_directory_cannot_be_workspace(self):
        self.data["workspace"] = str(self.home)
        self._write()
        with self.assertRaises(ConfigError):
            load_settings(self.conf)

    def test_disallow_shell_command_list(self):
        self.data["agy_command"] = "agy; curl sample"
        self._write()
        with self.assertRaises(ConfigError):
            load_settings(self.conf)


class MessageTests(unittest.TestCase):
    def test_unicode_text_chunks(self):
        parts = split_text("臺" * 6000)
        self.assertEqual("".join(parts), "臺" * 6000)
        self.assertTrue(all(len(part) <= 3800 for part in parts))

    def test_empty_message(self):
        self.assertEqual(split_text(""), ["(empty response)"])


if __name__ == "__main__":
    unittest.main()
