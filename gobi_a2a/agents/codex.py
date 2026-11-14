"""OpenAI's Codex CLI (`codex exec`) behind A2A. A scaffold: off by default, one non-interactive run per task.

    codex exec --json --skip-git-repo-check --sandbox workspace-write --color never [--model M]
        [-c model_reasoning_effort=E] [resume <session>] -- "<prompt>"

`exec` runs the prompt to the end without asking anything. `--json` prints one event per line: the session
(`thread.started`), each command, file change and message (`item.*`), the usage (`turn.completed`) or the error
(`turn.failed`). The commands and changes are progress notes, the last message is the result, and the session id is
kept, so a follow-up in the same A2A context runs `codex exec resume <session>` and continues the conversation, as
claude does.

`--skip-git-repo-check` lets it work in a conversation's folder, which is not a git repo. `--sandbox workspace-write`
lets it change files in that folder and run commands there, without network; `danger-full-access` in `command` lifts
the sandbox, Codex's counterpart to Claude Code's bypassPermissions. Codex signs in as it does on this machine
(`codex login`, or CODEX_API_KEY in `env:`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, Event, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "Codex"
DESCRIPTION = "OpenAI's Codex CLI (codex exec) working in its own folder."


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox",
                                                        "workspace-write", "--color", "never", "--", "{prompt}"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])
    effort_args: list[str] = Field(default_factory=lambda: ["-c", "model_reasoning_effort={effort}"])
    resume_args: list[str] = Field(default_factory=lambda: ["resume", "{session}"])


def events(e: dict[str, Any]) -> Event:
    """One line of `codex exec --json`."""
    kind, item = e.get("type"), e.get("item") or {}
    if kind == "thread.started":
        return Event(session=e.get("thread_id"))
    if kind == "turn.completed":
        return Event(usage=e.get("usage"))
    if kind == "turn.failed":
        return Event(error=(e.get("error") or {}).get("message"))
    if kind == "error":                           # a retry, or the error turn.failed repeats
        return Event(note=e.get("message"))
    what = item.get("type")
    if kind == "item.started" and what == "command_execution":
        return Event(note=f"$ {item.get('command', '')}")
    if kind == "item.started" and what == "web_search":
        return Event(note=f"Searching the web: {item.get('query', '')}")
    if kind == "item.started" and what == "mcp_tool_call":
        return Event(note=f"Calling {item.get('server', '')}.{item.get('tool', '')}")
    if kind == "item.completed" and what == "file_change":
        return Event(note="Changed " + ", ".join(c.get("path", "") for c in item.get("changes") or []))
    if kind == "item.completed" and what == "agent_message":
        return Event(note=item.get("text"), text=item.get("text"))
    if kind == "item.completed" and what == "error":
        return Event(note=item.get("message"))
    return Event()


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Write a script that ..."],
                       description="Runs Codex in its own folder and reports what it did. A follow-up in the same context "
                                   "continues the same session. A message may pick the model and effort: "
                                   "{\"model\": ..., \"effort\": ...} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill, events=events)
