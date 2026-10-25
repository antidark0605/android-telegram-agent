# android-telegram-agent

Revision: 14 — 2026-10-25

Small non-root **Termux + Telegram + official AGY CLI** Bot for Android. Original Python standard-library code, not a port of another private codebase. No root, inbound ports, OpenClaw, collaboration, kanban, server framework, or pip dependencies.

Coding and deployment agents must read the repository-wide [`AGENTS.md`](AGENTS.md)
before changing code, configuration or a device. Human installers can start with the
Termux and service guides linked below.

**Validation scope:** The basic Telegram ↔ AGY text round trip has been exercised in a non-root Termux environment. The integrated session, MCP and interactive controls require separate acceptance testing. Device-specific test environments, identifiers and results are intentionally kept outside this public repository.

## Telegram UX

| Telegram input | Behavior |
| --- | --- |
| Text | Require an activated session and explicit model, then run AGY and resume the prior conversation |
| /start, /help | Show available commands |
| /myid | Show the **current** Telegram user, chat type/ID and Topic ID, subject to the authorization policy |
| /new | Reset this Topic's AGY conversation (keeps its CWD, model and display selections) |
| /status | Show running/idle, CWD, native AGY conversation ID, selected model/variant and last reported model, YOLO setting |
| /model | Pick a model/variant from `agy models`; **per Topic** |
| /format | Pick `rich`, `classic` or `raw` (unformatted body) reply transport; **per Topic** |
| /last | Manually resend the bounded, private last reply for this Topic |
| /session start [CWD] | Activate the current chat/Topic without submitting a prompt; optionally select or create a private directory inside the configured workspace |
| /ls [PATH] | List any directory readable/searchable by the Bot's Termux user, using a formatted `ls -l` view with the resolved path |
| /context | Show last task's accounting tokens; **not** an invented context occupancy percentage |
| /quota, /usage | Request AGY's standalone /usage rate-limit report, if supported |
| Inline 🛑 中斷 (Stop) | Cancel that active AGY task; no /cancel command |

At startup, the Bot compares Telegram's default command menu with this implemented
command set, replaces stale metadata through `setMyCommands` only when needed, and
confirms the result through `getMyCommands`. A menu-sync failure is recorded without
blocking polling. Telegram stores this menu independently from message routing, so a
removed command can remain visible until this synchronization or a BotFather update.
The standard private/group category scopes are cleared to inherit the same default;
the Bot does not create per-chat or per-language command overrides.

Each task sends one formatted progress status message (state and elapsed seconds) with 🛑 中斷 (Stop), edited in place around every four seconds, and a separate final answer. Stop is tied to the authorized user, chat, exact status-message ID and unique task key. While the answer is being delivered the status shows *Delivering* without Stop; it shows completed only after Telegram acknowledges the answer, otherwise *Delivery unconfirmed*. Once finished, the button is removed. `/status` remains a plain-text summary. Each Topic has at most one active task. Different Topics may run concurrently up to `max_concurrent_tasks` (default 3); excess Topics are rejected before AGY starts. Stop, typing, Ask Telegram state, model/display pickers and cleanup remain scoped to the originating Topic.

**Topics:** A fresh chat/Topic requires `/session start` before ordinary text can run AGY. `/session start [CWD]` may select an existing directory or create a missing directory tree below the configured workspace; a relative path is resolved below that workspace, while an absolute path must remain inside the same boundary. Quoted paths with spaces are accepted. Existing ancestors are validated before creation, and each new directory is restricted to mode 0700. Inaccessible, non-directory, symlinked, foreign-owned or group/world-writable paths fail before activation and must be corrected and re-entered. Omitting `[CWD]` selects the configured workspace. An active Topic may be rebound with an explicit different CWD; rebinding clears its native conversation ID so the next task starts a new AGY conversation while preserving model and display selections. This activates the current scope; it does not create a Telegram Topic. A pre-existing saved AGY conversation remains activated. The private chat (no Topic) has its own separate Session. Each Telegram `message_thread_id` has a separate CWD, AGY conversation ID, Model selection and last task token accounting. A new Topic does not require creating another Bot Token. `/new` resets only the current Topic while preserving its CWD, model and display mode. Restarting the Bot preserves its Session mappings in a private JSON file. Invalid/corrupted state stops startup rather than silently erasing conversations; a failed persistence operation never updates the in-memory mapping.

While AGY is actively processing and has not entered final delivery, the Bot refreshes Telegram's `typing` chat action for the same chat and Topic every four seconds. Typing pauses while Ask Telegram is waiting for the user. Telegram documents that this status lasts no more than five seconds and clears when the Bot sends a message; chat-action failure is advisory and does not terminate the AGY task.

