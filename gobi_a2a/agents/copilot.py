"""GitHub Copilot CLI (`copilot -p`) behind A2A. A scaffold: off by default, one non-interactive run per task.

    copilot --prompt=<prompt> --silent --no-color --allow-all-tools --allow-all-urls --no-ask-user --autopilot
        [--model=M] [--reasoning-effort=E] --session-id=<uuid>

`--prompt` runs the prompt to the end and exits; `--silent` prints only the agent's answer (no usage summary), which is
the result, each line of it a progress note as it streams. The prompt is the value of `--prompt=`, one argument, so a
prompt that starts with "-" is never read as an option.

Nobody is there to approve anything, and without permission flags `-p` denies every request that needs approval:
`--allow-all-tools` lets Copilot run its tools (shell, write, ...) without asking, within the conversation's folder
(its default path check), `--allow-all-urls` lets it fetch from the web (as claude's WebFetch may), `--no-ask-user`
keeps it from pausing for a question, and `--autopilot` lets it keep going until it calls the task done. Copilot has
no `--yes`: these are its unattended mode. `--allow-all` (or `--yolo`) in `command` also lifts the path check:
Copilot's counterpart to Claude Code's bypassPermissions, only where Copilot may do anything the account running the
gateway can.

The model and the effort are a2a.yaml's, or a message's: {"model": "claude-sonnet-4.5", "effort": "high"} in its
metadata ("reasoning_effort" works too) runs that one task with `--model=claude-sonnet-4.5 --reasoning-effort=high`.
Any model name Copilot knows passes (gpt-5, claude-sonnet-4, auto, ...); Copilot itself says when it does not know one.

Copilot does not print its session id in this mode, but it takes one: `--session-id <uuid>` opens that session, or
starts it when there is none. The first task in an A2A context runs with a new UUID and each follow-up, or a steer sent
while a run is going (it waits its turn), with the same one, so the conversation continues, as claude does. The id is
kept on disk, so this holds across a gateway restart too, and a failed run keeps it (a rate limit does not cost the
conversation its memory). A message may name the session instead: {"session": "<id>"} resumes that one (an exact id,
such as one Copilot listed or a task's metadata reported), {"session": "new"} starts afresh. Each task's final status
metadata says which session it ran in.

Copilot signs in as it does on this machine (`copilot login`, or COPILOT_GITHUB_TOKEN / GH_TOKEN in `env:`; a
fine-grained token with the "Copilot Requests" permission, not a classic `ghp_` one). Install the CLI itself:
`npm install -g @github/copilot` or `brew install copilot-cli`. The `copilot` that VS Code's Copilot Chat puts on PATH
is a shim (in .../globalStorage/github.copilot-chat/copilotCli) that asks on stdin to install the CLI when it is missing
and exits 0 even when a run fails, so it is left off the PATH a run sees: without the real CLI, a task fails and says
it is not installed.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

from a2a.types import AgentSkill
from pydantic import Field

from gobi_a2a.agents.cli import CLIConfig, build_cli
from gobi_a2a.agents.common import Mounted

log = logging.getLogger("gobi_a2a.copilot")

NAME = "GitHub Copilot CLI"
DESCRIPTION = "GitHub Copilot CLI (copilot) working in its own folder."
SHIM_DIR = ("github.copilot-chat", "copilotCli")  # where VS Code's Copilot Chat keeps its `copilot` shim


class Config(CLIConfig):
    command: list[str] = Field(default_factory=lambda: ["copilot", "--prompt={prompt}", "--silent", "--no-color",
                                                        "--allow-all-tools", "--allow-all-urls", "--no-ask-user",
                                                        "--autopilot"])
    model_args: list[str] = Field(default_factory=lambda: ["--model={model}"])
    effort_args: list[str] = Field(default_factory=lambda: ["--reasoning-effort={effort}"])
    resume_args: list[str] = Field(default_factory=lambda: ["--session-id={session}"])


def new_session() -> str:
    """The id a conversation's first task gives Copilot: a UUID, so Copilot starts that session."""
    return str(uuid.uuid4())


def is_shim(folder: str) -> bool:
    return Path(os.path.normpath(folder)).parts[-2:] == SHIM_DIR


def without_shim(path: str) -> str:
    """PATH without VS Code's `copilot` shim folder, as the shim itself searches it for the real CLI."""
    return os.pathsep.join(d for d in path.split(os.pathsep) if d and not is_shim(d))


def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None) -> Mounted:
    path = cfg.env.get("PATH", os.environ.get("PATH", ""))
    if any(is_shim(d) for d in path.split(os.pathsep) if d):
        cfg = cfg.model_copy(update={"env": {**cfg.env, "PATH": without_shim(path)}})
        if shutil.which(cfg.command[0], path=cfg.env["PATH"]) is None and shutil.which(cfg.command[0], path=path):
            log.warning("%s: the only %r on PATH is VS Code's Copilot Chat shim, which is not the CLI: install it "
                        "(npm install -g @github/copilot, or brew install copilot-cli) or point command at it",
                        name, cfg.command[0])
    skill = AgentSkill(id="code", name="Code in a folder", tags=["coding"], examples=["Fix the failing test in ..."],
                       description="Runs GitHub Copilot CLI in its own folder, unattended, and reports what it did. A "
                                   "follow-up in the same context continues the same session. A message may pick the "
                                   "model, the effort and the session: {\"model\": \"claude-sonnet-4.5\", \"effort\": "
                                   "\"high\", \"session\": \"<id>\" or \"new\"} in its metadata.")
    return build_cli(name, cfg, url=url, workdir=workdir, auth=auth, token=token, title=NAME, description=DESCRIPTION,
                     skill=skill, new_session=new_session)
