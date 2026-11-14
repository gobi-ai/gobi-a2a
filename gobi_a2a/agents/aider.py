"""Aider behind A2A. A scaffold: off by default, one `aider --message` run per task.

    aider --yes-always --no-pretty --no-stream --no-check-update --message "<prompt>" [--model M]

`--message` runs one instruction and exits; `--yes-always` answers aider's questions (creating a git repo in the
folder, adding files), since nobody is there to. Aider keeps its chat history in the folder, and a follow-up in the same
A2A context works in the same folder: add "--restore-chat-history" to `command` to carry the conversation on. Aider
reads its API keys from the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) or `env:` in a2a.yaml.
"""
from __future__ import annotations

from pathlib import Path

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "Aider"
DESCRIPTION = "Aider, the AI pair programmer, editing code in its own folder (a git repo of its own)."


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["aider", "--yes-always", "--no-pretty", "--no-stream",
                                                        "--no-check-update", "--message", "{prompt}"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Edit code in a git repo", tags=["coding", "git"], examples=["Refactor ... into ..."],
                       description="Runs aider once per task in its own folder and returns what it printed. A message may "
                                   "pick the model: {\"model\": ...} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill)
