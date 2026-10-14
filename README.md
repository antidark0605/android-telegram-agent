# android-telegram-agent

Revision: 4 — 2026-10-14

Small non-root **Termux + Telegram + official AGY CLI** Bot for Android. Original Python standard-library code, not a port of another private codebase. No root, inbound ports, OpenClaw, collaboration, kanban, server framework, or pip dependencies.

**Validation scope:** The basic Telegram ↔ AGY text round trip has been exercised in a non-root Termux environment. The integrated session, MCP and interactive controls require separate acceptance testing. Device-specific test environments, identifiers and results are intentionally kept outside this public repository.

## Telegram UX

| Telegram input | Behavior |
| --- | --- |
| Text | Require an activated session and explicit model, then run AGY and resume the prior conversation |
| /start, /help | Show available commands |
| /myid | Show the **current** Telegram user, chat type/ID and Topic ID, subject to the authorization policy |
| /new | Reset this Topic's AGY conversation (keeps its model selection) |
| /status | Show running/idle, CWD, native AGY conversation ID, selected model/variant and last reported model, YOLO setting |
| /model | Pick a model/variant from `agy models`; **per Topic** |
| /session start | Activate the current chat/Topic without submitting a prompt; keep the configured workspace |
| /ls | List the configured workspace using `ls -l`, without arbitrary arguments |
| /context | Show last task's accounting tokens; **not** an invented context occupancy percentage |
| /quota, /usage | Request AGY's standalone /usage rate-limit report, if supported |
| Inline 🛑 Stop | Cancel that active AGY task; no /cancel command |

Each task sends one `⏳ Running · MM:SS` status message with 🛑 Stop, edited around every five seconds, and a separate final answer. Stop is tied to the authorized user, chat, exact status-message ID and unique task key. Once finished, the button is removed. One AGY process at a time prevents memory spikes on small devices.

**Topics:** A fresh chat/Topic requires `/session start` before ordinary text can run AGY. This activates the current scope; it does not create a Telegram Topic. A pre-existing saved AGY conversation remains activated. The private chat (no Topic) has its own separate Session. Each Telegram `message_thread_id` has a separate AGY conversation ID, Model selection and last task token accounting. A new Topic does not require creating another Bot Token. `/new` resets only the current Topic. Restarting the Bot preserves its Session mappings in a private JSON file. Invalid/corrupted state stops startup rather than silently erasing conversations; a failed persistence operation never updates the in-memory mapping.

**Model identification:** `/status` displays the configured or selected model, not a guessed effective model. Models selected through Telegram are passed as AGY's `--model` argument to the *next* run; an active task cannot change its model. If a validated AGY JSON result explicitly provides an `actual_model` field, the Bot saves it separately; without one it displays **not reported**. The former AGY default option only omitted `--model`; it did not identify a particular model. It has been removed, together with `/models` and `/model default`. Choose an explicit model before submitting tasks. Effort is part of the AGY model variant (for example High/Medium/Low), not a separate Bot setting. A native session ID remains pending until AGY returns a validated conversation ID. `/ls` only lists the configured workspace, excludes hidden entries just like `ls -l`, rejects arguments, has a five-second deadline and truncates long output. Listing errors do not expose stderr.

### Ask Telegram

AGY can call a small **local MCP tool** named `ask_telegram` with `question` and optional `choices` (every choice requires label, description and value). The Bot forwards the question to the same chat/Topic with inline choice buttons or accepts a free-text reply, then returns the answer to the waiting AGY MCP process. A pending free-text reply is not interpreted as a new task.

On Bot startup, `AskBridge.install()` merges the single `telegram-ask` server into the selected **private workspace** `.agents/mcp_config.json`, without touching global AGY settings. Existing unrelated MCP servers are preserved. Startup fails safely on a conflicting `telegram-ask` definition. The bridge stores only pending question/answer data in the private config directory, cleans it when a task ends and never exposes an HTTP listener. The installed AGY CLI must support workspace stdio MCP and load its configuration; **this must be verified on the device**.

## Rich message and artifacts (v0.2 candidate)

