"""The claude agent end to end through the gateway (/claude) and the A2A client, with Claude Code replaced by a
scripted run. Ported from gobi-harness/a2a/claude/tests."""
from __future__ import annotations

import asyncio

import pytest
from a2a.helpers import get_artifact_text
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from google.protobuf.json_format import MessageToDict

from conftest import Script, ask, client_for, config, final, http_for
from gobi_a2a.gateway import create_app


async def stack_for(tmp_path, **claude):
    script = Script()
    app = create_app(config(tmp_path, {"claude": {"enabled": True, **claude}}), agent_kwargs={"claude": {"run": script}})
    http = http_for(app)
    return await client_for(http, "claude"), http, script, app


@pytest.fixture
async def stack(tmp_path):
    client, http, script, app = await stack_for(tmp_path)
    yield client, script, app
    await http.aclose()


@pytest.fixture
async def defaults(tmp_path):
    """Defaults set in a2a.yaml (sonnet, high), so a fallback to them can be told from Claude Code's own."""
    client, http, script, app = await stack_for(tmp_path, model="claude-sonnet-5", effort="high")
    yield client, script, app
    await http.aclose()


def executor(app):
    return app.state.agents["claude"].executor


async def test_a_task_is_one_claude_run_and_ends_with_its_result_and_cost(stack, tmp_path):
    client, script, _ = stack
    task = await final(client, "write a.txt")
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert get_artifact_text(task.artifacts[0]) == "Did: write a.txt"
    spent = MessageToDict(task.status.message.metadata)
    assert spent["cost_usd"] == 0.042 and spent["usage"]["input_tokens"] == 1500 and spent["turns"] == 3
    got = await client.get_task(GetTaskRequest(id=task.id))
    assert MessageToDict(got.status.message.metadata)["cost_usd"] == 0.042, "a caller reading the task later sees it too"
    notes = " ".join(str(MessageToDict(m)) for m in got.history)
    assert "Looking at the files." in notes and "[Read]" in notes
    o = script.options[0]
    assert o.model == "claude-opus-5-5" and "Bash" not in o.allowed_tools and o.resume is None
    assert o.cwd.startswith(str((tmp_path / "work" / "claude").resolve())) and o.permission_mode == "default"
    assert o.max_turns is None, "no turn limit unless a2a.yaml sets one"


async def test_a_follow_up_in_the_same_context_continues_the_session(stack):
    client, script, _ = stack
    first = await final(client, "start")
    await final(client, "go on", context_id=first.context_id)
    await final(client, "something else")
    assert script.options[1].resume == "sess-1" and script.options[1].cwd == script.options[0].cwd
    assert script.options[2].resume is None and script.options[2].cwd != script.options[0].cwd


async def test_a_failed_run_is_a_failed_task(stack):
    client, script, _ = stack
    script.error = True
    assert (await final(client, "too long")).status.state == TaskState.TASK_STATE_FAILED
    script.error, script.raise_ = False, RuntimeError("CLI not found")
    task = await final(client, "again")
    assert task.status.state == TaskState.TASK_STATE_FAILED and "CLI not found" in str(MessageToDict(task.status))
    assert MessageToDict(task.status.message.metadata) == {}, "no result, so no cost to report"


async def test_a_message_without_text_is_rejected(stack):
    client, script, _ = stack
    assert (await final(client, "   ")).status.state == TaskState.TASK_STATE_REJECTED and not script.options


async def test_a_cancel_stops_the_run(stack):
    client, script, app = stack
    script.hold = asyncio.Event()
    sending = asyncio.create_task(final(client, "long job"))
    ex = executor(app)
    for _ in range(200):
        if ex.running:
            break
        await asyncio.sleep(0.01)
    (task_id,) = ex.running
    await client.cancel_task(CancelTaskRequest(id=task_id))
    task = await asyncio.wait_for(sending, 5)
    assert task.status.state == TaskState.TASK_STATE_CANCELED and not ex.running


# ---- a2a.yaml reaches Claude Code
async def test_the_config_reaches_claude(tmp_path):
    client, http, script, app = await stack_for(tmp_path, permission_mode="bypassPermissions", max_turns=7, effort="XHigh",
                                                thinking="16000", allowed_tools=["Read", "Bash"], system_prompt="Be brief.",
                                                name="My Claude", description="Mine.")
    await final(client, "go")
    o = script.options[0]
    assert (o.permission_mode, o.max_turns, o.effort, o.thinking, o.allowed_tools) == \
        ("bypassPermissions", 7, "xhigh", {"type": "enabled", "budget_tokens": 16000}, ["Read", "Bash"])
    assert o.system_prompt == {"type": "preset", "preset": "claude_code", "append": "Be brief."}
    card = (await http.get("/claude/.well-known/agent-card.json")).json()
    assert (card["name"], card["description"]) == ("My Claude", "Mine.")
    await http.aclose()


