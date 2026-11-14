"""omp (oh-my-pi, the pi coding agent) behind A2A. A scaffold: off by default, one print-mode run per task.

    omp -p "<prompt>" [--model M] [--thinking E]

`-p` is pi's non-interactive print mode: it runs the prompt to the end and prints the answer. omp signs in to its
providers as it does on this machine. The flags follow pi's CLI; check `omp --help` on yours and change `command`,
`model_args` and `effort_args` in a2a.yaml if they differ.

To grow it: pi's `--mode json` prints one JSON event per line (parse it in place of CLIExecutor's line notes), and its
sessions can be continued, like claude's session per A2A context.
"""
from __future__ import annotations

from pathlib import Path

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, build_cli
from gobi_a2a.agents.common import Mounted

NAME = "omp"
DESCRIPTION = "omp (oh-my-pi), a terminal coding agent, working in its own folder."


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["omp", "-p", "{prompt}"])
    model_args: list[str] = Field(default_factory=lambda: ["--model", "{model}"])
    effort_args: list[str] = Field(default_factory=lambda: ["--thinking", "{effort}"])


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Fix the failing test in ..."],
                       description="Runs omp once per task in its own folder and returns what it printed. A message may pick "
                                   "the model and effort: {\"model\": ..., \"effort\": ...} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill)
