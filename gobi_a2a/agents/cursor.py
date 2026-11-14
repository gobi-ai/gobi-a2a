"""Cursor's agent CLI (`cursor-agent`) behind A2A. A scaffold: off by default, one headless run per task.

    cursor-agent --print --output-format text [--model M] "<prompt>"

`--print` is Cursor's non-interactive mode. Nobody is there to approve a command, so in print mode Cursor may refuse
to change files or run commands: add "--force" to `command` in a2a.yaml where that is safe (it is Cursor's analogue of
Claude Code's bypassPermissions). Cursor signs in as it does on this machine (`cursor-agent login`, or CURSOR_API_KEY).

To grow it: `--output-format stream-json` gives one JSON event per line (parse it in place of CLIExecutor's line
notes), and `--resume <chat id>` continues a chat, like claude's session per A2A context.
"""
from __future__ import annotations

from pathlib import Path

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "Cursor Agent"
DESCRIPTION = "Cursor's coding agent (cursor-agent) working in its own folder."


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["cursor-agent", "--print", "--output-format", "text"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Add a test for ..."],
                       description="Runs Cursor's agent once per task in its own folder and returns what it printed. A message "
                                   "may pick the model: {\"model\": ...} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill)
