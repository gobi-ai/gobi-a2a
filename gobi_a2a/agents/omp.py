"""omp (oh-my-pi, the pi coding agent) behind A2A. A scaffold: off by default, one non-interactive run per task.

    omp --mode json [--model M] [--thinking E] [--resume <session>] -- "<prompt>"

`--mode json` runs the prompt to the end without asking anything and prints one event per line: first the session
header (`{"type": "session", "id": ...}`), then each tool call (`tool_execution_start`) and message (`message_end`).
The tool calls are progress notes, the last assistant text is the result, and the session id is kept, so a follow-up
in the same A2A context runs with `--resume <session>` and continues the conversation, as claude does. omp finds the
session among those of the folder it runs in, which is the conversation's own.

omp signs in to its providers as it does on this machine (`omp login`: a ChatGPT, Copilot or Gemini subscription, or
an API key). It runs on bun, which must be installed too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, Event, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "omp"
DESCRIPTION = "omp (oh-my-pi), a terminal coding agent, working in its own folder."
ARGS = ("command", "path", "pattern", "query", "url")   # what a tool call's note names, the first found
FAILED = ("error", "aborted")                           # stop reasons that end a run short of an answer


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["omp", "--mode", "json", "--", "{prompt}"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])
    effort_args: list[str] = Field(default_factory=lambda: ["--thinking", "{effort}"])
    resume_args: list[str] = Field(default_factory=lambda: ["--resume", "{session}"])


def events(e: dict[str, Any]) -> Event:
    """One line of `omp --mode json`."""
    kind = e.get("type")
    if kind == "session":
        return Event(session=e.get("id"))
    if kind == "tool_execution_start":
        args = e.get("args") if isinstance(e.get("args"), dict) else {}
        what = e.get("intent") or next((str(args[a]) for a in ARGS if args.get(a)), "")
        return Event(note=f"{e.get('toolName', 'tool')}: {what}" if what else e.get("toolName", "tool"))
    if kind == "auto_retry_start":
        return Event(note=f"Retrying ({e.get('attempt')}/{e.get('maxAttempts')}): {e.get('errorMessage', '')}")
    if kind == "notice":
        return Event(note=e.get("message"))
    m = e.get("message") or {}
    if kind == "message_end" and m.get("role") == "assistant":
        text = "".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text").strip()
        failed = m.get("stopReason") in FAILED
        return Event(note=text or None, text=text or None, usage=m.get("usage"),
                     error=(m.get("errorMessage") or f"stopped: {m.get('stopReason')}") if failed else None)
    return Event()


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Fix the failing test in ..."],
                       description="Runs omp in its own folder and reports what it did. A follow-up in the same context "
                                   "continues the same session. A message may pick the model and effort: "
                                   "{\"model\": ..., \"effort\": ...} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill, events=events)
