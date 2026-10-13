# One-pass Android AGY Telegram acceptance plan

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
| A12 | /context and /quota | Verified token counts, correct labels, rate data or honest unavailable |
| A13 | Concurrency, network retry, background, screen off | No duplicate requests, memory/process lifetime observed |
| A14 | Public commits, workflows and logs | No private URLs, account identifiers, device IDs, or credentials |

For each case report PASS/FAIL/NOT VERIFIED with a short anonymized diagnosis in the private feedback channel. Store real evidence only locally. Do not merge until blocking cases are reviewed.

Known limits: AGY print timeout is finite, context task usage is not context occupancy, terminal process group cleanup cannot cover deliberately detached children, and workspace MCP support depends on the installed CLI.
