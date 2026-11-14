"""The gateway: what it mounts, where, how it is found, and who may call it."""
from __future__ import annotations

from urllib.parse import urljoin

import httpx
import pytest
from a2a.types import TaskState

from conftest import BASE, TOKEN, Script, client_for, config, fake_cli, final, http_for
from gobi_a2a.gateway import create_app

AGENTS = ["claude", "cursor", "codex", "omp", "copilot"]
RPC = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "nope"}}


async def test_by_default_only_claude_is_mounted(tmp_path):
    app = create_app(config(tmp_path))
    assert list(app.state.agents) == ["claude"]
    async with http_for(app) as http:
        assert (await http.get("/claude/.well-known/agent-card.json")).status_code == 200
        for off in ("cursor", "codex", "omp", "copilot"):
            assert (await http.get(f"/{off}/.well-known/agent-card.json")).status_code == 404
        index = (await http.get("/")).json()
    assert [a["id"] for a in index["agents"]] == ["claude"] and index["disabled"] == ["cursor", "codex", "omp", "copilot"]


async def test_every_enabled_agent_is_mounted_under_its_name(gateway):
    app, http, _ = gateway
    assert list(app.state.agents) == AGENTS
    for name in AGENTS:
        card = (await http.get(f"/{name}/.well-known/agent-card.json")).json()
        assert card["supportedInterfaces"][0]["url"] == f"{BASE}/{name}/"
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"


async def test_the_index_and_v1_agents_list_each_agents_address(gateway):
    _, http, _ = gateway
    index = (await http.get("/")).json()
    assert index["name"] == "gobi-a2a" and index["url"] == BASE and index["disabled"] == []
    claude = index["agents"][0]
    assert claude == {"id": "claude", "name": "Claude Code", "description": claude["description"], "address": f"{BASE}/claude",
                      "endpoint": f"{BASE}/claude/", "card": f"{BASE}/claude/.well-known/agent-card.json", "auth": "bearer"}
    agents = (await http.get("/v1/agents")).json()["agents"]
    assert [a["id"] for a in agents] == AGENTS
    assert agents[1]["agent_card"]["name"] == "Cursor Agent" and agents[2]["agent_card"]["name"] == "Codex" \
        and agents[3]["agent_card"]["name"] == "omp" and agents[4]["agent_card"]["name"] == "GitHub Copilot CLI"
    assert (await http.get("/healthz")).json() == {"status": "ok"}


async def test_the_address_given_to_gobi_finds_the_card_the_way_the_harness_does(gateway):
    """gobi-harness engine/a2a.py: card_url = urljoin(address + "/", ".well-known/agent-card.json"), no redirects."""
    _, http, _ = gateway
    for name in AGENTS:
        address = f"{BASE}/{name}"
        url = urljoin(address.rstrip("/") + "/", ".well-known/agent-card.json")
        r = await http.get(url, headers={"Authorization": ""})
        assert r.status_code == 200
        endpoint = urljoin(url, r.json()["supportedInterfaces"][0]["url"])
        assert endpoint == f"{address}/"


async def test_cards_and_health_are_public_and_everything_else_needs_the_token(gateway):
    app, _, _ = gateway
    async with http_for(app, token=None) as bare:
        for name in AGENTS:
            card = (await bare.get(f"/{name}/.well-known/agent-card.json")).json()
            assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
            assert card["securityRequirements"] == [{"schemes": {"bearer": {}}}]
            for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
                r = await bare.post(f"/{name}/", json=RPC, headers=headers)
                assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert (await bare.get("/healthz")).status_code == 200
        for path in ("/", "/v1/agents", "/nowhere", "/claude/.well-known/other.json"):
            assert (await bare.get(path)).status_code == 401, path
        ok = await bare.post("/claude/", json=RPC, headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200 and "error" in ok.json(), "past auth, the agent answers: no such task"


async def test_an_agent_with_its_own_token_takes_only_that_one(tmp_path):
    app = create_app(config(tmp_path, {"cursor": fake_cli(token="cursor-only")}))
    async with http_for(app, token=None) as bare:
        assert (await bare.post("/cursor/", json=RPC, headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 401
        assert (await bare.post("/cursor/", json=RPC, headers={"Authorization": "Bearer cursor-only"})).status_code == 200
        assert (await bare.post("/claude/", json=RPC, headers={"Authorization": "Bearer cursor-only"})).status_code == 401
        assert (await bare.post("/claude/", json=RPC, headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
        assert (await bare.get("/", headers={"Authorization": "Bearer cursor-only"})).status_code == 401


async def test_without_auth_nothing_asks_for_a_token_and_the_cards_say_so(tmp_path):
    app = create_app(config(tmp_path, token=None, no_auth=True))
    async with http_for(app, token=None) as bare:
        card = (await bare.get("/claude/.well-known/agent-card.json")).json()
        assert "securitySchemes" not in card and "securityRequirements" not in card
        assert (await bare.post("/claude/", json=RPC)).status_code == 200
        assert (await bare.get("/")).json()["agents"][0]["auth"] == "none"


async def test_without_a_public_url_the_cards_name_the_listening_address(tmp_path):
    app = create_app(config(tmp_path, public_url=None, host="127.0.0.1", port=8123))
    async with http_for(app) as http:
        card = (await http.get("/claude/.well-known/agent-card.json")).json()
    assert card["supportedInterfaces"][0]["url"] == "http://127.0.0.1:8123/claude/"


async def test_one_task_through_each_mounted_agent(gateway):
    _, http, script = gateway
    for name in AGENTS:
        client = await client_for(http, name)
        task = await final(client, "hello there")
        assert task.status.state == TaskState.TASK_STATE_COMPLETED, name
    assert len(script.options) == 1, "claude ran once; the others ran their commands"


async def test_agents_keep_their_tasks_apart(gateway):
    _, http, _ = gateway
    task = await final(await client_for(http, "claude"), "mine")
    rpc = {**RPC, "params": {"id": task.id}}
    assert "result" in (await http.post("/claude/", json=rpc)).json()
    assert "error" in (await http.post("/cursor/", json=rpc)).json()


async def test_the_agents_workspaces_are_apart(gateway, tmp_path):
    app, _, _ = gateway
    claude, cursor = app.state.agents["claude"].executor, app.state.agents["cursor"].executor
    assert claude.s.workdir == (tmp_path / "work" / "claude").resolve()
    assert cursor.s.workdir == (tmp_path / "work" / "cursor").resolve()
