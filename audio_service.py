"""Serial audio queue using Android's current media output route through Termux:API."""
import subprocess
import threading
import time
from pathlib import Path
from voice_handler import download_voice

class AudioService:
    def __init__(self, api, private_dir: Path):
        self.api, self.private_dir = api, private_dir
        self.lock = threading.RLock()
        self.jobs = []
        self.current = None
        self.worker_running = False
        self.generation = 0

    def submit(self, kind, value, callback=None):
        if kind not in ("voice", "tts"):
            raise ValueError("Invalid audio job")
        with self.lock:
            if len(self.jobs) >= 6:
                return False
            self.jobs.append((kind, value, callback))
            if not self.worker_running:
                self.worker_running = True
                threading.Thread(target=self._worker, daemon=True, name="audio-worker").start()
        return True

    def status(self):
        with self.lock:
            return f"Audio: {self.current or 'idle'}; queued: {len(self.jobs)}"

    def stop(self):
        with self.lock:
            self.jobs.clear()
            self.generation += 1
            playing = self.current == "voice"
        if playing:
            try:
                self._run(["termux-media-player", "stop"], 8)
            except ValueError:
                pass
        # Termux:API has no reliable active TTS-utterance cancellation API.

    @staticmethod
    def _run(argv, timeout, input_text=None):
        try:
            result = subprocess.run(argv, input=input_text, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf8", errors="replace",
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Audio command unavailable") from None
        if result.returncode:
            raise ValueError("Audio command failed")
        return result.stdout[:1000]

    def _play_voice(self, data, generation):
        path = None
        try:
            path = download_voice(self.api, data, self.private_dir)
            with self.lock:
                if generation != self.generation:
                    return
            self._run(["termux-media-player", "play", str(path)], 12)
            deadline = time.monotonic() + data["duration"] + 15
            while time.monotonic() < deadline:
                with self.lock:
                    if generation != self.generation:
                        return
                time.sleep(0.5)
                state = self._run(["termux-media-player", "info"], 10)
                if "No track currently!" in state:
                    return
                if not state.startswith("Status: Playing"):
                    raise ValueError("Audio interrupted")
            self._run(["termux-media-player", "stop"], 8)
            raise ValueError("Audio timeout")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _speak(self, text, generation):
        with self.lock:
            if generation != self.generation:
                return
        self._run(["termux-tts-speak", "-l", "zh", "-n", "TW", "-s", "MUSIC"],
                  35, input_text=text)
        # Native TTS response may finish before Android speaks the full phrase.
        deadline = time.monotonic() + min(45, max(2, len(text) / 4))
        while time.monotonic() < deadline:
            with self.lock:
                if generation != self.generation:
                    return
            time.sleep(0.2)

    def _worker(self):
        while True:
            with self.lock:
                if not self.jobs:
                    self.current = None
                    self.worker_running = False
                    return
                kind, data, callback = self.jobs.pop(0)
                generation = self.generation
                self.current = kind
            ok = False
            try:
                if kind == "voice":
                    self._play_voice(data, generation)
                else:
                    self._speak(data, generation)
                ok = True
            except (ValueError, OSError, KeyError, TypeError):
                pass
            finally:
                if callback is not None:
                    try:
                        callback(ok and generation == self.generation)
                    except Exception:
                        pass
                with self.lock:
                    self.current = None
