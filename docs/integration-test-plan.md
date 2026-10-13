# One-pass Android AGY Telegram acceptance plan

Revision: 2 (2026-10-13) — quota schema, session persistence, and failure handling.

Run once against the same PR build in isolated non-root Termux. Preserve the existing running Bot; only one poller per Bot token. Do not publish secrets or raw private transcripts.

| Case | Test | Expected |
| --- | --- | --- |
| A01 | Non-root boot, /start, /status, text | Real cloud AGY response in original chat |
| A02 | Two successive messages, same Topic | Explicit conversation ID reused |
| A03 | Alternate Topic A and B, then /new in A | Separate AGY conversations, B unchanged |
| A04 | /models and /model switch in Topic A | Real selected AGY model used only by A |
| A05 | Restart Bot and repeat A/B | Model and session state preserved |
| A06 | More than 45 seconds task | No Python deadline when config is null; AGY print ceiling verified |
| A07 | Timer update, press inline Stop | Same status message edited, owned process group ended |
| A08 | Wrong user, old button, wrong chat/topic | Rejected, does not affect new task |
| A09 | YOLO harmless approved command | Explicit private opt-in flag, no global settings changed |
| A10 | MCP ask with two descriptive choices | Same Topic shows question, button answer resumes original task |
| A11 | MCP ask with free text, Stop while waiting | Answer not interpreted as new task, Stop terminates wait |
| A12 | /context and /quota | Verified token counts; quota only for successful AGY terminal results with approved structured numerical rate-limit fields, else unavailable |
| A13 | Concurrency, network retry, background, screen off | No duplicate requests, memory/process lifetime observed |
| A14 | Public commits, workflows and logs | No private URLs, account identifiers, device IDs, or credentials |
| A15 | Simulate Telegram HTTP 409, network error, transient send failure | Private JSONL distinguishes poll and send failures, records safe HTTP code and UTC timestamp; no raw exception, Token, URL or message text |
| A16 | Corrupt or unwritable polling-offset state | Bot safely refuses to reset/replay updates silently; `offset_failed` captures stage and the error remains distinguishable from network failure |
| A17 | Keep Bot idle and then run a long task | First `poll_ok` is visible, subsequent idle heartbeat is throttled (about 60 seconds), and task start/finish duration and send outcome appear without secret data |
| A18 | Generate enough synthetic operational events for rotation | All active/backup log files stay private and bounded; logging failure does not break Telegram operation |
| A19 | Simulate quota CLI status ERROR, SUCCESS with denied_actions, arbitrary response text, malformed numeric rate data | Every case displays unavailable; no arbitrary CLI response is forwarded to Telegram |
| A20 | Simulate SessionStore.change/reset I/O failure and a corrupt persistent state file | No in-memory/disk divergence, corrupt state blocks startup, and no silent loss of the session mapping |
| A21 | Let AGY return SUCCESS then fail session persistence | Telegram status and structured log mark task as failed; reply explains conversation continuity cannot be guaranteed, Bot returns idle |

For each case report PASS/FAIL/NOT VERIFIED with a short anonymized diagnosis in the private feedback channel. Store real evidence only locally. Do not merge until blocking cases are reviewed.

Known limits: AGY print timeout is finite, context task usage is not context occupancy, terminal process group cleanup cannot cover deliberately detached children, and workspace MCP support depends on the installed CLI.
