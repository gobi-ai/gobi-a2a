"""Cursor's agent CLI (`cursor-agent`) behind A2A. A scaffold: off by default, one headless run per task.

    cursor-agent --print --output-format stream-json --trust [--model M] [--resume <session>] -- "<prompt>"

`--print` is Cursor's non-interactive mode. `stream-json` prints one event per line: the session (`system` init), each
message (`assistant`) and tool call (`tool_call`), and the answer (`result`). The messages and tool calls are progress
notes, the answer is the result, and the session id is kept, so a follow-up in the same A2A context runs with
`--resume <session>` and continues the chat, as claude does.

`--trust` trusts the conversation's folder: without it, print mode stops in any folder it has not been told to trust,
and each conversation has a new one. Nobody is there to approve a command, so Cursor may still refuse to change files
or run commands: add "--force" to `command` in a2a.yaml where that is safe (it is Cursor's analogue of Claude Code's
bypassPermissions). Cursor signs in as it does on this machine (`cursor-agent login`, or CURSOR_API_KEY).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, Event, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "Cursor Agent"
DESCRIPTION = "Cursor's coding agent (cursor-agent) working in its own folder."
ARGS = ("command", "path", "pattern", "globPattern", "query", "url")   # what a tool call's note names, the first found


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["cursor-agent", "--print", "--output-format", "stream-json",
                                                        "--trust", "--", "{prompt}"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])
    resume_args: list[str] = Field(default_factory=lambda: ["--resume", "{session}"])


def events(e: dict[str, Any]) -> Event:
    """One line of `cursor-agent --print --output-format stream-json`."""
    kind, session = e.get("type"), e.get("session_id")
    if kind == "assistant":
        text = "".join(c.get("text", "") for c in (e.get("message") or {}).get("content") or [] if c.get("type") == "text")
        return Event(note=text, session=session)
    if kind == "tool_call" and e.get("subtype") == "started":
        return Event(note=tool_note(e.get("tool_call") or {}), session=session)
    if kind == "result":
        failed = e.get("is_error") or e.get("subtype") != "success"
        return Event(text=e.get("result"), error=e.get("result") if failed else None, usage=e.get("usage"), session=session)
    return Event(session=session)


def tool_note(call: dict[str, Any]) -> str:
    """'shell: npm test' from {"shellToolCall": {"args": {"command": "npm test"}}}."""
    if isinstance(call.get("tool"), dict):        # {"tool": {"case": "shellToolCall", "value": {...}}}
        call = {call["tool"].get("case", ""): call["tool"].get("value") or {}}
    key = next((k for k in call if k.endswith("ToolCall")), "")
    args = (call.get(key) or {}).get("args") or {}
    what = next((str(args[a]) for a in ARGS if args.get(a)), "")
    return f"{key.removesuffix('ToolCall') or 'tool'}: {what}" if what else key.removesuffix("ToolCall") or "tool"


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Add a test for ..."],
                       description="Runs Cursor's agent in its own folder and reports what it did. A follow-up in the same "
                                   "context continues the same chat. A message may pick the model: {\"model\": ...} in its "
                                   "metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill, events=events)
