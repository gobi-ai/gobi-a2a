"""Claude Code behind A2A (the Claude Agent SDK), adapted from gobi-harness/a2a/claude.

One A2A task is one Claude Code run. Tasks in the same A2A context share a workspace and a Claude session, so a
follow-up task remembers the one before it. A cancel stops the run.

What the caller sees while it runs: a status message for each thing Claude says and each tool it starts. At the end:
the result as an artifact, and the run's cost and token use as the metadata of the final status message, which stays
on the task, so the caller can count it however it reads the task.

A caller may pick the model and the effort for one run: "model" and "effort" in the message's metadata (or the
request's; the message's wins). A follow-up or a steer in the same context resumes the same Claude session with
whatever it names; what it does not name is the server's default, not what the run before used. A value that is not a
model name, or an effort Claude Code does not know, rejects the task before anything runs.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentSkill, TaskState
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query
from claude_agent_sdk.types import PermissionMode
from google.protobuf.json_format import MessageToDict
from pydantic import Field, field_validator

from gobi_a2a.agents.common import (NOTE_CHARS, AgentConfig, BadOverride, Mounted, a2a_app, card, check_effort, check_model,
                                    overrides, workspace)

log = logging.getLogger("gobi_a2a.claude")

Run = Callable[..., AsyncIterator[Any]]           # claude_agent_sdk.query(prompt=, options=), or a test's stand-in
PERMISSION_MODES: tuple[str, ...] = get_args(PermissionMode)   # default, acceptEdits, plan, bypassPermissions, dontAsk, auto
DEFAULT_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "WebSearch", "WebFetch"]   # files and the web: no Bash
NAME = "Claude Code"
DESCRIPTION = "Claude Code working in its own folder: code, files and web research."


# ---- config: the `agents.claude` block of a2a.yaml
class Config(AgentConfig):
    enabled: bool = True                          # the one agent on by default
    model: str = "claude-opus-5-5"                # a message may pick another
    effort: str | None = None                     # low|medium|high|xhigh|max; None: Claude Code's default
    thinking: str | int | None = None             # adaptive, disabled, or a token budget; None: Claude Code's default
    permission_mode: str = "default"              # bypassPermissions: every tool runs, Bash included
    allowed_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLS))
    max_turns: int | None = None                  # None: no limit, the run goes until Claude is done
    system_prompt: str | None = None              # appended to Claude Code's own

    @field_validator("model", mode="before")
    @classmethod
    def _model(cls, v: Any) -> str:
        return check_model(v) or "claude-opus-5-5"

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, v: Any) -> str | None:
        return check_effort(v)

    @field_validator("thinking", mode="before")
    @classmethod
    def _thinking(cls, v: Any) -> str | int | None:
        thinking_from(v)                          # raises on a bad value
        return v

    @field_validator("permission_mode", mode="before")
    @classmethod
    def _mode(cls, v: Any) -> str:
        m = (v or "").strip() if isinstance(v, str) else v
        if not m:
            return "default"
        if m not in PERMISSION_MODES:
            raise ValueError(f"{v!r} is not one of {', '.join(PERMISSION_MODES)}")
        return m

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _tools(cls, v: Any) -> Any:
        if v is None:
            return list(DEFAULT_TOOLS)
        if isinstance(v, str):                    # "Read,Write,Bash" is fine too
            v = v.split(",")
        return [t.strip() for t in v if isinstance(t, str) and t.strip()] if isinstance(v, list) else v

    @field_validator("max_turns", mode="before")
    @classmethod
    def _turns(cls, v: Any) -> Any:
        if v in (None, "", 0):
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"{v!r} is not a positive number of turns (leave it out for no limit)")
        return v


def thinking_from(value: Any) -> dict | None:
    """"adaptive", "disabled", or a fixed token budget. Empty: Claude Code decides."""
    if value is None:
        return None
    v = str(value).strip().lower() if isinstance(value, (str, int)) and not isinstance(value, bool) else "?"
    if not v:
        return None
    if v in ("adaptive", "disabled"):
        return {"type": v}
    if v.isdigit() and int(v) > 0:
        return {"type": "enabled", "budget_tokens": int(v)}
    raise ValueError(f"thinking {value!r} is not adaptive, disabled or a positive token budget")


# ---- the executor
@dataclass
class Settings:
    workdir: Path                                 # one folder per A2A context under it
    model: str = "claude-opus-5-5"
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    permission_mode: str = "default"              # a tool outside allowed_tools is refused: nobody is there to ask
    max_turns: int | None = None                  # None: no turn limit, the run goes until Claude is done
    system_prompt: str | None = None              # appended to Claude Code's own
    effort: str | None = None                     # low|medium|high|xhigh|max; None: Claude Code's default
    thinking: dict[str, Any] | None = None        # {"type": "adaptive"|"disabled"} or {"type": "enabled", "budget_tokens": N}

    @staticmethod
    def from_config(cfg: Config, workdir: Path) -> "Settings":
        return Settings(workdir=workdir, model=cfg.model, allowed_tools=list(cfg.allowed_tools),
                        permission_mode=cfg.permission_mode, max_turns=cfg.max_turns, system_prompt=cfg.system_prompt,
                        effort=cfg.effort, thinking=thinking_from(cfg.thinking))


class ClaudeExecutor(AgentExecutor):
    def __init__(self, settings: Settings, run: Run = query):
        self.s, self.run = settings, run
        self.sessions: dict[str, str] = {}        # A2A context -> Claude session, for the next task in that context
        self.running: dict[str, asyncio.Task] = {}

    def options(self, context_id: str, model: str | None = None, effort: str | None = None) -> ClaudeAgentOptions:
        """The run's options: the server's settings, with the model and effort a caller asked for in their place."""
        ws = workspace(self.s.workdir, context_id)
        extra = {"system_prompt": {"type": "preset", "preset": "claude_code", "append": self.s.system_prompt}} if self.s.system_prompt else {}
        return ClaudeAgentOptions(cwd=str(ws), model=model or self.s.model, allowed_tools=list(self.s.allowed_tools),
                                  permission_mode=self.s.permission_mode, max_turns=self.s.max_turns,
                                  resume=self.sessions.get(context_id), effort=effort or self.s.effort,
                                  thinking=self.s.thinking, **extra)

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
                raise                             # this request itself is going away
            await up.cancel(up.new_agent_message([new_text_part("Stopped before it finished.")]))
        except Exception as e:                    # the CLI could not start, the API failed: say so, as the task's end
            log.exception("run failed")
            await up.failed(up.new_agent_message([new_text_part(f"Claude Code failed: {type(e).__name__}: {e}")]))
        finally:
            self.running.pop(task.id, None)

    async def _run(self, up: TaskUpdater, context_id: str, prompt: str, model: str | None = None,
                   effort: str | None = None) -> None:
        options = self.options(context_id, model, effort)
        log.info("run in %s: model=%s effort=%s", context_id, options.model, options.effort or "(Claude Code default)")
        result: ResultMessage | None = None
        async for m in self.run(prompt=prompt, options=options):
            if isinstance(m, AssistantMessage) and not getattr(m, "parent_tool_use_id", None):
                for b in m.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        note = b.text.strip()[:NOTE_CHARS]
                    elif isinstance(b, ToolUseBlock):
                        note = f"[{b.name}]"
                    else:
                        continue
                    await up.update_status(TaskState.TASK_STATE_WORKING, up.new_agent_message([new_text_part(note)]))
            elif isinstance(m, ResultMessage):
                result = m
        if result is None:
            raise RuntimeError("Claude Code ended without a result")
        if result.session_id:
            self.sessions[context_id] = result.session_id
        spent = {"cost_usd": result.total_cost_usd, "usage": result.usage or {}, "turns": result.num_turns,
                 "duration_ms": result.duration_ms, "model": options.model,
                 **({"effort": options.effort} if options.effort else {})}
        text = (result.result or "").strip()
        if result.is_error or result.subtype != "success":
            why = text or "; ".join(str(e) for e in (getattr(result, "errors", None) or [])) or result.subtype
            await up.failed(up.new_agent_message([new_text_part(why)], metadata=spent))
            return
        await up.add_artifact([new_text_part(text or "(no text)")], name="result")
        await up.complete(up.new_agent_message([new_text_part(text[:NOTE_CHARS] or "done")], metadata=spent))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        run = self.running.get(context.task_id or "")
        if run is not None and not run.done():
            run.cancel()                          # execute() reports the cancel when the run has stopped
            return
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


# ---- mounting
def build(name: str, cfg: Config, *, url: str, workdir: Path, auth: bool, token: str | None = None,
          run: Run | None = None) -> Mounted:
    settings = Settings.from_config(cfg, workdir)
    executor = ClaudeExecutor(settings, run) if run else ClaudeExecutor(settings)
    skill = AgentSkill(id="work", name="Work on files and the web",
                       description="Reads, writes and edits files in its own workspace, searches and reads the web, and "
                                   "reports what it did. A follow-up in the same context continues the same session. A message "
                                   "may pick the run's model and effort: {\"model\": ..., \"effort\": \"low\"|...|\"max\"} "
                                   "in its metadata; otherwise the server's defaults.",
                       tags=["coding", "research", "files"], examples=["Write a script that ...", "Find out ... and summarize it"])
    agent_card = card(url, cfg.name or NAME, cfg.description or DESCRIPTION, auth=auth, skills=[skill])
    log.info("claude: model=%s effort=%s thinking=%s permission_mode=%s max_turns=%s", settings.model,
             settings.effort or "(Claude Code default)", settings.thinking or "(Claude Code default)", settings.permission_mode,
             settings.max_turns or "(no limit)")
    if settings.permission_mode == "bypassPermissions":
        log.warning("claude: permission_mode=bypassPermissions: every tool runs, Bash included, without asking")
    return Mounted(name=name, app=a2a_app(executor, agent_card), card=agent_card, executor=executor, token=token)