After polling state and the update offset are ready, the Bot sends one lifecycle notice per previously used, currently authorized user/chat pair, routed to that pair's most recently used Topic. The first persisted ready instance reports `ready`; later instances report `restarted`. The cause remains unknown without host evidence and the notice never claims a model is loaded. A send is attempted at most once per pair in one process; an unconfirmed attempt is not replayed in that process and may be tried again on the next start. Destinations are learned only from authorized inbound use, not by broadcasting to every allowlist entry.

**Model identification:** `/status` displays the configured or selected model, not a guessed effective model. Models selected through Telegram are passed as AGY's `--model` argument to the *next* run; an active task cannot change its model. If a validated AGY JSON result explicitly provides an `actual_model` field, the Bot saves it separately; without one it displays **not reported**. The former AGY default option only omitted `--model`; it did not identify a particular model. It has been removed, together with `/models` and `/model default`. Choose an explicit model before submitting tasks. Effort is part of the AGY model variant (for example High/Medium/Low), not a separate Bot setting. A native session ID remains pending until AGY returns a validated conversation ID. `/ls [PATH]` resolves relative paths from the current Topic CWD and accepts parent, absolute and symlinked paths when their resolved target is a directory that the Bot's Termux user can read and search. It displays the resolved path in the same title/path/code-block layout as the shared UI. This wider read-only listing scope does not change the stricter Session CWD, AGY workspace or attachment boundaries. Every HTML chunk is independently bounded by Telegram UTF-16 units; an explicit formatting rejection safely falls back to plain text, while an ambiguous network failure is not retried. It excludes hidden entries like `ls -l`; missing, inaccessible and non-directory targets fail before `ls` starts. Listings use a fixed argument vector with no shell, have a five-second deadline and bounded output. Listing failures do not expose stderr.

**Usage footer:** after a successful non-raw task, the Bot executes the configured AGY executable with the zero-turn `/usage` command and accepts only structured `SUCCESS`, `num_turns=0`, `command.name=usage` data. Gemini, Claude/GPT and Gemini 4 Argon groups remain separate and are selected from the task's own model. Argon displays `5h: N/A` and its independent Weekly fraction/reset with two-decimal precision; it never borrows the general Gemini pool. Context remains `unavailable`: task token accounting is not context occupancy. Missing, failed, denied or malformed usage data renders unavailable rather than reusing a different family.

### Ask Telegram

AGY can call a small **local MCP tool** named `ask_telegram` with `question` and optional `choices` (every choice requires label, description and value). The Bot forwards the question to the same chat/Topic with inline choice buttons or accepts a free-text reply, then returns the answer to the waiting AGY MCP process. A pending free-text reply is not interpreted as a new task.

On Bot startup and when a Topic CWD is activated, `AskBridge.install()` merges the single `telegram-ask` server into that private CWD's `.agents/mcp_config.json`, without touching global AGY settings. Existing unrelated MCP servers are preserved. Installation fails safely on a conflicting definition, a symlinked `.agents`/configuration path, or non-private ownership and permissions. The bridge stores only pending question/answer data in the private config directory, cleans it when a task ends and never exposes an HTTP listener. The installed AGY CLI must support workspace stdio MCP and load its configuration; **this must be verified on the device**.

## Unified text, Rich message and artifacts (v0.2 candidate)

