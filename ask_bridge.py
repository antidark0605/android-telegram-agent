"""Private local filesystem bridge between Telegram polling and AGY stdio MCP."""
from __future__ import annotations
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path
from sessions import write_private

ID = re.compile(r"[0-9a-f]{32}")
TASK_ID = re.compile(r"[0-9a-f]{16}")

class AskBridge:
    def __init__(self, directory: Path):
        self.directory = directory
        self.questions = directory / "questions"
        self.answers = directory / "answers"
        self.active = directory / "active"
        for path in (directory, self.questions, self.answers, self.active):
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.chmod(0o700)

    def _managed_entry(self, value: object) -> bool:
        """Recognize this Bot's prior release entry without executing it."""
        if not isinstance(value, dict) or set(value) != {"command", "args"}:
            return False
        args = value.get("args")
        if (value.get("command") != sys.executable or not isinstance(args, list)
                or len(args) != 3 or not all(isinstance(item, str) for item in args)):
            return False
        script = Path(args[0])
        return (script.is_absolute() and script.name == "ask_bridge.py"
                and args[1] == "mcp" and args[2] == str(self.directory))

    def install(self, workspace: Path) -> None:
        """Merge one scoped MCP server into the workspace, never global settings."""
        agents = workspace / ".agents"
        if agents.is_symlink():
            raise ValueError("Workspace .agents directory cannot be a symbolic link.")
        agents.mkdir(mode=0o700, exist_ok=True)
        info = agents.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
                or not agents.resolve().is_relative_to(workspace.resolve())):
            raise ValueError("Workspace .agents directory must be private and owned by this user.")
        config = agents / "mcp_config.json"
        if config.is_symlink():
            raise ValueError("Workspace MCP configuration cannot be a symbolic link.")
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(config, flags)
        except FileNotFoundError:
            current = {}
        else:
            try:
                config_info = os.fstat(fd)
                if (not stat.S_ISREG(config_info.st_mode)
                        or config_info.st_uid != os.getuid()
                        or config_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                    raise ValueError("Workspace MCP configuration must be a private file.")
                with os.fdopen(fd, "r", encoding="utf8") as stream:
                    fd = -1
                    raw = stream.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError("Workspace MCP configuration is too large.")
                current = json.loads(raw)
            finally:
                if fd >= 0:
                    os.close(fd)
        if not isinstance(current, dict):
            raise ValueError("Invalid workspace MCP configuration.")
        servers = current.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("Invalid workspace MCP servers.")
        entry = {"command": sys.executable,
                 "args": [str(Path(__file__).resolve()), "mcp", str(self.directory)]}
        if ("telegram-ask" in servers and servers["telegram-ask"] != entry
                and not self._managed_entry(servers["telegram-ask"])):
            raise ValueError("Workspace has a conflicting telegram-ask MCP server.")
        servers["telegram-ask"] = entry
        if agents.is_symlink() or config.is_symlink():
            raise ValueError("Workspace MCP path changed during installation.")
        write_private(config, current)

    def begin(self, task_key: str, user: int, chat: int, topic: int | None) -> None:
        if not TASK_ID.fullmatch(task_key):
            raise ValueError("Invalid task routing key")
        write_private(self.active / (task_key + ".json"),
                      {"task": task_key, "user": user, "chat": chat,
                       "topic": topic})

    def end(self, task_key: str) -> None:
        try:
            (self.active / (task_key + ".json")).unlink(missing_ok=True)
        except OSError:
            pass
        for directory in (self.questions, self.answers):
            try:
                files = list(directory.iterdir())
            except OSError:
                continue
            for file in files:
                try:
                    data = json.loads(file.read_text(encoding="utf8"))
                    if data.get("task") == task_key:
                        file.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass

    def next_question(self, task_key: str) -> dict | None:
        for file in sorted(self.questions.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf8"))
                if data.get("task") != task_key:
                    continue
                dest = file.with_suffix(".sent")
                file.rename(dest)
                if ID.fullmatch(str(data.get("id", ""))):
                    return data
            except (OSError, ValueError):
                continue
        return None

    def answer(self, question: dict, value: str) -> None:
        qid = question["id"]
        if not ID.fullmatch(qid):
            return
        write_private(self.answers / (qid + ".json"),
                      {"task": question["task"], "id": qid, "answer": value})


def _response(message_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _ask(root: Path, args: dict) -> str:
    try:
        task_key = os.environ.get("ANDROID_TELEGRAM_AGENT_TASK_KEY", "")
        if not TASK_ID.fullmatch(task_key):
            return "(no active Telegram task)"
        active_file = root / "active" / (task_key + ".json")
        active = json.loads(active_file.read_text(encoding="utf8"))
        if not active.get("task") or type(active.get("chat")) is not int:
            return "(no active Telegram task)"
        question = str(args.get("question") or "").strip()
        if not question or len(question) > 3500:
            return "(a question of 1–3500 characters is required)"
        choices = args.get("choices") or []
        if not isinstance(choices, list) or len(choices) > 6:
            return "(choices must be an array of at most six options)"
        clean = []
        for choice in choices:
            if not isinstance(choice, dict):
                return "(invalid choice)"
            label = str(choice.get("label") or "").strip()
            description = str(choice.get("description") or "").strip()
            value = str(choice.get("value") or label).strip()
            if not (label and description and value) or max(map(len, (label, description, value))) > 250:
                return "(every choice requires a label, description and value)"
            clean.append({"label": label, "description": description, "value": value})
        qid = uuid.uuid4().hex
        payload = {**active, "id": qid, "question": question, "choices": clean}
        questions = root / "questions"
        answers = root / "answers"
        questions.mkdir(mode=0o700, parents=True, exist_ok=True)
        answers.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(questions / (qid + ".json"), payload)
        reply_file = answers / (qid + ".json")
        while True:
            if reply_file.exists():
                reply = json.loads(reply_file.read_text(encoding="utf8"))
                reply_file.unlink(missing_ok=True)
                if reply.get("task") == active["task"] and reply.get("id") == qid:
                    return str(reply.get("answer") or "")
            try:
                now = json.loads(active_file.read_text(encoding="utf8"))
                if now.get("task") != active["task"]:
                    return "(Telegram task ended)"
            except (OSError, ValueError):
                return "(Telegram task ended)"
            time.sleep(0.5)
    except (OSError, ValueError, TypeError):
        return "(Telegram question unavailable)"


def mcp_main(root: Path) -> None:
    """MCP stdio uses one JSON-RPC document per line, without extra stdout."""
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                continue
            method = req.get("method")
            mid = req.get("id")
            if mid is None:
                continue  # JSON-RPC notifications have no response.
            if method == "initialize":
                result = _response(mid, {
                    "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "telegram-ask", "version": "1.0"},
                })
            elif method == "tools/list":
                result = _response(mid, {"tools": [{
                    "name": "ask_telegram",
                    "description": "Ask this task's Telegram user and wait for their reply.",
                    "inputSchema": {"type": "object", "properties": {
                        "question": {"type": "string"},
                        "choices": {"type": "array", "items": {
                            "type": "object", "properties": {
                                "label": {"type": "string"}, "description": {"type": "string"},
                                "value": {"type": "string"}},
                            "required": ["label", "description", "value"]}}},
                        "required": ["question"]},
                }]})
            elif method == "tools/call" and req.get("params", {}).get("name") == "ask_telegram":
                args = req["params"].get("arguments", {})
                answer = _ask(root, args if isinstance(args, dict) else {})
                result = _response(mid, {"content": [{"type": "text", "text": answer}]})
            else:
                result = {"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32601, "message": "Unknown MCP method"}}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, AttributeError, KeyError):
            continue


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "mcp":
        mcp_main(Path(sys.argv[2]))
    else:
        raise SystemExit("Usage: python ask_bridge.py mcp <private-directory>")
