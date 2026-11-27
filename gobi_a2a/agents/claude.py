"""Claude Code behind A2A (the Claude Agent SDK), adapted from gobi-harness/a2a/claude.

One A2A task is one Claude Code run. Tasks in the same A2A context share a workspace and a Claude session, so a
follow-up task remembers the one before it. A cancel stops the run.

What the caller sees while it runs: a status message for each thing Claude says and each tool it starts. At the end:
the result as an artifact, and the run's cost and token use as the metadata of the final status message, which stays
on the task, so the caller can count it however it reads the task. A run that ends with no result (cancelled, or the
CLI failed) has no cost to give, but its status message still says the tokens its API calls used up to there.

When Claude needs something only the user can tell it, it asks with its AskUserQuestion tool. Nobody is there to
answer, so the run ends there and the task is INPUT_REQUIRED, with the question as its status message (and the
structured questions, with the cost, in that message's metadata). The answer is a message on the same task: it resumes
the Claude session with the answer as the prompt, and the task goes on.

A caller may pick the model and the effort for one run: "model" and "effort" in the message's metadata (or the
request's; the message's wins). A follow-up or a steer in the same context resumes the same Claude session with
whatever it names; what it does not name is the server's default, not what the run before used. A value that is not a
model name, or an effort Claude Code does not know, rejects the task before anything runs.
"""
from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentSkill, TaskState
from claude_agent_sdk import (AssistantMessage, CanUseToolShadowedWarning, ClaudeAgentOptions, PermissionResultDeny,
                              ResultMessage, TextBlock, ToolPermissionContext, ToolUseBlock, query)
from claude_agent_sdk.types import CanUseTool, PermissionMode
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
ASK = "AskUserQuestion"                           # Claude Code's tool for asking the user: the task's INPUT_REQUIRED
UNATTENDED = ("You work unattended: the user handed you this task and is not watching. When you need something only "
              "they can tell you (a choice between real options, a fact you cannot find, a go-ahead), ask with the "
              f"{ASK} tool, which pauses the task until they answer. Do not ask what you can find out or decide "
              "yourself.")
ASKED = ("Your question went to the user, who is not here now. End your turn: do not guess the answer or go on "
         "without it. Their answer comes as the next message.")
COUNTED = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
LINE = 64 * 1024 * 1024    # one CLI message: a Read of a screenshot carries the image, past the SDK's 1MB default

# The allowed tools are meant to run without asking, so can_use_tool only ever sees the rest: say nothing about it.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


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


@dataclass
class Calls:
    """The usage of each API call a run made so far, from its assistant messages: the CLI sends one call's blocks as
    several messages with the same id, and the last of them counts the most. For an end with no result to read it from."""
    seen: dict[str, dict[str, Any]] = field(default_factory=dict)

    def see(self, m: AssistantMessage) -> None:
        if isinstance(m.usage, dict):
            self.seen[m.message_id or m.uuid or f"call-{len(self.seen)}"] = m.usage

    def usage(self) -> dict[str, int]:
        return {k: sum(int(u.get(k) or 0) for u in self.seen.values()) for k in COUNTED
                if any(k in u for u in self.seen.values())}


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


def permit(asked: list[dict[str, Any]]) -> CanUseTool:
    """Decides the tools outside allowed_tools: a question is kept in `asked` for the user, anything else is refused."""
    async def decide(tool: str, data: dict[str, Any], _: ToolPermissionContext) -> PermissionResultDeny:
        if tool == ASK:
            asked.extend(q for q in data.get("questions") or [] if isinstance(q, dict))
            return PermissionResultDeny(message=ASKED)
        return PermissionResultDeny(message=f"{tool} is not allowed here: nobody is there to approve it.")
    return decide


def question_text(questions: list[dict[str, Any]]) -> str:
    """The questions as the user reads them: each with its options, if it has any."""
    lines = []
    for i, q in enumerate(questions, 1):
        head = str(q.get("question") or q.get("header") or "").strip() or "(no question)"
        lines.append(f"{i}. {head}" if len(questions) > 1 else head)
        for o in q.get("options") or []:
            if isinstance(o, dict) and o.get("label"):
                lines.append(f"   - {o['label']}" + (f": {o['description']}" if o.get("description") else ""))
        if q.get("multiSelect") and q.get("options"):
            lines.append("   (any of these)")
    return "\n".join(lines)