The final **AGY response** is delivered via Telegram Bot API **10.3** \`sendRichMessage\`: headings, emphasis, lists, code blocks and tables are handled natively from \`rich_message.markdown\`. The running timer, inline Stop button, slash commands and Ask Telegram question still use the existing message API, so their behavior does not change.

The Bot will attach a file only when the agent **explicitly declares it** on its own line in the final answer. For example, after creating a synthetic PDF under the workspace's \`artifacts/\` directory:

\`\`\`text
TAGW_MEDIA: {"path":"artifacts/report.pdf","kind":"auto"}
\`\`\`

- The artifact path must be **relative to the configured workspace** and start with \`artifacts/\`. Never list arbitrary files, follow symlinks, or use paths outside this delivery root. Incoming marker metadata can **never** choose another Telegram chat, user, or Topic.
- A maximum of **4 artifacts**, each at most **20 MiB** and **40 MiB total** per reply, are allowed. The file must exist and be a regular file. Unsafe markers produce a generic omission notice without disclosing the requested path.
- PNG/JPEG/WebP typically become in-chat photos, MP3/M4A become audio, MP4 video, and PDF/ZIP/TXT/HTML or unknown file types become documents. The output may combine markdown and multiple media attachments into one Rich Message.
- The Bot sends files using bounded-memory **multipart/form-data streaming** with 64 KiB file chunks. It does not load whole media files into memory, unlike typical simple multipart examples.
- If Telegram explicitly rejects the Rich call with HTTP **400 or 404**, the Bot falls back to \`sendMessage\` with safe limited HTML formatting, followed by classic \`sendDocument\`/\`sendPhoto\`/\`sendAudio\`/\`sendVideo\` for declared files. **An ambiguous network failure is not automatically retried** because Telegram may already have accepted the request.
- Longer answers are split into pieces before reaching rich formatting limits. Some complex Markdown spanning split boundaries may render less perfectly than a short single-message answer. The fallback HTML parser intentionally supports only a readable subset of Markdown.

Never include secrets in artifacts. Telegram group members may see results even if the Bot only executes commands from whitelisted senders. Private credentials, workspaces, personal files, OAuth state, raw logs and physical test-device information must not be committed into the public repository.

**Important:** GitHub mock CI verifies request structure, safety and fallbacks. Actual **Telegram 10.3 rich media delivery**, client rendering, album behavior, and local CLI artifact generation must be confirmed by Local Agent on the device before production deployment.

## Telegram access control and first-time `/myid` setup

This Bot does not authorize an entire group just because a message arrived there. Every normal incoming text command, AGY prompt, Topic/session action and inline callback requires an exact **`(user_id, chat_id)` pair** in the private `allowed_pairs` configuration. The chat ID may be negative for Telegram groups/supergroups; Topic IDs are routing/session scopes *inside* an authorized chat, not independent permissions. If another user joins an authorized group, that user still cannot control AGY until their **own user/chat pair** is explicitly added. Unknown senders are ignored and do not receive a denial message. Bot-originated messages are ignored to avoid loops.

After your authorized account sends `/myid`, the Bot replies **in that same chat/Topic** with only the caller's `user_id`, `chat_id`, `chat_type` and `thread_id` (`none` outside a Topic). It does not reveal the configured allowlist, Bot Token, other users or AGY state.

**First-time setup (optional):** If you do not yet know your IDs, you may **temporarily** enable `"allow_unlisted_myid": true` in your local private config. This enables `/myid` **only for an unlisted sender's own 1:1 private chat** (`chat.type == "private"` and `chat.id == from.id`). No other commands, callbacks, Ask replies, task launches or group chats become authorized. For a fresh installation, `allowed_pairs: []` is permitted **only while this explicit discovery option is enabled**. Send `/myid` after the Bot has started, note the IDs **locally**, then set:

```json
{
  "allowed_pairs": [
    {"user_id": "YOUR_USER_ID", "chat_id": "YOUR_CHAT_ID"}
  ],
  "allow_unlisted_myid": false
}
```

The JSON above is an **excerpt** to merge into the existing config, not a replacement for the Token/workspace/model fields. Restart the Bot after editing the file, without running two long-pollers for the same Token. The discovery option is **false by default**. If you want a group/Topic Chat ID, first authorize your own user ID in that group's chat pair through private configuration; discovery does not allow arbitrary group probing. Never place actual numeric IDs in public GitHub, screenshots, PR comments or CI logs.

## Dependencies

See [Termux setup](docs/termux-setup.md) and the optional [media conversion guide](docs/youtube-media-conversion.md). These guides do not add attachment delivery to the Bot.

| Dependency | Installation | Notes |
| --- | --- | --- |
| Termux App | Official Termux source | Run as the ordinary Android App UID, **not root** |
| Python 3.10+ | `pkg install python` | The Bot only imports standard-library modules |
| Official Android ARM64 AGY CLI | Official CLI installation and authentication | `command -v agy`; confirm `agy -p "ping" --output-format json` works |
| Telegram Bot Token | Obtain from BotFather | Use a dedicated Bot and an explicit user/chat allowlist |
| Git | `pkg install git` | Only needed for cloning/updating files |

No `pip install` is necessary for this Bot. MCP also uses the current Termux Python interpreter. No dependency on private Linux quota utilities is included.

## Private configuration

```sh
pkg update
pkg install python git
git clone https://github.com/antidark0605/android-telegram-agent.git
cd android-telegram-agent
mkdir -p "$HOME/telegram-agent-workspace" "$HOME/.config/android-telegram-agent"
chmod 700 "$HOME/telegram-agent-workspace" "$HOME/.config/android-telegram-agent"
# Only initialize a new installation; never overwrite an existing private config.
if [ ! -e "$HOME/.config/android-telegram-agent/config.json" ]; then
  cp config.example.json "$HOME/.config/android-telegram-agent/config.json"
