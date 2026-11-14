"""The A2A wire protocol as docs/protocol.md documents it, in raw JSON (no A2A client library): headers, envelopes,
the Agent Card, each method, Server-Sent Events, the task lifecycle, metadata, and error codes. If one of these fails,
the reference is out of date, or the server changed.

`Harness` repeats gobi-harness/engine/a2a.py's calls exactly, so the gateway's agents and examples/echo_agent.py are
both checked against what a Gobi harness actually sends.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest

from conftest import BASE, TOKEN, Script, config, fake_cli
from gobi_a2a.gateway import create_app

V = {"A2A-Version": "1.0"}
AUTH = {"Authorization": f"Bearer {TOKEN}", **V}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
EXAMPLE = Path(__file__).parent.parent / "examples" / "echo_agent.py"


def msg(text: str, **extra) -> dict:
    return {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}], **extra}


class Raw:
    """JSON-RPC 2.0 over HTTP POST to one agent's endpoint."""

    def __init__(self, http: httpx.AsyncClient, endpoint: str, headers: dict | None = None):
        self.http, self.endpoint, self.headers = http, endpoint, AUTH if headers is None else headers

    async def post(self, method: str, params: dict, headers: dict | None = None) -> httpx.Response:
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        return await self.http.post(self.endpoint, json=body, headers=self.headers if headers is None else headers)

    async def __call__(self, method: str, params: dict) -> dict:
        r = await self.post(method, params)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        d = r.json()
        assert d["jsonrpc"] == "2.0" and "id" in d and ("result" in d) != ("error" in d)
        return d

    async def result(self, method: str, params: dict) -> dict:
        d = await self(method, params)
        assert "result" in d, d
        return d["result"]

    async def error(self, method: str, params: dict) -> dict:
        d = await self(method, params)
        assert "error" in d, d
        return d["error"]


class Harness:
    """What gobi-harness/engine/a2a.py sends: card, then SendMessage(returnImmediately), GetTask polls, steers, cancel."""

    def __init__(self, http: httpx.AsyncClient, address: str, token: str | None):
        self.http, self.address = http, address
        self.headers = {"A2A-Version": "1.0", "Content-Type": "application/json",
                        **({"Authorization": f"Bearer {token}"} if token else {})}

    async def read_card(self) -> dict:
        url = urljoin(self.address.rstrip("/") + "/", ".well-known/agent-card.json")
        r = await self.http.get(url, headers={"Authorization": ""}, follow_redirects=False)
        assert r.status_code == 200
        card = r.json()
        endpoint = next(i["url"] for i in card["supportedInterfaces"] if i["protocolBinding"].upper() == "JSONRPC")
        self.endpoint = urljoin(url, endpoint)
        return card

    async def call(self, method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        r = await self.http.post(self.endpoint, json=body, headers=self.headers, follow_redirects=False)
        assert r.status_code == 200, r.status_code
        d = r.json()
        assert not d.get("error"), d["error"]
        return d["result"]

    async def send(self, text: str, task_id=None, context_id=None, metadata=None) -> dict:
        m = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}],
             **({"taskId": task_id} if task_id else {}), **({"contextId": context_id} if context_id else {}),
             **({"metadata": metadata} if metadata else {})}
        r = await self.call("SendMessage", {"message": m, "configuration": {"returnImmediately": True}})
        return r["task"]

    async def run(self, text: str, metadata=None) -> dict:
        task = await self.send(text, metadata=metadata)
        for _ in range(500):
            if task["status"]["state"] in TERMINAL:
                return task
            await asyncio.sleep(0.01)
            task = await self.call("GetTask", {"id": task["id"]})
        raise AssertionError("the task never ended")


def outcome(task: dict) -> tuple[str, str, dict]:
    """engine/a2a.py outcome(): (state, artifacts' text or the status message's, the status message's metadata)."""
    status = task.get("status") or {}
    text = lambda parts: "\n".join(p["text"] for p in parts or [] if p.get("text"))
    made = "\n\n".join(t for t in (text(a.get("parts")) for a in task.get("artifacts") or []) if t)
    return status.get("state", ""), made or text((status.get("message") or {}).get("parts")), (status.get("message") or {}).get("metadata") or {}


