"""The cursor, aider and omp scaffolds: a command line program per task, run on tests/fake_agent.py."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from a2a.helpers import get_artifact_text
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from google.protobuf.json_format import MessageToDict

from conftest import FAKE_AGENT, client_for, config, fake_cli, final, http_for
from gobi_a2a.agents import aider, cursor, omp
from gobi_a2a.agents.cli import CLISettings
from gobi_a2a.gateway import create_app


async def agent(tmp_path, name="cursor", **extra):
    app = create_app(config(tmp_path, {name: fake_cli(**extra)}))
    http = http_for(app)
    return await client_for(http, name), http, app


def lines(text: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


@pytest.mark.parametrize("name", ["cursor", "aider", "omp"])
async def test_a_task_is_one_run_and_its_output_is_the_result(tmp_path, name):
    client, http, _ = await agent(tmp_path, name)
    task = await final(client, "fix the bug")
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    out = lines(get_artifact_text(task.artifacts[0]))
    assert out["answer"] == "fix the bug" and json.loads(out["args"]) == []
    assert Path(out["cwd"]).resolve().parent == (tmp_path / "work" / name).resolve()
    got = await client.get_task(GetTaskRequest(id=task.id))
    notes = " ".join(str(MessageToDict(m)) for m in got.history)
    assert "step fix" in notes and "step bug" in notes, "each printed line is a progress note"
    assert MessageToDict(task.status.message.metadata) == {"exit_code": 0}
    await http.aclose()


async def test_a_follow_up_in_the_same_context_works_in_the_same_folder(tmp_path):
    client, http, _ = await agent(tmp_path)
    first = await final(client, "touch")
    again = await final(client, "touch", context_id=first.context_id)
    other = await final(client, "touch")
    assert "note-0.txt" in get_artifact_text(again.artifacts[0]) and "note-1.txt" in get_artifact_text(again.artifacts[0])
    assert "note-1.txt" not in get_artifact_text(other.artifacts[0])
    await http.aclose()


async def test_the_model_and_effort_come_from_the_config_or_the_message(tmp_path):
    client, http, _ = await agent(tmp_path, "omp", model="sonnet", model_args=["--model", "{model}"],
                                  effort_args=["--thinking={effort}"])
    task = await final(client, "a")
    assert json.loads(lines(get_artifact_text(task.artifacts[0]))["args"]) == ["--model", "sonnet"]
    task = await final(client, "b", metadata={"model": "gpt-5", "effort": "High"})
    assert json.loads(lines(get_artifact_text(task.artifacts[0]))["args"]) == ["--model", "gpt-5", "--thinking=high"]
    assert MessageToDict(task.status.message.metadata) == {"exit_code": 0, "model": "gpt-5", "effort": "high"}
    rejected = await final(client, "c", metadata={"model": "x; rm -rf /"})
    assert rejected.status.state == TaskState.TASK_STATE_REJECTED
    await http.aclose()


async def test_the_prompt_is_one_argument_never_shell(tmp_path):
    client, http, _ = await agent(tmp_path)
    prompt = "say $(whoami) `id` ; echo pwned && 'quoted'"
    task = await final(client, prompt)
    assert lines(get_artifact_text(task.artifacts[0]))["answer"] == prompt
    await http.aclose()


async def test_a_nonzero_exit_is_a_failed_task_with_its_stderr(tmp_path):
    client, http, _ = await agent(tmp_path, "aider")
    task = await final(client, "fail")
    assert task.status.state == TaskState.TASK_STATE_FAILED
    status = str(MessageToDict(task.status))
    assert "exited with 3" in status and "it broke" in status
    assert MessageToDict(task.status.message.metadata)["exit_code"] == 3
    await http.aclose()


async def test_a_missing_program_fails_the_task_and_says_so(tmp_path):
    app = create_app(config(tmp_path, {"cursor": {"enabled": True, "command": ["no-such-agent-cli-xyz"]}}))
    async with http_for(app) as http:
        task = await final(await client_for(http, "cursor"), "go")
    assert task.status.state == TaskState.TASK_STATE_FAILED and "not installed" in str(MessageToDict(task.status))


async def test_a_run_past_its_timeout_is_stopped(tmp_path):
    client, http, _ = await agent(tmp_path, timeout=0.5)
    task = await asyncio.wait_for(final(client, "hang"), 10)
    assert task.status.state == TaskState.TASK_STATE_FAILED and "within 0.5s" in str(MessageToDict(task.status))
    await http.aclose()


async def test_a_cancel_kills_the_process(tmp_path):
    client, http, app = await agent(tmp_path)
    ex = app.state.agents["cursor"].executor
    sending = asyncio.create_task(final(client, "hang"))
    started = None
    for _ in range(500):
        started = next((tmp_path / "work" / "cursor").glob("*/started"), None) if (tmp_path / "work" / "cursor").exists() else None
        if started and ex.running:
            break
        await asyncio.sleep(0.01)
    pid = int(started.read_text())
    (task_id,) = ex.running
    await client.cancel_task(CancelTaskRequest(id=task_id))
    task = await asyncio.wait_for(sending, 10)
    assert task.status.state == TaskState.TASK_STATE_CANCELED and not ex.running
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the agent's process is still running")
    await http.aclose()


def test_argv_placeholders():
    s = CLISettings(workdir=Path("."), command=["tool", "--message", "{prompt}"], model_args=["--model", "{model}"],
                    effort_args=["--effort", "{effort}"])
    assert s.argv("do it") == ["tool", "--message", "do it"]
    assert s.argv("do it", model="m", effort="low") == ["tool", "--message", "do it", "--model", "m", "--effort", "low"]
    assert CLISettings(workdir=Path("."), command=["tool", "-p"]).argv("{model} x") == ["tool", "-p", "{model} x"]


def test_the_scaffolds_default_commands():
    assert cursor.Config(enabled=True).command[:2] == ["cursor-agent", "--print"]
    assert aider.Config().command[0] == "aider" and "{prompt}" in aider.Config().command
    assert omp.Config().command == ["omp", "-p", "{prompt}"]
    assert FAKE_AGENT.exists()