The final **AGY response** is delivered via Telegram Bot API **10.3** \`sendRichMessage\`: headings, emphasis, lists, code blocks and tables are handled natively from \`rich_message.markdown\`. Slash commands and Ask Telegram questions still use the existing plain message API. The progress status message uses `sendMessage`/`editMessageText` with `parse_mode=HTML` (escaped text) and its inline keyboard; see Shared UI presentation below.

The Bot will attach a file only when the agent **explicitly declares it** on its own line in the final answer. For example, after creating a synthetic PDF under the current Topic CWD's \`artifacts/\` directory:

\`\`\`text
TAGW_MEDIA: {"path":"artifacts/report.pdf","kind":"auto"}
\`\`\`

- The artifact path must be **relative to the current Topic CWD** and start with \`artifacts/\`. The Topic CWD is revalidated inside the configured workspace before delivery. Never list arbitrary files, follow symlinks, or use paths outside this delivery root. Incoming marker metadata can **never** choose another Telegram chat, user, or Topic.
- A maximum of **4 artifacts**, each at most **20 MiB** and **40 MiB total** per reply, are allowed. The file must exist and be a regular file. Unsafe markers produce a generic omission notice without disclosing the requested path.
- PNG/JPEG/WebP typically become in-chat photos, MP3/M4A become audio, MP4 video, and PDF/ZIP/TXT/HTML or unknown file types become documents. The output may combine markdown and multiple media attachments into one Rich Message.
- The Bot sends files using bounded-memory **multipart/form-data streaming** with 64 KiB file chunks. It does not load whole media files into memory, unlike typical simple multipart examples.
- If Telegram explicitly rejects the Rich call with HTTP **400 or 404**, the Bot falls back to \`sendMessage\` with safe limited HTML formatting, followed by classic \`sendDocument\`/\`sendPhoto\`/\`sendAudio\`/\`sendVideo\` for declared files. **An ambiguous network failure is not automatically retried** because Telegram may already have accepted the request.
- Longer answers are split into pieces before reaching rich formatting limits. Some complex Markdown spanning split boundaries may render less perfectly than a short single-message answer. The fallback HTML parser intentionally supports only a readable subset of Markdown.

Never include secrets in artifacts. Telegram group members may see results even if the Bot only executes commands from whitelisted senders. Private credentials, workspaces, personal files, OAuth state, raw logs and physical test-device information must not be committed into the public repository.

Inbound photos and documents are accepted only after the same exact user/chat/topic authorization and session/model gates as text. Photos are limited to 20 MiB at the Bot API download boundary and PNG/JPEG/WebP signatures; documents require an allowlisted MIME type, a matching safe filename suffix and the same 20 MiB bound. Markdown accepts `.md` with `text/markdown` or Telegram's common `text/plain` classification. Authorized users receive a generic rejection for unsupported attachments; unauthorized senders remain silent. Temporary files are owner-only under `incoming-media/` (the legacy photo wrapper uses `incoming-images/`) and are removed after the task. The bot never accepts a model-supplied destination chat or filesystem path.

**Important:** GitHub mock CI verifies request structure, safety and fallbacks. Actual **Telegram 10.3 rich media delivery**, client rendering, album behavior, and local CLI artifact generation must be confirmed by Local Agent on the device before production deployment.

`/last` is a manual resend of the most recent rendered response for the current chat/Topic. It is private, bounded to 32 entries and expires after 24 hours. It is not an exactly-once queue: ambiguous network sends are never replayed automatically.

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

See [Termux setup](docs/termux-setup.md) and the optional [media conversion guide](docs/youtube-media-conversion.md). The Bot's inbound/outbound attachment contract is part of this checkout; the media conversion guide only describes external conversion dependencies.

| Dependency | Installation | Notes |
| --- | --- | --- |
| Termux App | Official Termux source | Run as the ordinary Android App UID, **not root** |
| Python 3.10+ | `pkg install python` | The Bot only imports standard-library modules and the vendored `vendor/telegram_ui` sources |
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
  "max_concurrent_tasks": 3,
  "print_timeout": "24h",
  "yolo": false,
  "allow_unlisted_myid": false
}
```

Run:
```sh
python bot.py --config "$HOME/.config/android-telegram-agent/config.json"
```

For a repeatable home-device installation, pinned upgrades, rollback, safe doctor
output and a `termux-services` template, follow
[`docs/service-setup.md`](docs/service-setup.md). The release tools never copy or
modify private config, workspace, credentials or session state, and never restart a
service automatically.

`task_timeout_seconds: null` means no Python-imposed task deadline. `print_timeout: "24h"` extends the **separate AGY Headless deadline**: it is **not literally unlimited**. The installed CLI must accept this duration. The Stop button remains available. Owners can choose another valid AGY duration. Device testing should verify actual long runs, cancellation and cleanup before assuming robust background behavior.

`max_concurrent_tasks` accepts 1–8 and bounds the number of Topics that may run AGY simultaneously. Use `1` on memory-constrained devices; the default `3` matches the shared Telegram gateway's bounded worker default. This limit does not merge Topic sessions: each Topic keeps its own conversation, CWD, model and controls.

**YOLO warning:** `yolo: true` adds `--dangerously-skip-permissions` to AGY invocation, allowing AGY to auto-approve tool operations accessible to its Android UID. This greatly expands what remotely submitted prompts can do. Public sample is deliberately `false`. Enable it **only in a trusted, private deployment under explicit owner approval**, restrict Bot access with the user/chat allowlist, and use a dedicated workspace. A working directory is **not a filesystem security boundary**. The Bot does not change global AGY permission settings.