# ---- the executor
@dataclass
class Settings:
    workdir: Path                                 # one folder per A2A context under it
    model: str = "claude-opus-5-5"
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    permission_mode: str = "default"              # a tool outside allowed_tools is refused: nobody is there to ask
    max_turns: int | None = None                  # None: no turn limit, the run goes until Claude is done
    system_prompt: str | None = None              # appended to Claude Code's own, after UNATTENDED
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

    def options(self, context_id: str, model: str | None = None, effort: str | None = None,
                asked: list[dict[str, Any]] | None = None) -> ClaudeAgentOptions:
        """The run's options: the server's settings, with the model and effort a caller asked for in their place.
        Questions Claude asks go into `asked`."""
        ws = workspace(self.s.workdir, context_id)
        append = "\n\n".join(p for p in (UNATTENDED, self.s.system_prompt) if p)
        tools = [t for t in self.s.allowed_tools if t != ASK]   # allowed, it would run with nobody to answer it
        return ClaudeAgentOptions(cwd=str(ws), model=model or self.s.model, allowed_tools=tools,
                                  permission_mode=self.s.permission_mode, max_turns=self.s.max_turns,
                                  resume=self.sessions.get(context_id), effort=effort or self.s.effort,
                                  thinking=self.s.thinking, max_buffer_size=LINE, can_use_tool=permit(asked if asked is not None else []),
                                  system_prompt={"type": "preset", "preset": "claude_code", "append": append})

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
        spent: dict[str, Any] = {}                # what the run used, as far as it got: every end says it
        run = asyncio.create_task(self._run(up, task.context_id, prompt, spent=spent, **asked))
        self.running[task.id] = run
        try:
            await run
        except asyncio.CancelledError:
            if not run.cancelled():
                raise                             # this request itself is going away
            await up.cancel(up.new_agent_message([new_text_part("Stopped before it finished.")], metadata=spent or None))
        except Exception as e:                    # the CLI could not start, the API failed: say so, as the task's end
            log.exception("run failed")
            await up.failed(up.new_agent_message([new_text_part(f"Claude Code failed: {type(e).__name__}: {e}")],
                                                 metadata=spent or None))
        finally:
            self.running.pop(task.id, None)

    async def _run(self, up: TaskUpdater, context_id: str, prompt: str, model: str | None = None,
                   effort: str | None = None, spent: dict[str, Any] | None = None) -> None:
        """One run. [spent] says what it used as it goes: the tokens of each call, then the result's cost and usage."""
        spent = {} if spent is None else spent
        asked: list[dict[str, Any]] = []
        options = self.options(context_id, model, effort, asked)
        log.info("run in %s: model=%s effort=%s", context_id, options.model, options.effort or "(Claude Code default)")
        chosen = {"model": options.model, **({"effort": options.effort} if options.effort else {})}
        calls = Calls()
        result: ResultMessage | None = None
        async for m in self.run(prompt=prompt, options=options):
            if isinstance(m, AssistantMessage):   # a subagent's calls cost too
                calls.see(m)
                if calls.seen:
                    spent.update({"usage": calls.usage(), **chosen})
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
        spent.clear()
        spent.update({"cost_usd": result.total_cost_usd, "usage": result.usage or {}, "turns": result.num_turns,
                      "duration_ms": result.duration_ms, **chosen})
        text = (result.result or "").strip()
        if result.is_error or result.subtype != "success":
            why = text or "; ".join(str(e) for e in (getattr(result, "errors", None) or [])) or result.subtype
            await up.failed(up.new_agent_message([new_text_part(why)], metadata=spent))
            return
        if asked:                                 # it waits for the user: their answer, on this task, resumes it
            await up.requires_input(up.new_agent_message([new_text_part(question_text(asked))],
                                                         metadata={**spent, "questions": asked}))
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
                                   "reports what it did. When it needs something only the user knows, it asks: the task is "
                                   "input-required, and the answer, sent on the same task, lets it go on. A follow-up "
                                   "in the same context continues the same session. A message "
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
