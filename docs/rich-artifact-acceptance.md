# Android v0.2: Telegram Rich Output and Artifact Acceptance

Revision: 4 — 2026-10-18

This is a **candidate**, not a deployment log. Use synthetic file names and
data. No real device model, hardware serial, company name, chat ID, Bot token,
personal path, private repository URL, OAuth credentials, chat content, or
raw Termux logs belong in this public document.

## Entry requirements

- Use one fixed PR commit SHA in a separate, non-root Termux checkout and
  private workspace. Preserve the existing running Bot.
- Only one active getUpdates poller per Bot Token. Use an isolated test Bot or
  coordinate an authorized switchover.
- Confirm the installed Bot API endpoint accepts sendRichMessage and Rich
  media uploads. New clients should render formatting; legacy clients may
  have differences, which must be noted.
- Python stdlib only; no special root, system or extra long-lived process.

## Acceptance cases

| ID | Scenario | Expected |
| --- | --- | --- |
| R01 | Short Chinese response with headings, **bold**, inline/fenced code | No raw Markdown symbols except deliberate code; Rich API used |
| R02 | Real table and numbered/bullet lists | Telegram Rich output correctly readable |
| R03 | Long multilingual answer above classic 4096 limit | All content delivered in order; no truncated Unicode |
| R04 | Private DM and forum Topic | Response and every artifact stay in original authorized chat/thread |
| R05 | Agent creates a synthetic PNG under artifacts/, declares it | One Telegram Rich media photo preview actually opens |
| R06 | Agent creates a PDF or TXT, declares it | Downloadable document actually reaches Telegram |
| R07 | Agent creates an MP3 or MP4, declares it | Playable or downloadable appropriate media |
| R08 | Multiple declarations, repeat same marker | Maximum 4 files, duplicates delivered once |
| R09 | Malformed marker, marker inside code fence | Unsafe marker omitted; fenced example untouched |
| R10 | Agent tries absolute path, traversal, symlink, outside artifacts/ | Rejected, no local private file sent or path leaked |
| R11 | File over limit or changed while delivering | Rejected or safe failure with no arbitrary upload |
| R12 | Explicit Rich unsupported HTTP 400/404 | Safe HTML text and standard sendDocument/sendPhoto fallback |
| R13 | Telegram timeout during output | No automatic duplicate or unsafe fallback; private log categorizes failure |
| R14 | Idle and active memory during multipart upload | Bounded memory and clean temporary resources |
| R15 | Existing /session, /model, /status, /myid, Stop and Ask | No regressions; exactly one task at a time |
| R16 | Public PR SHA and patch, workflow logs, filename/constants scan | No company/private/hardware/device, user identifiers or credentials |
| R17 | `/display` rich/classic/raw selected in two Topics, then `/new` | Mode is stored, survives conversation reset and applies only to the selected Topic; callback owner/chat/topic/message is checked; raw body is unformatted but media markers remain extracted metadata |
| R18 | Inbound document/photo with unsafe name, MIME, size, path and cancellation; `.md` as `text/markdown` and `text/plain` | Authorized task only; bounded private temp file; valid Markdown accepted; invalid input gets a generic authorized reply and is cleaned; unauthorized input stays silent |
| R19 | `/last` after a successful, failed and ambiguous send | Manual current-Topic resend is bounded/TTL-limited; no automatic exactly-once claim |
| R20 | Service start with persisted prior use, multiple Topics, stale authorization and send failure | After offset readiness, one notice per authorized used user/chat per process, routed to its most recent Topic; first evidence says ready, persisted prior readiness says restarted; reason stays unknown, model readiness is not claimed, and ambiguous failure is not retried in-process |

## Important behavior

- A generated file is not automatically uploaded. It must be declared using
  a standalone TAGW_MEDIA JSON line in the final response.
- Only workspace/artifacts/ is eligible. The bot must not guess private
  workspace paths or upload unlisted files.
- Rich uses structured markdown and embedded media when possible. Explicit
  unsupported responses may be downgraded to HTML + classic media APIs.
- No automatic retry on uncertain Telegram transport errors: receiving
  the bytes and losing the acknowledgement is possible.
- A Rich Message with large/complex Markdown may be split; a code fence
  spanning a chunk boundary is a known formatting limitation for follow-up.
- Telegram reports sending success, but only a real user can confirm display
  or download on a client.
- `/last` is a bounded manual resend record, not a durable outgoing media queue;
  ambiguous sends are never automatically replayed.

## Reporting

One private, sanitized result table: R01–R16, PASS/FAIL/NOT VERIFIED,
generic failure cause, fixed tested Commit SHA. Keep screenshots, tokens,
real paths/filenames, serial numbers, stdout/stderr and user conversations
**only in the private test environment**.

## Delivery regression checks

- Grow, truncate, or rewrite a synthetic artifact after the first upload chunk;
  delivery must fail without emitting bytes beyond the validated file size.
  Modification timestamps and file identity are checked before and during reads,
  including after the final chunk. Already transmitted bytes cannot be recalled;
  this is bounded streaming with mutation detection, not an atomic file snapshot.
  A bounded validation pass records SHA-256; the streamed bytes must match it
  before the multipart terminator is sent, including when timestamp resolution
  hides a same-size rewrite. This adds one full file read without buffering the
  whole file. A later source edit to bytes already sent does not invalidate those
  original validated bytes if neither metadata nor streamed content changes.
- Return a missing, boolean, nonpositive, or noninteger message ID for a later
  Rich chunk, fallback text chunk, or fallback attachment. Delivery must remain
  unconfirmed, with no automatic retry or protocol-error fallback. Every send
  requires its own valid message ID, including sends after the first success.
