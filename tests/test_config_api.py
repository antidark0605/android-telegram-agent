import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigError, load_settings
from telegram_api import TelegramAPI, TelegramError, split_text
from command_menu import telegram_commands


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

    def test_myid_bootstrap_is_explicit_boolean(self):
        self.data["allowed_pairs"] = []
        self._write()
        with self.assertRaises(ConfigError):
            load_settings(self.conf)
        self.data["allow_unlisted_myid"] = True
        self._write()
        settings = load_settings(self.conf)
        self.assertEqual(settings.allowed_pairs, frozenset())
        self.assertTrue(settings.allow_unlisted_myid)
        self.data["allow_unlisted_myid"] = "true"
        self._write()
        with self.assertRaises(ConfigError):
            load_settings(self.conf)

    def test_myid_bootstrap_disabled_by_default(self):
        self._write()
        self.assertFalse(load_settings(self.conf).allow_unlisted_myid)

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

    def test_allowed_callback_updates(self):
        api = TelegramAPI("dummy-token")
        with patch.object(api, "_call", return_value=[]) as call:
            self.assertEqual(api.get_updates(0), [])
        self.assertIn("callback_query", call.call_args.args[1]["allowed_updates"])

    def test_command_menu_get_set_and_validation(self):
        api = TelegramAPI("dummy-token")
        commands = telegram_commands()
        remote = [dict(item, is_ephemeral=False) for item in commands]
        with patch.object(api, "_call", side_effect=[remote, True, True]) as call:
            self.assertEqual(api.get_my_commands(), commands)
            api.set_my_commands(commands)
            api.delete_my_commands("all_private_chats")
        self.assertEqual(call.call_args_list[0].args[0], "getMyCommands")
        self.assertEqual(call.call_args_list[1].args,
                         ("setMyCommands", {"commands": commands}))
        self.assertEqual(call.call_args_list[2].args,
                         ("deleteMyCommands",
                          {"scope": {"type": "all_private_chats"}}))
        with patch.object(api, "_call", return_value={}):
            with self.assertRaises(TelegramError) as caught:
                api.get_my_commands()
        self.assertEqual(caught.exception.kind, "protocol")
        with patch.object(api, "_call") as call:
            with self.assertRaises(TelegramError) as caught:
                api.set_my_commands([{"command": "model"}])
        call.assert_not_called()
        self.assertEqual(caught.exception.kind, "protocol")
        for bad in ([{"command": "Bad", "description": "x"}],
                    [{"command": "x", "description": ""}]):
            with patch.object(api, "_call") as call:
                with self.assertRaises(TelegramError):
                    api.set_my_commands(bad)
            call.assert_not_called()
        with patch.object(api, "_call") as call:
            with self.assertRaises(TelegramError):
                api.get_my_commands("chat")
        call.assert_not_called()

    def test_send_text_keyboard_and_message_id(self):
        api = TelegramAPI("dummy-token")
        markup = {"inline_keyboard": [[{"text": "Stop", "callback_data": "stop:test"}]]}
        with patch.object(api, "_call", return_value={"message_id": 77}) as call:
            message_id = api.send_text(1, "testing", 2, reply_markup=markup)
        self.assertEqual(message_id, 77)
        self.assertEqual(call.call_args.args[1]["reply_markup"], markup)
        self.assertEqual(call.call_args.args[1]["message_thread_id"], 2)

    def test_edit_removes_keyboard(self):
        api = TelegramAPI("dummy-token")
        with patch.object(api, "_call", return_value=True) as call:
            api.edit_text(1, 3, "Completed")
        self.assertEqual(call.call_args.args[0], "editMessageText")
        self.assertEqual(call.call_args.args[1]["reply_markup"],
                         {"inline_keyboard": []})

    def test_empty_message(self):
        self.assertEqual(split_text(""), ["(empty response)"])

    def test_non_bmp_emoji_respect_utf16_limit(self):
        from vendor.telegram_ui import utf16_length
        text = "😀" * 5000 + "\n" + "a😀\n" * 10
        parts = split_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(utf16_length(part) <= 3800 for part in parts))
        # An emoji (two UTF-16 units) is never cut in half.
        self.assertEqual(split_text("😀😀😀", limit=2), ["😀", "😀", "😀"])
        self.assertEqual(split_text("ab\ncd\n", limit=4), ["ab\n", "cd\n"])

    def test_formatted_status_send_and_edit_carry_markup(self):
        api = TelegramAPI("dummy-token")
        markup = {"inline_keyboard": [[{"text": "🛑", "callback_data": "stop:k1"}]]}
        with patch.object(api, "_call", return_value={"message_id": 5}) as call:
            api.send_text(1, "<b>x</b>\n" + "y" * 3900, 2, reply_markup=markup,
                          parse_mode="HTML")
            self.assertEqual(call.call_count, 1)  # HTML tags are never split.
            sent = call.call_args.args[1]
            self.assertEqual((sent["parse_mode"], sent["reply_markup"],
                              sent["message_thread_id"]), ("HTML", markup, 2))
            api.edit_text(1, 5, "<b>x</b>", markup, parse_mode="HTML")
            edited = call.call_args.args[1]
            self.assertEqual((edited["parse_mode"], edited["reply_markup"],
                              edited["message_id"]), ("HTML", markup, 5))
            api.send_text(1, "plain *text*")
            self.assertNotIn("parse_mode", call.call_args.args[1])
            api.edit_text(1, 5, "plain")
            self.assertNotIn("parse_mode", call.call_args.args[1])

    def test_formatted_text_utf16_size_guard(self):
        from telegram_api import TelegramError
        from vendor.telegram_ui import utf16_length
        api = TelegramAPI("dummy-token")
        # 7 units of tags + 2044 emoji (2 units each) + 1 = exactly 4096 units.
        exact = "<b>" + "😀" * 2044 + "y" + "</b>"
        over = "<b>" + "😀" * 2044 + "yy" + "</b>"
        self.assertEqual(utf16_length(exact), 4096)
        self.assertLess(len(over), 4096)  # Code points alone would pass.
        with patch.object(api, "_call", return_value={"message_id": 5}) as call:
            api.send_text(1, exact, parse_mode="HTML")
            self.assertEqual(call.call_args.args[1]["text"], exact)
            api.edit_text(1, 5, exact, parse_mode="HTML")
            self.assertEqual(call.call_args.args[1]["text"], exact)
            self.assertEqual(call.call_count, 2)
        for send in (lambda: api.send_text(1, over, 2, parse_mode="HTML"),
                     lambda: api.edit_text(1, 5, over, parse_mode="HTML")):
            with patch.object(api, "_call") as call:
                with self.assertRaises(TelegramError) as ctx:
                    send()
            call.assert_not_called()
            self.assertEqual(ctx.exception.kind, "protocol")
            self.assertNotIn("😀", str(ctx.exception))
            self.assertNotIn("dummy-token", str(ctx.exception))
        # Plain text of the same size is still chunked, not rejected.
        with patch.object(api, "_call", return_value={"message_id": 6}) as call:
            api.send_text(1, over)
            self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
