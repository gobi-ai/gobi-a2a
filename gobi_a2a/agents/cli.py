"""A coding agent that is a command line program, behind A2A: the base of the cursor, aider and omp scaffolds.

One A2A task is one run of the command, in the A2A context's own folder (a follow-up in the same context works in the
same folder, so whatever the tool keeps there, its chat history or a git repo, carries over). While it runs, each line
it prints is a status message; when it exits 0, what it printed is the result artifact; any other exit fails the task
with the end of its stderr. A cancel stops the process (SIGTERM, then SIGKILL).

The command is an argv list, never a shell line, so the prompt cannot become shell syntax. "{prompt}" in it is replaced
by the task's text; without one, the text is the last argument. "{model}" and "{effort}" are replaced in `model_args`
and `effort_args`, which are added only when a model or effort is set (in a2a.yaml, or by the message's metadata, as for
claude).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentSkill, TaskState
from google.protobuf.json_format import MessageToDict
from pydantic import Field, field_validator

from gobi_a2a.agents.common import (NOTE_CHARS, AgentConfig, BadOverride, Mounted, a2a_app, card, check_effort, check_model,
                                    overrides, workspace)

log = logging.getLogger("gobi_a2a.cli")

MAX_OUTPUT = 512 * 1024                           # what the result artifact keeps of stdout, at most
STDERR_TAIL = 4000                                # what a failure reports of stderr
KILL_AFTER = 5.0                                  # seconds between SIGTERM and SIGKILL on cancel


class CLIConfig(AgentConfig):
    command: list[str]                            # argv; "{prompt}" is the task's text, else it is appended
    model: str | None = None                      # default model; a message may pick another
    model_args: list[str] = Field(default_factory=list)   # e.g. ["--model", "{model}"], added when a model is set
    effort: str | None = None
    effort_args: list[str] = Field(default_factory=list)  # e.g. ["--thinking", "{effort}"], added when an effort is set
    timeout: float | None = None                  # seconds; None: no limit
    env: dict[str, str] = Field(default_factory=dict)     # added to the gateway's environment for the run

    @field_validator("command")
    @classmethod
    def _command(cls, v: list[str]) -> list[str]:
        if not v or not all(isinstance(a, str) for a in v) or not v[0].strip():
            raise ValueError("command must be a non-empty list of strings, the program first")
        return v

    @field_validator("model", mode="before")
    @classmethod
    def _model(cls, v: Any) -> str | None:
        return check_model(v)

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, v: Any) -> str | None:
        return check_effort(v)

    @field_validator("timeout", mode="before")
    @classmethod
    def _timeout(cls, v: Any) -> Any:
        return None if v in (None, "", 0) else v


@dataclass
class CLISettings:
    workdir: Path
    command: list[str]
    label: str = "the agent"                      # what failure messages call it
    model: str | None = None
    model_args: list[str] = field(default_factory=list)
    effort: str | None = None
    effort_args: list[str] = field(default_factory=list)
    timeout: float | None = None
    env: dict[str, str] = field(default_factory=dict)

    def argv(self, prompt: str, model: str | None = None, effort: str | None = None) -> list[str]:
        """The command for one run. Placeholders are whole-argument or in-argument; nothing goes through a shell."""
        model, effort = model or self.model, effort or self.effort
        base = [a.replace("{prompt}", prompt) for a in self.command]
        if model:
            base += [a.replace("{model}", model) for a in self.model_args]
        if effort:
            base += [a.replace("{effort}", effort) for a in self.effort_args]
        if not any("{prompt}" in a for a in self.command):
            base.append(prompt)
        return base


class CLIExecutor(AgentExecutor):
    def __init__(self, settings: CLISettings):
        self.s = settings
        self.running: dict[str, asyncio.Task] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue, task.id, task.context_id)
        prompt = context.get_user_input().strip()
        if not prompt:
            await up.reject(up.new_agent_message([new_text_part("Nothing to do: the message has no text.")]))
            return
        msg = context.message
        try:
            asked = overrides(MessageToDict(msg.metadata) if msg is not None else None, context.metadata)
        except BadOverride as e:
            await up.reject(up.new_agent_message([new_text_part(f"Not run: {e}.")]))
            return
        await up.start_work()
        run = asyncio.create_task(self._run(up, task.context_id, prompt, **asked))
        self.running[task.id] = run
        try:
            await run
        except asyncio.CancelledError:
            if not run.cancelled():
                raise
            await up.cancel(up.new_agent_message([new_text_part("Stopped before it finished.")]))
        except FileNotFoundError:
            await up.failed(up.new_agent_message([new_text_part(f"{self.s.command[0]!r} is not installed on the gateway's machine "
                                                                f"(not found on PATH).")]))
        except TimeoutError:
            await up.failed(up.new_agent_message([new_text_part(f"{self.s.label} did not finish within {self.s.timeout:g}s.")]))
        except Exception as e:
            log.exception("run failed")
            await up.failed(up.new_agent_message([new_text_part(f"{self.s.label} failed: {type(e).__name__}: {e}")]))
        finally:
            self.running.pop(task.id, None)

    async def _run(self, up: TaskUpdater, context_id: str, prompt: str, model: str | None = None,
                   effort: str | None = None) -> None:
        ws = workspace(self.s.workdir, context_id)
        argv = self.s.argv(prompt, model, effort)
        log.info("run in %s: %s", context_id, argv[0])
        proc = await asyncio.create_subprocess_exec(*argv, cwd=ws, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env={**os.environ, **self.s.env}, start_new_session=True)
        out: list[str] = []
        size = 0
        err = bytearray()

        async def read_stdout() -> None:
            nonlocal size
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                if size < MAX_OUTPUT:
                    out.append(line)
                    size += len(line)
                if line.strip():
                    await up.update_status(TaskState.TASK_STATE_WORKING,
                                           up.new_agent_message([new_text_part(line.strip()[:NOTE_CHARS])]))

        async def read_stderr() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                err.extend(raw)
                del err[:-STDERR_TAIL]

        try:
            async with asyncio.timeout(self.s.timeout):
                await asyncio.gather(read_stdout(), read_stderr())
                code = await proc.wait()
        except BaseException:
            await stop(proc)
            raise
        text = "".join(out).strip()
        spent = {"exit_code": code, **({"model": model or self.s.model} if (model or self.s.model) else {}),
                 **({"effort": effort or self.s.effort} if (effort or self.s.effort) else {})}
        if code != 0:
            why = err.decode(errors="replace").strip() or text[-STDERR_TAIL:] or f"exit code {code}"
            await up.failed(up.new_agent_message([new_text_part(f"{self.s.label} exited with {code}: {why}")], metadata=spent))
            return
        await up.add_artifact([new_text_part(text or "(no output)")], name="result")
        await up.complete(up.new_agent_message([new_text_part(text[-NOTE_CHARS:] or "done")], metadata=spent))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        run = self.running.get(context.task_id or "")
        if run is not None and not run.done():
            run.cancel()                          # _run stops the process; execute() reports the cancel
            return
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def stop(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the process group, then SIGKILL it if it lingers."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, 15)
    try:
        await asyncio.wait_for(proc.wait(), KILL_AFTER)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, 9)
        await proc.wait()


def build_cli(name: str, cfg: CLIConfig, *, url: str, workdir: Path, auth: bool, token: str | None, title: str,
              description: str, skill: AgentSkill) -> Mounted:
    """Mount a command line agent. The gateway starts even if the program is missing; its tasks then fail and say so."""
    settings = CLISettings(workdir=workdir, command=list(cfg.command), label=cfg.name or title, model=cfg.model,
                           model_args=list(cfg.model_args), effort=cfg.effort, effort_args=list(cfg.effort_args),
                           timeout=cfg.timeout, env=dict(cfg.env))
    if shutil.which(settings.command[0]) is None:
        log.warning("%s: %r is not on PATH; its tasks will fail until it is installed", name, settings.command[0])
    executor = CLIExecutor(settings)
    agent_card = card(url, cfg.name or title, cfg.description or description, auth=auth, skills=[skill])
    return Mounted(name=name, app=a2a_app(executor, agent_card), card=agent_card, executor=executor, token=token)
