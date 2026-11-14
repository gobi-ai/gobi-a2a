"""The cursor, codex and omp scaffolds: a command line program per task, run on tests/fake_agent.py."""
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
from gobi_a2a.agents import codex, cursor, omp
from gobi_a2a.agents.cli import CLISettings
from gobi_a2a.gateway import create_app


async def agent(tmp_path, name="cursor", **extra):
    app = create_app(config(tmp_path, {name: fake_cli(**extra)}))
    http = http_for(app)
    return await client_for(http, name), http, app


def lines(text: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


@pytest.mark.parametrize("name", ["cursor", "codex", "omp"])
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
    client, http, _ = await agent(tmp_path, "codex")
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


def test_model_and_effort_go_before_a_double_dash_so_the_prompt_stays_an_argument():
    s = CLISettings(workdir=Path("."), command=["tool", "exec", "--", "{prompt}"], model_args=["--model", "{model}"],
                    effort_args=["-c", "effort={effort}"])
    assert s.argv("--yolo", model="m", effort="high") == ["tool", "exec", "--model", "m", "-c", "effort=high", "--", "--yolo"]
    assert CLISettings(workdir=Path("."), command=["tool", "--"], model_args=["-m", "{model}"]).argv("-x", model="m") == \
        ["tool", "-m", "m", "--", "-x"]


async def test_json_events_are_notes_and_the_answer_and_a_follow_up_resumes_the_session(tmp_path):
    client, http, _ = await agent(tmp_path, "codex", resume_args=["resume", "{session}"])
    first = await final(client, "json")
    assert first.status.state == TaskState.TASK_STATE_COMPLETED
    assert get_artifact_text(first.artifacts[0]) == "args: []", "the last message is the result, not the event lines"
    assert MessageToDict(first.status.message.metadata)["usage"] == {"input_tokens": 10, "output_tokens": 2}
    notes = " ".join(str(MessageToDict(m)) for m in (await client.get_task(GetTaskRequest(id=first.id))).history)
    assert "$ ls" in notes and "not json, a plain line" in notes
    again = await final(client, "json", context_id=first.context_id)
    assert get_artifact_text(again.artifacts[0]) == 'args: ["resume", "session-1"]'
    other = await final(client, "json")
    assert get_artifact_text(other.artifacts[0]) == "args: []", "another context starts its own session"
    await http.aclose()


async def test_a_session_that_will_not_resume_is_dropped_so_the_next_message_starts_afresh(tmp_path):
    client, http, app = await agent(tmp_path, "codex", resume_args=["gone", "{session}"])
    first = await final(client, "json")
    failed = await final(client, "json", context_id=first.context_id)
    assert failed.status.state == TaskState.TASK_STATE_FAILED
    assert "no rollout found" in str(MessageToDict(failed.status.message)) and "new session" in str(MessageToDict(failed.status.message))
    fresh = await final(client, "json", context_id=first.context_id)
    assert get_artifact_text(fresh.artifacts[0]) == "args: []"
    await http.aclose()


def test_codex_events():
    assert codex.events({"type": "thread.started", "thread_id": "t"}).session == "t"
    assert codex.events({"type": "turn.failed", "error": {"message": "401"}}).error == "401"
    ev = codex.events({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
    assert (ev.text, ev.note) == ("done", "done")
    assert codex.events({"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py"}]}}).note == "Changed a.py"
    assert codex.events({"type": "item.started", "item": {"type": "reasoning"}}) == codex.Event()


def test_cursor_events():
    assert cursor.events({"type": "system", "subtype": "init", "session_id": "c"}).session == "c"
    ev = cursor.events({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}, "session_id": "c"})
    assert (ev.note, ev.session) == ("hi", "c")
    started = {"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "npm test"}}}}
    assert cursor.events(started).note == "shell: npm test"
    assert cursor.tool_note({"tool": {"case": "readToolCall", "value": {"args": {"path": "a.py"}}}}) == "read: a.py"
    done = cursor.events({"type": "result", "subtype": "success", "is_error": False, "result": "all done", "usage": {"x": 1}})
    assert (done.text, done.error, done.usage) == ("all done", None, {"x": 1})


def test_omp_events():
    assert omp.events({"type": "session", "id": "s1", "cwd": "/w"}).session == "s1"
    assert omp.events({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}}).note == "bash: ls"
    assert omp.events({"type": "tool_execution_start", "toolName": "read", "args": {}, "intent": "Reading a.py"}).note == \
        "read: Reading a.py"
    done = omp.events({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "usage": {"input": 5},
                                                          "content": [{"type": "thinking", "thinking": "hm"},
                                                                      {"type": "text", "text": "All done."}]}})
    assert (done.text, done.note, done.usage, done.error) == ("All done.", "All done.", {"input": 5}, None)
    tools_only = omp.events({"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse",
                                                                "content": [{"type": "toolCall", "name": "bash"}]}})
    assert tools_only.text is None, "a message that only calls tools is not the answer"
    failed = omp.events({"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                                                            "errorMessage": "401", "content": []}})
    assert failed.error == "401"
    assert omp.events({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "q"}]}}) == \
        omp.Event()


def test_the_scaffolds_default_commands():
    assert cursor.Config(enabled=True).command[:2] == ["cursor-agent", "--print"]
    assert omp.Config().command == ["omp", "--mode", "json", "--", "{prompt}"]
    assert omp.Config().resume_args == ["--resume", "{session}"]
    assert codex.Config().command[:3] == ["codex", "exec", "--json"] and codex.Config().command[-2:] == ["--", "{prompt}"]
    assert codex.Config().resume_args == ["resume", "{session}"] and cursor.Config().resume_args == ["--resume", "{session}"]
    assert "stream-json" in cursor.Config().command and "--trust" in cursor.Config().command
    s = CLISettings(workdir=Path("."), command=codex.Config().command, model_args=["--model", "{model}"],
                    resume_args=codex.Config().resume_args)
    assert s.argv("- go", model="m", session="t")[-6:] == ["--model", "m", "resume", "t", "--", "- go"]
    assert FAKE_AGENT.exists()
