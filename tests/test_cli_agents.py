"""The cursor, codex, omp and copilot scaffolds: a command line program per task, run on tests/fake_agent.py."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest
from a2a.helpers import get_artifact_text
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from google.protobuf.json_format import MessageToDict

from conftest import FAKE_AGENT, client_for, config, fake_cli, final, http_for
from gobi_a2a.agents import codex, copilot, cursor, omp
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


def test_copilots_default_command_runs_one_prompt_unattended_and_names_its_session():
    c = copilot.Config()
    assert not c.enabled and c.model is None and c.effort is None
    s = CLISettings(workdir=Path("."), command=c.command, model_args=c.model_args, effort_args=c.effort_args,
                    resume_args=c.resume_args)
    unattended = ["--silent", "--no-color", "--allow-all-tools", "--allow-all-urls", "--no-ask-user", "--autopilot"]
    assert s.argv("- fix it", model="gpt-5", effort="high", session="u-1") == [
        "copilot", "--prompt=- fix it", *unattended, "--model=gpt-5", "--reasoning-effort=high", "--session-id=u-1"], \
        "the prompt is the value of --prompt=, one argument"
    assert s.argv("go") == ["copilot", "--prompt=go", *unattended]
    assert uuid.UUID(copilot.new_session()).version == 4 and copilot.new_session() != copilot.new_session()


def test_copilot_leaves_vs_codes_shim_off_the_path():
    shim = os.path.join("/Users/me/Library/Application Support/Code/User/globalStorage", "github.copilot-chat", "copilotCli")
    assert copilot.is_shim(shim) and copilot.is_shim(shim + "/") and not copilot.is_shim("/opt/homebrew/bin")
    assert copilot.without_shim(os.pathsep.join(["/usr/bin", shim, "/opt/homebrew/bin"])) == \
        os.pathsep.join(["/usr/bin", "/opt/homebrew/bin"])


def args_of(task) -> list[str]:
    return json.loads(lines(get_artifact_text(task.artifacts[0]))["args"])


async def test_copilots_output_is_the_result_and_a_follow_up_runs_with_the_same_session_id(tmp_path):
    client, http, _ = await agent(tmp_path, "copilot")
    first = await final(client, "fix the bug")
    assert first.status.state == TaskState.TASK_STATE_COMPLETED
    out = lines(get_artifact_text(first.artifacts[0]))
    assert out["answer"] == "fix the bug" and Path(out["cwd"]).resolve().parent == (tmp_path / "work" / "copilot").resolve()
    (sid,) = args_of(first)
    assert sid.startswith("--session-id=") and uuid.UUID(sid.removeprefix("--session-id="))
    again = await final(client, "touch", context_id=first.context_id)
    assert args_of(again) == [sid], "a follow-up continues the same Copilot session"
    other = await final(client, "touch")
    assert args_of(other) != [sid], "another context has its own session"
    await http.aclose()


async def test_a_failed_copilot_run_keeps_its_session_and_the_metadata_names_it(tmp_path):
    client, http, app = await agent(tmp_path, "copilot")
    ex = app.state.agents["copilot"].executor
    failed_first = await final(client, "fail")
    assert failed_first.status.state == TaskState.TASK_STATE_FAILED and "it broke" in str(MessageToDict(failed_first.status))
    kept = ex.sessions[failed_first.context_id]
    assert MessageToDict(failed_first.status.message.metadata)["session"] == kept
    again = await final(client, "go", context_id=failed_first.context_id)
    assert args_of(again) == [f"--session-id={kept}"], "Copilot starts a session it was told to, so the id still serves"
    failed = await final(client, "fail", context_id=again.context_id)
    assert "new session" not in str(MessageToDict(failed.status.message)), "a rate limit must not cost the memory"
    still = await final(client, "go", context_id=again.context_id)
    assert args_of(still) == [f"--session-id={kept}"]
    assert MessageToDict(still.status.message.metadata) == {"exit_code": 0, "session": kept}
    await http.aclose()


async def test_a_message_picks_copilots_model_effort_and_session(tmp_path):
    client, http, _ = await agent(tmp_path, "copilot", model="gpt-5", model_args=["--model={model}"],
                                  effort_args=["--reasoning-effort={effort}"])
    first = await final(client, "a")
    (model, sid) = args_of(first)
    assert model == "--model=gpt-5", "a2a.yaml's model by default"
    picked = await final(client, "b", context_id=first.context_id,
                         metadata={"model": "claude-sonnet-4.5", "reasoning_effort": "XHigh"})
    assert args_of(picked) == ["--model=claude-sonnet-4.5", "--reasoning-effort=xhigh", sid]
    assert MessageToDict(picked.status.message.metadata) == {"exit_code": 0, "model": "claude-sonnet-4.5",
                                                             "effort": "xhigh", "session": sid.removeprefix("--session-id=")}
    named = await final(client, "c", context_id=first.context_id, metadata={"session": "3f2b8c1e-aaaa-4bbb-8ccc-0123456789ab"})
    assert args_of(named)[-1] == "--session-id=3f2b8c1e-aaaa-4bbb-8ccc-0123456789ab", "a caller may resume its own session"
    follow = await final(client, "d", context_id=first.context_id)
    assert args_of(follow)[-1] == "--session-id=3f2b8c1e-aaaa-4bbb-8ccc-0123456789ab", "and the follow-ups keep it"
    fresh = await final(client, "e", context_id=first.context_id, metadata={"session": "new"})
    assert args_of(fresh)[-1] not in (sid, "--session-id=3f2b8c1e-aaaa-4bbb-8ccc-0123456789ab")
    for bad in ({"session": "--yolo"}, {"session": "a b"}, {"effort": "huge"}, {"model": "x; rm -rf /"}):
        rejected = await final(client, "f", context_id=first.context_id, metadata=bad)
        assert rejected.status.state == TaskState.TASK_STATE_REJECTED, bad
    await http.aclose()


@pytest.mark.parametrize("name", ["copilot", "codex"])
async def test_a_follow_up_after_a_restart_still_resumes_the_session(tmp_path, name):
    client, http, _ = await agent(tmp_path, name, resume_args=["resume", "{session}"] if name == "codex" else
                                  ["--session-id={session}"])
    first = await final(client, "json" if name == "codex" else "go")
    await http.aclose()
    client, http, _ = await agent(tmp_path, name, resume_args=["resume", "{session}"] if name == "codex" else
                                  ["--session-id={session}"])      # a new gateway on the same workdir
    again = await final(client, "json" if name == "codex" else "go", context_id=first.context_id)
    if name == "codex":
        assert get_artifact_text(again.artifacts[0]) == 'args: ["resume", "session-1"]'
    else:
        assert args_of(again) == args_of(first)
    await http.aclose()


async def test_a_steer_sent_while_a_run_is_going_waits_its_turn_in_the_same_session(tmp_path):
    client, http, _ = await agent(tmp_path, "copilot")
    one, two = await asyncio.wait_for(asyncio.gather(final(client, "slow", context_id="ctx-1"),
                                                     final(client, "slow", context_id="ctx-1")), 20)
    assert one.status.state == two.status.state == TaskState.TASK_STATE_COMPLETED
    log = (tmp_path / "work" / "copilot" / "ctx-1" / "turns.log").read_text().split()
    assert log == ["begin", "end", "begin", "end"], "two runs in one conversation never overlap"
    assert args_of(one) == args_of(two)
    await http.aclose()


async def test_copilot_without_the_real_cli_fails_as_not_installed_not_through_vs_codes_shim(tmp_path, monkeypatch):
    shim = tmp_path / "Code" / "globalStorage" / "github.copilot-chat" / "copilotCli"
    shim.mkdir(parents=True)
    (shim / "copilot").write_text("#!/bin/sh\necho \"Install GitHub Copilot CLI? ['y/N']\"\nexit 0\n")
    (shim / "copilot").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim), "/usr/bin", "/bin"]))
    app = create_app(config(tmp_path, {"copilot": {"enabled": True}}))
    async with http_for(app) as http:
        task = await final(await client_for(http, "copilot"), "go")
    assert task.status.state == TaskState.TASK_STATE_FAILED and "not installed" in str(MessageToDict(task.status))

    real = tmp_path / "bin"
    real.mkdir()
    (real / "copilot").write_text("#!/bin/sh\necho \"real copilot $1\"\n")
    (real / "copilot").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim), str(real), "/usr/bin", "/bin"]))
    app = create_app(config(tmp_path, {"copilot": {"enabled": True}}))
    async with http_for(app) as http:
        task = await final(await client_for(http, "copilot"), "go")
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert get_artifact_text(task.artifacts[0]) == "real copilot --prompt=go", "the real CLI behind the shim is the one run"