@pytest.fixture
async def gw(tmp_path):
    script = Script()
    app = create_app(config(tmp_path, {"claude": {"enabled": True, "model": "claude-sonnet-5"}, "cursor": fake_cli()}),
                     agent_kwargs={"claude": {"run": script}})
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=10)
    yield http, Raw(http, "/claude/"), script, app
    await http.aclose()


# ---- transport, headers, envelope
async def test_the_a2a_version_header_is_required(gw):
    http, rpc, _, _ = gw
    no_version = {"Authorization": f"Bearer {TOKEN}"}
    for headers in (no_version, {**no_version, "A2A-Version": "0.3"}, {**no_version, "A2A-Version": "9.9"}):
        d = (await rpc.post("GetTask", {"id": "x"}, headers=headers)).json()
        assert d["error"]["code"] == -32009 and d["error"]["data"][0]["reason"] == "VERSION_NOT_SUPPORTED"
    assert (await rpc.error("GetTask", {"id": "x"}))["code"] == -32001, "with A2A-Version: 1.0 the call goes through"


async def test_auth_failures_are_http_401_not_json_rpc(gw):
    _, rpc, _, _ = gw
    r = await rpc.post("GetTask", {"id": "x"}, headers=V)
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"} and r.headers["www-authenticate"] == "Bearer"


