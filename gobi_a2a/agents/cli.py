"""A coding agent that is a command line program, behind A2A: the base of the cursor, codex, aider and omp scaffolds.

One A2A task is one run of the command, in the A2A context's own folder (a follow-up in the same context works in the
same folder, so whatever the tool keeps there, its chat history or a git repo, carries over). While it runs, each line
it prints is a status message; when it exits 0, what it printed is the result artifact; any other exit fails the task
with the end of its stderr. A cancel stops the process (SIGTERM, then SIGKILL).

The command is an argv list, never a shell line, so the prompt cannot become shell syntax. "{prompt}" in it is replaced
by the task's text; without one, the text is the last argument. "{model}" and "{effort}" are replaced in `model_args`
and `effort_args`, which are added only when a model or effort is set (in a2a.yaml, or by the message's metadata, as for
claude). They go just before a "--" in the command, if it has one, so that a prompt after it ("- fix the test") is never
read as an option.

An agent whose output is JSON events (codex, cursor) gives build_cli an `events` function that reads one event into
an Event: a progress note, the answer, an error, usage, or the agent's session id. With a session id and `resume_args`
("{session}" replaced by it, placed as model_args are), a follow-up in the same A2A context resumes that session, as
claude does. A line that is not a JSON object is read as plain text.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections.abc import Callable
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
    resume_args: list[str] = Field(default_factory=list)  # e.g. ["--resume", "{session}"], added for a follow-up
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
class Event:
    """What one line of an agent's JSON output says. Every field is optional."""
    note: str | None = None                       # a progress message
    text: str | None = None                       # the answer; the last one is the result
    error: str | None = None                      # why the run failed
    session: str | None = None                    # the agent's session, for resume_args
    usage: dict[str, Any] | None = None           # tokens, as the agent counts them


Events = Callable[[dict[str, Any]], Event]


@dataclass
class CLISettings:
    workdir: Path
    command: list[str]
    label: str = "the agent"                      # what failure messages call it
    model: str | None = None
    model_args: list[str] = field(default_factory=list)
    effort: str | None = None
    effort_args: list[str] = field(default_factory=list)
    resume_args: list[str] = field(default_factory=list)
    timeout: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    events: Events | None = None                  # reads the output as JSON events; None: plain text

    def argv(self, prompt: str, model: str | None = None, effort: str | None = None,
             session: str | None = None) -> list[str]:
        """The command for one run. Placeholders are whole-argument or in-argument; nothing goes through a shell."""
        model, effort = model or self.model, effort or self.effort
        base = [a.replace("{prompt}", prompt) for a in self.command]
        extra = [a.replace("{model}", model) for a in self.model_args] if model else []
        if effort:
            extra += [a.replace("{effort}", effort) for a in self.effort_args]
        if session:
            extra += [a.replace("{session}", session) for a in self.resume_args]
        at = self.command.index("--") if "--" in self.command else len(base)
        base[at:at] = extra
        if not any("{prompt}" in a for a in self.command):
            base.append(prompt)
        return base


class CLIExecutor(AgentExecutor):
    def __init__(self, settings: CLISettings):
        self.s = settings
        self.running: dict[str, asyncio.Task] = {}
        self.sessions: dict[str, str] = {}        # A2A context -> the agent's session, for the next task in that context

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
        resumed = self.sessions.get(context_id) if self.s.resume_args else None
        argv = self.s.argv(prompt, model, effort, resumed)
        log.info("run in %s: %s%s", context_id, argv[0], f" (resuming {resumed})" if resumed else "")
        proc = await asyncio.create_subprocess_exec(*argv, cwd=ws, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env={**os.environ, **self.s.env}, start_new_session=True)
        out: list[str] = []
        size = 0
        err = bytearray()
        said = Event()                            # what the JSON events said, the last of each

        async def note(text: str) -> None:
            await up.update_status(TaskState.TASK_STATE_WORKING, up.new_agent_message([new_text_part(text[:NOTE_CHARS])]))

        async def read_stdout() -> None:
            nonlocal size
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                if (ev := self._event(line)) is not None:
                    if ev.session:
                        said.session = self.sessions[context_id] = ev.session
                    said.text = ev.text if ev.text is not None else said.text
                    said.error, said.usage = ev.error or said.error, ev.usage or said.usage
                    if ev.note and ev.note.strip():
                        await note(ev.note.strip())
                    continue
                if size < MAX_OUTPUT:
                    out.append(line)
                    size += len(line)
                if line.strip():
                    await note(line.strip())

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
        text = (said.text if said.text is not None else "".join(out)).strip()
        spent = {"exit_code": code, **({"model": model or self.s.model} if (model or self.s.model) else {}),
                 **({"effort": effort or self.s.effort} if (effort or self.s.effort) else {}),
                 **({"usage": said.usage} if said.usage else {})}
        if code != 0:
            why = said.error or err.decode(errors="replace").strip() or text[-STDERR_TAIL:] or f"exit code {code}"
            if resumed and said.session is None:  # the session would not open (gone, or never kept): start afresh
                self.sessions.pop(context_id, None)
                why += " (the next message starts a new session)"
            await up.failed(up.new_agent_message([new_text_part(f"{self.s.label} exited with {code}: {why}")], metadata=spent))
            return
        await up.add_artifact([new_text_part(text or "(no output)")], name="result")
        await up.complete(up.new_agent_message([new_text_part(text[-NOTE_CHARS:] or "done")], metadata=spent))

    def _event(self, line: str) -> Event | None:
        """The line as an event, or None to read it as plain text."""
        if self.s.events is None or not line.lstrip().startswith("{"):
            return None
        try:
            obj = json.loads(line)
            return self.s.events(obj) if isinstance(obj, dict) else None
        except (ValueError, TypeError, AttributeError, KeyError):
            log.debug("not an event: %.200s", line)
            return None

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
              description: str, skill: AgentSkill, events: Events | None = None) -> Mounted:
    """Mount a command line agent. The gateway starts even if the program is missing; its tasks then fail and say so."""
    settings = CLISettings(workdir=workdir, command=list(cfg.command), label=cfg.name or title, model=cfg.model,
                           model_args=list(cfg.model_args), effort=cfg.effort, effort_args=list(cfg.effort_args),
                           resume_args=list(cfg.resume_args), timeout=cfg.timeout, env=dict(cfg.env), events=events)
    if shutil.which(settings.command[0]) is None:
        log.warning("%s: %r is not on PATH; its tasks will fail until it is installed", name, settings.command[0])
    executor = CLIExecutor(settings)
    agent_card = card(url, cfg.name or title, cfg.description or description, auth=auth, skills=[skill])
    return Mounted(name=name, app=a2a_app(executor, agent_card), card=agent_card, executor=executor, token=token)
