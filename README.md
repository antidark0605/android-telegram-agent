# android-telegram-agent

Lightweight Telegram bridge for AI CLI agents on Android via Termux.

**Current implementation: AGY CLI only.** Other agents are not supported or bundled. Designed for regular (non-root) Termux. Initial CLI/runner validation was performed on an **Android 9 / API 28 / ARM64 commercial device**; the full Telegram round trip and background behavior are **not yet device-verified**. This public project has no device-specific features.

## P0 behavior

Telegram text → dedicated Bot (long polling) → on-demand official `agy` subprocess → verified JSON model response → Telegram reply.

- One Python Bot process, **no third-party Python dependencies**.
- No inbound ports, Linux intermediary, webhook, root, OpenClaw or multi-agent framework.
- One AGY task at a time; `/cancel` stops its process group.
- Allowlist both Telegram **user_id and chat_id** before running anything.
- Private runtime config and update offset outside the source checkout.
- AGY uses a dedicated working directory. This is **not** a filesystem sandbox; Termux processes may still access other files allowed by their Android UID. The Bot does not bypass AGY tool-approval rules.
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

## Dependencies and installation

The runtime is intentionally small. **No third-party Python packages or `pip install` are required**: Telegram uses Python's built-in `urllib.request`, `json`, `threading` and `subprocess` modules. The **Android/Termux system tools below are still required**.

| Dependency | Required for | Installation / verification |
| --- | --- | --- |
| **Termux app** | Running the Bot as an ordinary Android app user | Install a supported release from the [official Termux project](https://github.com/termux/termux-app). Run the commands *inside Termux*, not as an ADB root shell. |
| **Python 3.10+** | Running `bot.py`, Telegram networking and the AGY subprocess adapter | `pkg install python`, then `python --version`. Termux automatically installs the required Python package dependencies. |
| **Official AGY CLI for Android architecture** | Cloud AI model execution; this is a separate binary, **not a Python package** | Follow the [official CLI setup guide](https://www.antigravity.google/docs/cli/install/) for the compatible Android/ARM64 build and complete authentication in the **same Termux app user**. Check `command -v agy` and `agy --version`. The Bot does not install or redistribute AGY. |
| **Network access** | Telegram Bot API + AGY's online model service | Both endpoints need working DNS/TLS/HTTPS; no inbound port or webhook is needed. |
| **Telegram Bot token and user/chat allowlist** | Bot authentication and access control | Obtain a dedicated Bot token and IDs privately; put them in a mode-`0600` config file **outside this Git checkout**. |
| **Git** | **Only** for cloning/updating this repository | `pkg install git`; not required at runtime if the source is already present. |

**Dependency baseline:** The non-root AGY runner has been exercised on Termux **0.118.3**, Python **3.14.6**, and official AGY CLI **1.3.3** on Android 9 / ARM64. These are **observed working versions, not version pins or a claim of universal Android support**. Synthetic unit tests have also passed on Python 3.10 and CI Python 3.11–3.13. Different Android releases and package versions need their own validation.

### First-time Termux setup

In the **non-root Termux terminal**:

```sh
# Install system packages. No pip installation is required.
pkg update
pkg install python git

# Confirm runtime availability.
id
python --version
command -v python
command -v agy
agy --version

# Verify AGY is authenticated and the cloud model really responds.
agy -p "Reply with: CLI_OK" --output-format json
```

- `id` should identify the **Termux app UID, not uid=0 (root)**.
- If `python: not found`, install Python with `pkg install python` and check again. `pkg` resolves Python's native package dependencies automatically; no extra `pip` requirements are hidden in this project.
- If `agy: not found`, **stop here** and install the compatible official Android binary in this Termux environment first. Installing `python` does not install AGY.
- The example `CLI_OK` is a synthetic marker, not a real token. Validate JSON `status=SUCCESS`, a nonempty `response` and no errors or permission denials; do **not** rely only on exit code 0.
- Termux:API, Termux:Boot, Shizuku, Root, OpenClaw and a separate Python Telegram framework are **not required** for P0. Any optional background/boot management is a separate future task.

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
- Working directory separation is for safe defaults, **not** filesystem isolation. Restrict AGY tools and do not store unrelated private files inside the AGY workspace.
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