Private files beside `config.json`: `telegram-offset.json`, `sessions.json`, bounded `last.json`, `lifecycle.json`, and `ask-bridge/` question/answer files. The lifecycle file contains only authorized-used routing integers, recency ordering and readiness evidence; it contains no token, host, PID, path or message content. File writes use atomic replacement and mode 0600; related directories should remain mode 0700. The only local AGY configuration change is a scoped workspace MCP server declaration. Token, conversations, original prompts, OAuth material, personal device identifiers and logs must never enter public GitHub, Actions artifacts or PR comments.

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
2. Activate with `/session start`, select `/model`, choose each `/format` mode, then use plain DM twice and confirm AGY Conversation ID stays the same. Create two Telegram Topics; interleave prompts and confirm distinct IDs, display mode, model selection and answer routing; restart Bot and resume each Topic; `/new` resets only one; `/last` only resends the current Topic's bounded saved answer.
3. Open `/model`, select a real model, check the **next AGY process argv** uses that slug, change another Topic and ensure no cross-over. Verify `/models` is unknown, `/model default` is rejected and stale/foreign buttons cannot change model state.
4. Verify this trusted deployment's `yolo` value and exact AGY argv flag, with a harmless controlled tool action. Do not run destructive prompts or modify global AGY settings.
5. Run an extended, non-destructive task: confirm status edits at ~5s intervals, press inline 🛑 Stop, ensure only that process group exits, old button is inert, and the next prompt succeeds. Confirm no `/cancel` command is offered or executed. Check AGY's own configured Headless ceiling.
6. Force AGY to invoke `ask_telegram` with two choices and then a free text question; verify topic routing, callback security, answer delivery, resumed task and Stop while waiting. Reboot/restart mid-question and ensure stale buttons do not resume another task.
7. Compare `/context` counters with a completed AGY JSON result, validate correct labeling, and check `/quota` displays the actual AGY report or honest unavailable state.
8. Check unknown sender/chat denial, malformed and foreign-topic callbacks, repeated/pending updates, photo/document MIME and size rejection, temporary Telegram network errors including safe 429 backoff, background/screen-off operation, lifecycle notice de-duplication, process and idle memory, and no leaked secrets in files/commit/CI logs.
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

## Shared UI presentation

Revision 1 — 2026-10-17. Reply identity, Usage and progress text are rendered
by the vendored public library in `vendor/telegram_ui` (telegram-agent-ui
v0.2.0, pinned commit and MIT license in [vendor/README.md](vendor/README.md)).
The sources are unmodified and pure standard-library Python; no `pip install`
is needed. `presentation.py` maps Bot state onto it:

- Final answers use `render_reply`: the header names the model the task ran
  with (Markdown-escaped; invalid labels show `unavailable`), the AGY answer
  body follows unchanged so standalone `TAGW_MEDIA` markers are still
  extracted, then the `render_usage` footer. Quota numbers and reset times come
  only from the existing `/usage` parser and 60-second cache; no extra call is
  made. Context stays unavailable: task token totals are not occupancy.
- Raw mode means the answer body receives no presentation formatting. Standalone
  `TAGW_MEDIA` lines remain delivery metadata: they are extracted through the
  same safe artifact path and are not displayed as body text.
- Progress uses `render_progress` without a flow line (AGY reports no stage
  events) for running, waiting, completed, cancelled and failed. *Delivering*
  is shown as running with a delivery activity line and no Stop button;
  *Delivery unconfirmed* is a terminal failed state with an explicit line and
  is never shown as completed. The upstream `task:cancel:<id>` button is
  translated to the Bot's `stop:<id>`; all Stop checks are unchanged.
- The status Markdown is converted with the conservative HTML fallback used for
  rich-delivery fallbacks. If Telegram rejects the Rich answer, that fallback
  shows the header's Markdown escapes (for example `gemini\-x`) literally.
- Plain-text messages are split with the library's UTF-16-aware `split_text`,
  so emoji and other non-BMP characters count as Telegram counts them and are
  never cut. It preserves every character but does not repair Markdown, code
  fences or tables across chunk boundaries.

Device rendering of the new status and header formats has not been verified.

## Incoming photos and documents

Revision 7 — 2026-10-18. Send a Telegram photo or allowlisted document with an optional caption after
activating the chat/topic and selecting a model. Without a caption, the agent
is asked to describe the photo. The largest available photo variant is fetched
using Telegram `getFile`, saved inside the current Topic CWD with owner-only access,
and passed to AGY as an explicit image path. The temporary image is removed
after the task, including failure. Forced termination or a filesystem error
can leave private files for manual cleanup; `incoming-images/` is Git-ignored.
Stop is checked between download reads; an in-progress network read can wait
up to its 30-second socket timeout. Downloads are limited to 20 MiB; photos
must carry PNG/JPEG/WebP signatures and documents must carry an allowlisted MIME
type and matching safe filename suffix. Markdown documents accept `.md` with
`text/markdown` or `text/plain`. The temporary file is removed after the task.
Unlisted senders and busy or unactivated sessions cannot initiate downloads.
Albums and multi-file messages are not implemented. Send one attachment per
task. Earlier ignored attachments are not replayed. A compatible AGY version
and image/file-capable selected model are required.

References: [Telegram getFile](https://core.telegram.org/bots/api#getfile),
[AGY print-mode image example](https://github.com/google-antigravity/antigravity-cli/issues/735).