async def test_the_card_has_the_documented_shape(gw):
    http, _, _, _ = gw
    r = await http.get("/claude/.well-known/agent-card.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    card = r.json()
    assert {"name", "description", "supportedInterfaces", "version", "capabilities", "securitySchemes", "securityRequirements",
            "defaultInputModes", "defaultOutputModes", "skills"} <= set(card)
    assert card["supportedInterfaces"] == [{"url": f"{BASE}/claude/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]
    assert card["capabilities"] == {"streaming": True, "pushNotifications": False}
    assert card["securitySchemes"] == {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}
    assert card["securityRequirements"] == [{"schemes": {"bearer": {}}}]
    assert {"id", "name", "description", "tags", "examples"} <= set(card["skills"][0])


# ---- methods
async def test_send_message_blocking_returns_the_finished_task(gw):
    _, rpc, _, _ = gw
    r = await rpc.result("SendMessage", {"message": msg("write a.txt", metadata={"effort": "xhigh"})})
    assert set(r) == {"task"}
    t = r["task"]
    assert {"id", "contextId", "status", "artifacts", "history"} <= set(t)
    assert t["status"]["state"] == "TASK_STATE_COMPLETED" and t["status"]["timestamp"].endswith("Z")
    m = t["status"]["message"]
    assert m["role"] == "ROLE_AGENT" and m["taskId"] == t["id"] and m["contextId"] == t["contextId"]
    assert m["metadata"] == {"cost_usd": 0.042, "usage": {"input_tokens": 1500, "output_tokens": 200}, "turns": 3,
                             "duration_ms": 1200, "model": "claude-sonnet-5", "effort": "xhigh"}
    assert isinstance(m["metadata"]["turns"], float), "metadata is a protobuf Struct: every number is a JSON double"
    assert t["artifacts"] == [{"artifactId": t["artifacts"][0]["artifactId"], "name": "result", "parts": [{"text": "Did: write a.txt"}]}]
    assert [h["role"] for h in t["history"]] == ["ROLE_USER", "ROLE_AGENT", "ROLE_AGENT"]
    assert t["history"][0]["metadata"] == {"effort": "xhigh"}, "the caller's message is kept as sent"


async def test_return_immediately_answers_submitted_and_get_task_polls_it(gw):
    _, rpc, script, _ = gw
    script.hold = asyncio.Event()
    t = (await rpc.result("SendMessage", {"message": msg("long"), "configuration": {"returnImmediately": True}}))["task"]
    assert t["status"] == {"state": "TASK_STATE_SUBMITTED"}
    for _ in range(100):
        got = await rpc.result("GetTask", {"id": t["id"]})
        if got["status"].get("message"):
            break
        await asyncio.sleep(0.01)
    assert got["status"]["state"] == "TASK_STATE_WORKING" and got["status"]["message"]["parts"][0]["text"] in ("Looking at the files.", "[Read]")
    script.hold.set()
    for _ in range(100):
        got = await rpc.result("GetTask", {"id": t["id"], "historyLength": 1})
        if got["status"]["state"] in TERMINAL:
            break
        await asyncio.sleep(0.01)
    assert got["status"]["state"] == "TASK_STATE_COMPLETED" and len(got["history"]) == 1
    assert "history" not in await rpc.result("GetTask", {"id": t["id"], "historyLength": 0})


async def test_a_follow_up_is_a_new_task_in_the_same_context_and_a_client_may_choose_the_context(gw):
    _, rpc, script, _ = gw
    a = (await rpc.result("SendMessage", {"message": msg("one")}))["task"]
    b = (await rpc.result("SendMessage", {"message": msg("two", contextId=a["contextId"])}))["task"]
    c = (await rpc.result("SendMessage", {"message": msg("three", contextId="my-thread-42")}))["task"]
    assert b["id"] != a["id"] and b["contextId"] == a["contextId"] and c["contextId"] == "my-thread-42"
    assert script.options[1].resume == "sess-1", "the same context resumes the same Claude session"


async def test_a_steer_to_a_running_task_is_queued_and_answers_at_once(gw):
    _, rpc, script, app = gw
    script.hold = asyncio.Event()
    t = (await rpc.result("SendMessage", {"message": msg("long"), "configuration": {"returnImmediately": True}}))["task"]
    steer = msg("go deeper", taskId=t["id"], contextId=t["contextId"], metadata={"effort": "max"})
    r = (await asyncio.wait_for(rpc.result("SendMessage", {"message": steer, "configuration": {"returnImmediately": True}}), 5))["task"]
    assert r["id"] == t["id"] and r["status"]["state"] in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING")
    script.hold.set()
    for _ in range(300):
        got = await rpc.result("GetTask", {"id": t["id"]})
        if len(script.options) == 2 and got["status"]["state"] in TERMINAL:
            break
        await asyncio.sleep(0.01)
    assert got["status"]["state"] == "TASK_STATE_COMPLETED" and got["status"]["message"]["metadata"]["effort"] == "max"
    assert script.options[1].resume == "sess-1" and script.options[1].effort == "max"
    assert got["status"]["message"]["parts"] == [{"text": "Did: go deeper"}], "the status is the last run's"
    assert [(a["name"], a["parts"][0]["text"]) for a in got["artifacts"]] == [("result", "Did: long"), ("result", "Did: go deeper")], \
        "one result artifact per run, oldest first"


async def test_a_message_to_a_finished_task_is_refused(gw):
    _, rpc, _, _ = gw
    t = (await rpc.result("SendMessage", {"message": msg("x")}))["task"]
    e = await rpc.error("SendMessage", {"message": msg("y", taskId=t["id"])})
    assert e["code"] == -32004 and "terminal state" in e["message"]


async def test_cancel_task(gw):
    _, rpc, script, app = gw
    script.hold = asyncio.Event()
    t = (await rpc.result("SendMessage", {"message": msg("long"), "configuration": {"returnImmediately": True}}))["task"]
    for _ in range(200):
        if app.state.agents["claude"].executor.running:
            break
        await asyncio.sleep(0.01)
    c = await rpc.result("CancelTask", {"id": t["id"]})
    assert c["id"] == t["id"] and c["status"]["state"] == "TASK_STATE_CANCELED"
    assert c["status"]["message"]["parts"] == [{"text": "Stopped before it finished."}]
    assert (await rpc.result("GetTask", {"id": t["id"]}))["status"]["state"] == "TASK_STATE_CANCELED"
    e = await rpc.error("CancelTask", {"id": t["id"]})
    assert e["code"] == -32002 and e["data"][0]["reason"] == "TASK_NOT_CANCELABLE"


@pytest.mark.xfail(strict=True, reason="known deviation (docs/protocol.md): a steer queued behind a canceled run still runs")
async def test_a_canceled_task_stays_canceled_even_with_a_steer_queued(gw):
    _, rpc, script, app = gw
    script.hold = asyncio.Event()
    t = (await rpc.result("SendMessage", {"message": msg("long"), "configuration": {"returnImmediately": True}}))["task"]
    for _ in range(200):
        if app.state.agents["claude"].executor.running:
            break
        await asyncio.sleep(0.01)
    await rpc.result("SendMessage", {"message": msg("steer", taskId=t["id"]), "configuration": {"returnImmediately": True}})
    await rpc.result("CancelTask", {"id": t["id"]})
    script.hold = None
    await asyncio.sleep(0.3)
    assert (await rpc.result("GetTask", {"id": t["id"]}))["status"]["state"] == "TASK_STATE_CANCELED"
    assert len(script.options) == 1, "no run starts after a cancel"


async def test_list_tasks(gw):
    _, rpc, _, _ = gw
    for text in ("a", "b", "c"):
        await rpc.result("SendMessage", {"message": msg(text, contextId="ctx-list")})
    page = await rpc.result("ListTasks", {"contextId": "ctx-list", "pageSize": 2})
    assert len(page["tasks"]) == 2 and page["pageSize"] == 2 and page["totalSize"] == 3 and page["nextPageToken"]
    rest = await rpc.result("ListTasks", {"contextId": "ctx-list", "pageSize": 2, "pageToken": page["nextPageToken"]})
    assert len(rest["tasks"]) == 1 and not rest.get("nextPageToken")
    assert all("artifacts" not in t for t in page["tasks"]), "artifacts only with includeArtifacts"
    full = await rpc.result("ListTasks", {"contextId": "ctx-list", "includeArtifacts": True})
    assert all(t["artifacts"] for t in full["tasks"])


async def test_send_streaming_message_is_server_sent_events_of_json_rpc_responses(gw):
    http, _, _, _ = gw
    body = {"jsonrpc": "2.0", "id": 7, "method": "SendStreamingMessage", "params": {"message": msg("stream it")}}
    async with http.stream("POST", "/claude/", json=body, headers={**AUTH, "Accept": "text/event-stream"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = (await r.aread()).decode()
    events = [json.loads(line.removeprefix("data: ")) for line in raw.splitlines() if line.startswith("data: ")]
    assert all(e["jsonrpc"] == "2.0" and e["id"] == 7 and len(e["result"]) == 1 for e in events)
    kinds = [next(iter(e["result"])) for e in events]
    assert kinds == ["task", "statusUpdate", "statusUpdate", "statusUpdate", "artifactUpdate", "statusUpdate"]
    states = [e["result"]["statusUpdate"]["status"]["state"] for e in events if "statusUpdate" in e["result"]]
    assert states == ["TASK_STATE_WORKING"] * 3 + ["TASK_STATE_COMPLETED"]
    assert events[0]["result"]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert events[-1]["result"]["statusUpdate"]["status"]["message"]["metadata"]["cost_usd"] == 0.042
    assert events[4]["result"]["artifactUpdate"]["artifact"]["parts"] == [{"text": "Did: stream it"}]


async def test_subscribe_to_a_running_task_streams_the_rest(gw):
    http, rpc, script, _ = gw
    script.hold = asyncio.Event()
    t = (await rpc.result("SendMessage", {"message": msg("long"), "configuration": {"returnImmediately": True}}))["task"]
    await asyncio.sleep(0.05)
    body = {"jsonrpc": "2.0", "id": 8, "method": "SubscribeToTask", "params": {"id": t["id"]}}

    async def read() -> str:
        async with http.stream("POST", "/claude/", json=body, headers={**AUTH, "Accept": "text/event-stream"}) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            return (await r.aread()).decode()

    reading = asyncio.create_task(read())
    await asyncio.sleep(0.05)
    script.hold.set()
    raw = await asyncio.wait_for(reading, 5)
    events = [json.loads(line.removeprefix("data: "))["result"] for line in raw.splitlines() if line.startswith("data: ")]
    assert "task" in events[0] and events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    e = await rpc.error("SubscribeToTask", {"id": t["id"]})
    assert e["code"] == -32004 and "terminal state" in e["message"]


# ---- task lifecycle outcomes
async def test_rejected_and_failed(gw):
    _, rpc, script, _ = gw
    r = (await rpc.result("SendMessage", {"message": msg("go", metadata={"effort": "extreme"})}))["task"]
    assert r["status"]["state"] == "TASK_STATE_REJECTED" and "Not run: effort" in r["status"]["message"]["parts"][0]["text"]
    assert "artifacts" not in r and not script.options, "rejected before anything ran"
    r = (await rpc.result("SendMessage", {"message": msg("   ")}))["task"]
    assert r["status"]["state"] == "TASK_STATE_REJECTED"
    script.raise_ = RuntimeError("API down")
    r = (await rpc.result("SendMessage", {"message": msg("x")}))["task"]
    assert r["status"]["state"] == "TASK_STATE_FAILED" and "API down" in r["status"]["message"]["parts"][0]["text"]


async def test_a_cli_agents_failure_carries_its_exit_code(gw):
    http, _, _, _ = gw
    r = (await Raw(http, "/cursor/").result("SendMessage", {"message": msg("fail")}))["task"]
    assert r["status"]["state"] == "TASK_STATE_FAILED" and r["status"]["message"]["metadata"] == {"exit_code": 3}
    assert "exited with 3: it broke" in r["status"]["message"]["parts"][0]["text"]


# ---- errors
@pytest.mark.parametrize("method, params, code, reason", [
    ("GetTask", {"id": "nope"}, -32001, "TASK_NOT_FOUND"),
    ("CancelTask", {"id": "nope"}, -32001, "TASK_NOT_FOUND"),
    ("Frobnicate", {}, -32601, None),
    ("SendMessage", {"message": {"parts": "x"}}, -32602, "INVALID_PARAMS"),
    ("SendMessage", {"message": {"role": "ROLE_USER", "parts": [{"text": "no id"}]}}, -32602, "INVALID_PARAMS"),
    ("CreateTaskPushNotificationConfig", {"taskId": "t", "url": "https://x.example/hook"}, -32003, "PUSH_NOTIFICATION_NOT_SUPPORTED"),
    ("GetExtendedAgentCard", {}, -32004, "UNSUPPORTED_OPERATION"),
])
async def test_errors(gw, method, params, code, reason):
    _, rpc, _, _ = gw
    e = await rpc.error(method, params)
    assert e["code"] == code and e["message"]
    if reason:
        assert e["data"][0]["reason"] == reason and e["data"][0]["domain"] == "a2a-protocol.org"


@pytest.mark.parametrize("method", ["tasks/send", "tasks/sendSubscribe", "tasks/get", "tasks/cancel", "tasks/resubscribe",
                                    "message/send", "message/stream", "agent/getAuthenticatedExtendedCard"])
async def test_pre_1_0_method_names_are_not_served(gw, method):
    """docs/protocol.md §7.8: only the 1.0 names; an old name is -32601 with or without the version header."""
    http, rpc, _, _ = gw
    assert (await rpc.error(method, {"id": "x"}))["code"] == -32601
    d = (await rpc.post(method, {"id": "x"}, headers={"Authorization": f"Bearer {TOKEN}"})).json()
    assert d["error"]["code"] == -32601


async def test_the_pre_1_0_card_path_is_not_served(gw):
    http, _, _, _ = gw
    assert (await http.get("/claude/.well-known/agent.json")).status_code == 401, "only agent-card.json is public"
    assert (await http.get("/claude/.well-known/agent.json", headers=AUTH)).status_code == 404
    assert (await http.get("/claude/.well-known/agent-card.json")).status_code == 200


async def test_a_body_that_is_not_json_is_a_parse_error(gw):
    http, _, _, _ = gw
    d = (await http.post("/claude/", content=b"{nope", headers=AUTH)).json()
    assert d["error"]["code"] == -32700 and d["id"] is None


# ---- what a Gobi harness does, against each kind of agent
async def test_the_harness_flow_against_the_gateways_agents(gw):
    http, _, _, _ = gw
    for name, expect in (("claude", "Did: build it"), ("cursor", "answer: build it")):
        h = Harness(http, f"{BASE}/{name}", TOKEN)
        card = await h.read_card()
        assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
        state, said, spent = outcome(await h.run("build it", metadata={"model": "claude-haiku-5"}))
        assert state == "TASK_STATE_COMPLETED" and expect in said and spent.get("model") == "claude-haiku-5"


def load_example():
    spec = importlib.util.spec_from_file_location("echo_agent", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_the_third_party_example_speaks_what_the_harness_needs():
    echo = load_example()
    app = echo.make_app("https://echo.example", "tok")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://echo.example", timeout=10) as http:
        h = Harness(http, "https://echo.example", "tok")
        card = await h.read_card()
        assert card["name"] == "Reverser" and h.endpoint == "https://echo.example/"
        state, said, spent = outcome(await h.run("hello"))
        assert (state, said) == ("TASK_STATE_COMPLETED", "olleh") and spent["usage"]["output_tokens"] == 5
        assert (await http.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}},
                                headers=V)).status_code == 401
        assert outcome(await h.run("   "))[0] == "TASK_STATE_REJECTED"
