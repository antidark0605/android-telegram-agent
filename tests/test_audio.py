"""Synthetic tests: never invoke Android, Telegram or TTS."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from audio_commands import parse_tts
from audio_service import AudioService
from voice_handler import select_voice

class AudioTests(unittest.TestCase):
    def test_parser(self):
        self.assertEqual(parse_tts(""), ("on", None))
        self.assertEqual(parse_tts("off"), ("off", None))
        self.assertEqual(parse_tts("status"), ("status", None))
        self.assertEqual(parse_tts("你好"), ("speak", "你好"))
        self.assertEqual(parse_tts("a"*501), ("invalid", None))

    def test_voice_limits(self):
        valid = dict(file_id="test_id", duration=30, file_size=4096, mime_type="audio/ogg")
        self.assertEqual(select_voice(valid), valid)
        for change in (dict(duration=121), dict(file_size=9*1024*1024),
                       dict(mime_type="audio/mp3"), dict(file_id="../../x")):
            self.assertIsNone(select_voice(dict(valid, **change)))

    def test_queue_limit(self):
        with tempfile.TemporaryDirectory() as d:
            service = AudioService(MagicMock(), Path(d))
            service.worker_running = True
            for i in range(6):
                self.assertTrue(service.submit("tts", str(i)))
            self.assertFalse(service.submit("tts", "overflow"))
            service.stop()
            self.assertEqual(service.jobs, [])

    def test_tts_uses_music_stream_no_shell(self):
        with tempfile.TemporaryDirectory() as d:
            service = AudioService(MagicMock(), Path(d))
            with patch.object(service, "_run", return_value="") as run, patch("audio_service.time.sleep"):
                service._speak("中文", 0)
            run.assert_called_once_with(
                ["termux-tts-speak", "-l", "zh", "-n", "TW", "-s", "MUSIC"],
                35, input_text="中文")

if __name__ == "__main__":
    unittest.main()