fi
chmod 600 "$HOME/.config/android-telegram-agent/config.json"
```

Replace placeholders in `$HOME/.config/android-telegram-agent/config.json` using a private editor and your actual **absolute Termux workspace path**. Never commit your Token or actual Telegram numeric IDs.

```json
{
  "bot_token": "YOUR_BOT_TOKEN",
  "allowed_pairs": [{"user_id": "YOUR_USER_ID", "chat_id": "YOUR_CHAT_ID"}],
  "agy_command": "agy",
  "workspace": "/ABSOLUTE/PRIVATE/TERMUX/WORKSPACE",
  "task_timeout_seconds": null,
  "print_timeout": "24h",
  "yolo": false,
  "allow_unlisted_myid": false
}
```

Run:
```sh
python bot.py --config "$HOME/.config/android-telegram-agent/config.json"
```

`task_timeout_seconds: null` means no Python-imposed task deadline. `print_timeout: "24h"` extends the **separate AGY Headless deadline**: it is **not literally unlimited**. The installed CLI must accept this duration. The Stop button remains available. Owners can choose another valid AGY duration. Device testing should verify actual long runs, cancellation and cleanup before assuming robust background behavior.

**YOLO warning:** `yolo: true` adds `--dangerously-skip-permissions` to AGY invocation, allowing AGY to auto-approve tool operations accessible to its Android UID. This greatly expands what remotely submitted prompts can do. Public sample is deliberately `false`. Enable it **only in a trusted, private deployment under explicit owner approval**, restrict Bot access with the user/chat allowlist, and use a dedicated workspace. A working directory is **not a filesystem security boundary**. The Bot does not change global AGY permission settings.

Private files beside `config.json`: `telegram-offset.json`, `sessions.json`, and `ask-bridge/` question/answer files. File writes use atomic replacement and mode 0600; related directories should remain mode 0700. The only local AGY configuration change is a scoped workspace MCP server declaration. Token, conversations, original prompts, OAuth material, personal device identifiers and logs must never enter public GitHub, Actions artifacts or PR comments.

## Private operational diagnostics

The Bot records safe, timestamped operational events to a **local file outside Git**:

```
$HOME/.config/android-telegram-agent/events.jsonl
```

The actual directory is derived from the private config path, so installations with a different config location will put the file beside that config. Each entry has a UTC timestamp and a fixed event name; optional fields include a **safe** HTTP status (e.g. 409), failure class (`http`, `transport`, `protocol`, `api`, `storage`), stage, retry delay, task duration and batch count. Sensitive strings are **not** logged: no Token, user/chat IDs, chat content, AGY prompts/replies, API response bodies, URLs, exception text, OAuth, session IDs or device identifiers.

Events include `poll_ok` (at most once per 60 seconds when idle), `poll_failed`, `inbound_authorized`, `send_attempt`, `send_ok`, `send_failed`, `task_started`, `task_finished`, `task_stop_requested`, `handler_failed`, `offset_failed`, `bot_started`, and `bot_stopped`. Failures from Telegram status-message edits and callbacks also have their own categories.

Logs use owner-only **0600** files, a **512 KiB** limit per file, and at most **two rotated backups** (`.1`, `.2`). They are best-effort and do not block Telegram operation if storage for diagnostics is unavailable.

Inspect locally, **never paste raw device logs into a public issue or CI**:

```sh
tail -n 80 "$HOME/.config/android-telegram-agent/events.jsonl"
```

**Troubleshooting a missing Telegram reply:** `poll_failed` with HTTP 409 suggests another long poller using the same Token; `poll_ok` without `inbound_authorized` calls for checking the authorized user/chat and message routing; `task_started` without `task_finished` indicates a running/stalled AGY task; `send_failed` means a Telegram outgoing request was not confirmed. `send_ok` is only Telegram API acknowledgement, **not proof that the recipient saw the message**. An `offset_failed` error stops the Bot rather than replaying potentially side-effecting commands indefinitely.

**Delivery limitation:** This revision adds diagnosis, **not** a durable, exactly-once outgoing-message queue. Telegram `sendMessage` has no built-in idempotency token; replaying an ambiguous failed network request can duplicate answers. A failed outgoing send is logged but is not automatically replayed, and the receiving user may need to request status again. This is an explicit limitation for Local Agent acceptance testing.

## Context and rate limit accuracy

The AGY JSON result has `usage.input_tokens`, `usage.output_tokens` and related token totals. These measure task processing, including possible multiple model calls; they **do not** represent current context occupancy. `/context` therefore shows only verifiable last-task counters and explicitly marks Context occupancy **unavailable** unless an independently verified live context source is added.

`/quota` calls standalone `agy -p "/usage" --output-format json` and
accepts only a successful official `command.name=usage` payload. Recognized
quota buckets provide numeric remaining fractions and timezone-aware reset
times; model-written response text is not used. Quota family selection uses
the current topic's selected model, and unknown families display unavailable.
The same cached provider supplies final-answer footers. See Response identity
and quota footer below for the cache and timeout rules.


## Tests and acceptance

Run synthetic no-network tests:
```sh
python -m compileall -q .
python -m unittest discover -s tests -v
```

**One-pass device acceptance plan:**

1. Verify a clean non-root Termux run, real Telegram text/AGY response and separate Bot status updates. Record only sanitized results.
2. Activate with `/session start`, select `/model`, then use plain DM twice and confirm AGY Conversation ID stays the same. Create two Telegram Topics; interleave prompts and confirm distinct IDs, model selection and answer routing; restart Bot and resume each Topic; `/new` resets only one.
3. Open `/model`, select a real model, check the **next AGY process argv** uses that slug, change another Topic and ensure no cross-over. Verify `/models`, `/model default` and stale/foreign/default buttons cannot change model state.
4. Verify this trusted deployment's `yolo` value and exact AGY argv flag, with a harmless controlled tool action. Do not run destructive prompts or modify global AGY settings.
5. Run an extended, non-destructive task: confirm status edits at ~5s intervals, press inline 🛑 Stop, ensure only that process group exits, old button is inert, and the next prompt succeeds. Confirm no `/cancel` command is offered or executed. Check AGY's own configured Headless ceiling.
6. Force AGY to invoke `ask_telegram` with two choices and then a free text question; verify topic routing, callback security, answer delivery, resumed task and Stop while waiting. Reboot/restart mid-question and ensure stale buttons do not resume another task.
7. Compare `/context` counters with a completed AGY JSON result, validate correct labeling, and check `/quota` displays the actual AGY report or honest unavailable state.
8. Check unknown sender/chat denial, malformed callbacks, repeated/pending updates, temporary Telegram network errors, background/screen-off operation, process and idle memory, and no leaked secrets in files/commit/CI logs.
9. Report a compact matrix with PASS/FAIL/NOT VERIFIED, generic durations and anonymized observed JSON field names. Keep full transcripts, raw stdout/stderr, private paths, IDs, Tokens and real device identifiers entirely in local private evidence.

**No public repository issue is required for private device feedback.**

## License

Original project code: MIT. Telegram, Termux and AGY are separate software with their own terms; their binaries and credentials are not redistributed.

## Response identity and quota footer

Revision 6 — 2026-10-14. Final answers show `⚡ [AGY] · <model>` without
the words "Selected model", followed by a Usage
blockquote. Official `agy -p /usage --output-format json` data supplies
5-hour and weekly remaining percentages and reset times for recognized
Gemini or Claude/GPT quota buckets. Responses within 60 seconds can reuse a
snapshot marked cached; the check timestamp is not displayed.
Quota lookup runs after AGY finishes, uses a 45-second timeout, and failures
show unavailable without discarding the answer. Unknown schemas and model
families are not guessed. Context occupancy remains unavailable: ordinary
AGY token counters do not establish context usage percentage.

Reference: [official CLI release notes](https://www.antigravity.google/docs/changelog/),
CLI v1.1.11 print-mode read-only slash commands. Official quota output is a
command payload; it is not parsed as a model-written quota answer.

Response headers and Usage footers retain a blank spacer paragraph around the
answer. Each Usage row is a separate quotation paragraph so Rich Markdown does
not fold soft line breaks into one continuous sentence.

## Incoming photos

Revision 5 — 2026-10-14. Send a Telegram photo with an optional caption after
activating the chat/topic and selecting a model. Without a caption, the agent
is asked to describe the photo. The largest available photo variant is fetched
using Telegram `getFile`, saved inside the workspace with owner-only access,
and passed to AGY as an explicit image path. The temporary image is removed
after the task, including failure. Forced termination or a filesystem error
can leave private files for manual cleanup; `incoming-images/` is Git-ignored.
Stop is checked between download reads; an in-progress network read can wait
up to its 30-second socket timeout. Downloads are limited to 10 MiB and PNG/JPEG.
Unlisted senders and busy or unactivated sessions cannot initiate downloads.
Only Telegram photo messages are supported; image documents and album grouping
are not implemented. Send one photo per task. Earlier ignored photos are not
replayed. A compatible AGY version and image-capable selected model are required.

References: [Telegram getFile](https://core.telegram.org/bots/api#getfile),
[AGY print-mode image example](https://github.com/google-antigravity/antigravity-cli/issues/735).
