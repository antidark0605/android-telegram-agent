# Telegram Voice / TTS via Android Audio Routing
Revision: 1 — 2026-11-11

Tracking: #19. Non-root Termux, no custom Audio Focus or forced routing.

## Commands
- Authorized Telegram OGG/Opus voice (up to 8 MiB, 120 seconds): download to private temp and play with `termux-media-player` via Android's currently chosen media output route.
- `/tts <text>` speaks up to 500 characters using free Android system TTS; `/tts` enables per-Topic speak mode; `/tts off` disables it; `/tts status` shows the setting. Pending Ask Telegram replies take precedence.
- `/audio status` shows the shared queue. `/audio stop` stops current media playback and drops queued items. **Current TTS utterance may finish** because Termux API offers no reliable active-utterance cancel.
- No AGY model/session is needed for audio. TTS mode is persisted via existing private session store, but /new resets it.

## Operator-owned prerequisites
Termux:API App installed with same signer/source as Termux, plus `pkg install termux-api`. Do not install from the Bot.
```sh
command -v termux-media-player
command -v termux-tts-speak
termux-tts-engines
termux-tts-speak -l zh -n TW -s MUSIC "測試語音"
```
Android TTS engine must support Traditional Chinese; offline support depends on installed voice packages. No cloud TTS credentials or paid API.

## Local Agent acceptance: NOT VERIFIED
1. Verify both Termux:API commands on RTX and compatible Android App signatures.
2. Telegram voice playback, OGG/Opus decoder, file cleanup after completion/failure, queue full, stop.
3. Confirm actual output through built-in speaker, Bluetooth speaker, wired/Bluetooth headset, including route changes.
4. Traditional Chinese TTS with screen on/off; compare expected utterance length with current conservative queue hold. Native TTS completion callback is not implemented; long utterances may overlap.
5. Verify no unauthorized getFile/command, pending Ask response priority, independent AGY tasks, restart.
6. Never publish device logs, IDs, tokens, paths, or voice data.

Live Android routing, real speech, and playback are **NOT VERIFIED**.
Sources:
https://github.com/termux/termux-api-package/blob/master/scripts/termux-media-player.in
https://github.com/termux/termux-api-package/blob/master/scripts/termux-tts-speak.in