@pytest.mark.parametrize("value, thinking", [("adaptive", {"type": "adaptive"}), ("Disabled", {"type": "disabled"}), (None, None)])
async def test_thinking_from_the_config(tmp_path, value, thinking):
    _, http, _, app = await stack_for(tmp_path, thinking=value)
    o = executor(app).options("ctx")
    assert o.thinking == thinking and o.effort is None and o.model == "claude-opus-5-5"
    await http.aclose()


# ---- the model and effort a caller picks for one run
async def test_a_task_runs_on_the_model_and_effort_its_message_names(defaults):
    client, script, _ = defaults
    task = await final(client, "think hard", metadata={"model": " claude-opus-5-5 ", "effort": "XHigh"})
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert (script.options[0].model, script.options[0].effort) == ("claude-opus-5-5", "xhigh")
    spent = MessageToDict(task.status.message.metadata)
    assert spent["model"] == "claude-opus-5-5" and spent["effort"] == "xhigh", "the cost is told against what ran"


async def test_without_a_choice_the_servers_defaults_run(defaults):
    client, script, _ = defaults
    task = await final(client, "plain")
    assert (script.options[0].model, script.options[0].effort) == ("claude-sonnet-5", "high")
    assert MessageToDict(task.status.message.metadata)["model"] == "claude-sonnet-5"
    await final(client, "half", metadata={"effort": "low"})
    assert (script.options[1].model, script.options[1].effort) == ("claude-sonnet-5", "low"), "what it does not name is the default"


async def test_the_request_metadata_serves_when_the_message_names_nothing_and_the_message_wins(defaults):
    client, script, _ = defaults
    await final(client, "a", request_metadata={"model": "claude-haiku-5", "effort": "medium"})
    await final(client, "b", metadata={"model": "claude-opus-5-5"}, request_metadata={"model": "claude-haiku-5", "effort": "max"})
    assert (script.options[0].model, script.options[0].effort) == ("claude-haiku-5", "medium")
    assert (script.options[1].model, script.options[1].effort) == ("claude-opus-5-5", "max")


async def test_a_follow_up_resumes_the_session_on_the_model_it_names(defaults):
    client, script, _ = defaults
    first = await final(client, "start", metadata={"model": "claude-haiku-5", "effort": "low"})
    await final(client, "now properly", first.context_id, metadata={"model": "claude-opus-5-5", "effort": "max"})
    await final(client, "and again", first.context_id)
    a, b, c = script.options
    assert (b.resume, b.cwd, b.model, b.effort) == ("sess-1", a.cwd, "claude-opus-5-5", "max")
    assert (c.resume, c.model, c.effort) == ("sess-2", "claude-sonnet-5", "high"), "not sticky: the server's defaults"


async def test_a_steer_on_a_running_task_resumes_it_with_its_own_model_and_effort(defaults):
    client, script, app = defaults
    script.hold = asyncio.Event()
    first = None
    async for r in client.send_message(ask("long job", metadata={"model": "claude-haiku-5", "effort": "low"}, now=True)):
        first = r.task
    async for _ in client.send_message(ask("go deeper", first.context_id, task_id=first.id, metadata={"effort": "max"},
                                           now=True)):
        pass
    script.hold.set()
    for _ in range(300):
        if len(script.options) == 2 and not executor(app).running:
            break
        await asyncio.sleep(0.01)
    a, b = script.options
    assert (a.model, a.effort, a.resume) == ("claude-haiku-5", "low", None)
    assert (b.model, b.effort, b.resume) == ("claude-sonnet-5", "max", "sess-1")
    got = await client.get_task(GetTaskRequest(id=first.id))
    assert MessageToDict(got.status.message.metadata)["effort"] == "max"


@pytest.mark.parametrize("metadata, why", [({"effort": "extreme"}, "effort"), ({"effort": 3}, "effort"),
                                           ({"model": "opus; rm -rf /"}, "model"), ({"model": 5}, "model"),
                                           ({"model": "x" * 200}, "model")])
async def test_a_model_or_effort_that_cannot_be_used_rejects_the_task_before_it_runs(defaults, metadata, why):
    client, script, _ = defaults
    task = await final(client, "go", metadata=metadata)
    assert task.status.state == TaskState.TASK_STATE_REJECTED and not script.options
    assert why in str(MessageToDict(task.status))


@pytest.mark.parametrize("metadata", [{"model": ""}, {"effort": None}, {"other": "x"}])
async def test_an_empty_choice_is_no_choice(defaults, metadata):
    client, script, _ = defaults
    assert (await final(client, "go", metadata=metadata)).status.state == TaskState.TASK_STATE_COMPLETED
    assert (script.options[0].model, script.options[0].effort) == ("claude-sonnet-5", "high")
