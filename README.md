# android-telegram-agent

Lightweight Telegram bridge for AI CLI agents on Android via Termux.

**Current implementation: AGY CLI only.** Other agents are not supported or bundled. Designed for regular (non-root) Termux, initially validated against an **Android 9 / API 28 / ARM64 commercial device**. This public project has no device-specific features.

## P0 behavior

Telegram text → dedicated Bot (long polling) → on-demand official `agy` subprocess → verified JSON model response → Telegram reply.

- One Python Bot process, **no third-party Python dependencies**.
- No inbound ports, Linux intermediary, webhook, root, OpenClaw or multi-agent framework.
- One AGY task at a time; `/cancel` stops its process group.
- Allowlist both Telegram **user_id and chat_id** before running anything.
- Private runtime config and update offset outside the source checkout.
- AGY runs in a dedicated sandbox directory. Does not bypass its own tool-approval rules.
- No incoming Telegram photo/voice/file handling, no arbitrary shell commands.

### Commands

| Command | Behavior |
| --- | --- |
| Plain text | Starts one **independent** AGY JSON request (no session continuity yet) |
| `/start`, `/help` | Lists supported actions |
| `/status` | Shows busy / idle |
| `/cancel` | Stops the active AGY subprocess |
| Other commands | Returns an unsupported-command message |

**Not part of P0:** `/new`, `/session`, `/model`, `/models`, `/ls`, `/quota`, `/usage`. These will be discussed after an actual Telegram ↔ AGY device round trip succeeds. Session/model/effort behavior differs between CLI agents; the project intentionally has no speculative multi-agent infrastructure.

## Prerequisites

- Android + [Termux](https://github.com/termux/termux-app) with Python 3.
- Official [Antigravity CLI](https://www.antigravity.google/docs/cli/headless/) installed for the Android architecture, authenticated **in the same Termux user**.
- A dedicated Telegram Bot created via BotFather with a private token.
- Correct Telegram user and chat numeric IDs. Find them privately; do **not** post token/IDs in issues, logs, screenshots or commits.
- Internet access for Telegram and AGY cloud model calls.

In Termux:

```sh
pkg update
pkg install python git
python --version
command -v agy
agy --version
agy -p "Reply with: CLI_OK" --output-format json
```

Verify the final command actually returns a model response *before* starting the Telegram bridge. A successful login or CLI exit code alone does not establish that tools executed successfully.

## Configure privately

Clone this repository and create the sandbox outside the source checkout:

```sh
git clone https://github.com/antidark0605/android-telegram-agent.git
cd android-telegram-agent
mkdir -p "$HOME/telegram-agent-workspace" "$HOME/.config/android-telegram-agent"
chmod 700 "$HOME/telegram-agent-workspace" "$HOME/.config/android-telegram-agent"
cp config.example.json "$HOME/.config/android-telegram-agent/config.json"
chmod 600 "$HOME/.config/android-telegram-agent/config.json"
```

Edit the private configuration file using your preferred local editor, replacing **every** placeholder:

```json
{
  "bot_token": "YOUR_BOT_TOKEN",
  "allowed_pairs": [
    {"user_id": "YOUR_USER_ID", "chat_id": "YOUR_CHAT_ID"}
  ],
  "agy_command": "agy",
  "workspace": "/ABSOLUTE/PRIVATE/TERMUX/HOME/telegram-agent-workspace",
  "task_timeout_seconds": 120
}
```

Use your **actual** absolute Termux home workspace path, not the example path. Only local private configuration may contain real tokens or numeric IDs. Keep this config out of the Git checkout; the Bot refuses group/world-readable settings and refuses to start with placeholders.

Run:

```sh
python bot.py --config "$HOME/.config/android-telegram-agent/config.json"
```

On first start, old queued Telegram messages are discarded to avoid executing stale prompts. The next new text message from an allowlisted user/chat triggers AGY. Bot restarts retain the Telegram update offset in the same private directory. Ctrl+C stops the Bot and cancels any active task.

> Only **one** process may poll a given Telegram Bot token. Use a dedicated Bot for each deployed instance. Telegram and AGY require Internet. Android background execution, screen-off behavior and auto-start still need real-device testing; no guarantee of 24/7 uptime is implied.

## Security

- The Telegram bot token is an authentication credential: never commit it or publish full request URLs containing it. Real chat IDs, prompts, responses, OAuth state, private device information and test evidence must stay off public GitHub.
- Keep all AGY tool permissions narrowly scoped. Even when a task reports success, a tool action may have been denied: the Bot checks denial metadata and never uses a global permission bypass.
- Do not select the Termux home directory itself as the workspace, because it may contain AGY login data. Use a small dedicated subdirectory.
- The Bot does **not** run privileged/root commands. Do not change that without an explicit security review.
- Stop the running Bot before modifying/upgrading its source. Change runtime secrets in the private file, not the repo.
- `.gitignore` does not retroactively remove accidentally committed data. Rotate exposed tokens immediately if this ever happens.

## Tests

```sh
python -m unittest discover -s tests -v
python -m compileall -q bot.py agy.py config.py telegram_api.py tests
```

Tests use synthetic messages and mocked AGY processes, with **no real Bot Token, network calls or device private data**. A GitHub Actions workflow runs them on multiple Python versions.

### Real-device verification checklist

1. Non-root Termux: AGY CLI independently answers a simple prompt.
2. Dedicated Bot receives a fresh Telegram message and returns its **real** AGY result.
3. Verify unauthorized sender/chat is ignored; no unauthorized AGY child starts.
4. Repeated messages, busy response, `/cancel`, timeout and network recovery.
5. Verify no child processes remain after cancellation/timeout.
6. Check private config mode `0600`, no tokens in tracked files or logs.
7. Measure idle/active memory and screen-off resilience on Android 9 / API 28 hardware.
8. Report sanitized PASS/FAIL only; do not attach real transcripts, OAuth details, logs, device identifiers or internal paths to the public repository.

## License

The original project source is MIT licensed. Telegram, Termux and Google Antigravity CLI are separate software with their own terms. None of their binaries, tokens or authentication files are distributed here.
