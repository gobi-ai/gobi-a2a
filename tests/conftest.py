"""Shared fixtures: a config built in code, the gateway in process (httpx over ASGI), and an A2A client for one agent."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import AgentCard, Role, SendMessageConfiguration, SendMessageRequest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from google.protobuf.json_format import ParseDict

from gobi_a2a.config import parse
from gobi_a2a.gateway import create_app

TOKEN = "s3cret"
BASE = "https://gw.example"
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


class Script:
    """Stands in for claude_agent_sdk.query: records the options of each run and plays one back."""

    def __init__(self):
        self.options = []
        self.error = False
        self.raise_ = None
        self.hold: asyncio.Event | None = None

    async def __call__(self, *, prompt, options):
        self.options.append(options)
        yield AssistantMessage(content=[TextBlock("Looking at the files."), ToolUseBlock("t1", "Read", {"file_path": "a.txt"})], model="m")
        if self.hold is not None:
            await self.hold.wait()
        if self.raise_:
            raise self.raise_
        yield ResultMessage(subtype="error_max_turns" if self.error else "success", duration_ms=1200, duration_api_ms=900,
                            is_error=self.error, num_turns=3, session_id=f"sess-{len(self.options)}", total_cost_usd=0.042,
                            usage={"input_tokens": 1500, "output_tokens": 200}, result="" if self.error else f"Did: {prompt}")


def fake_cli(**extra: Any) -> dict[str, Any]:
    """An agents.<cli scaffold> block that runs tests/fake_agent.py instead of the real program."""
    return {"enabled": True, "command": [sys.executable, str(FAKE_AGENT), "{prompt}"], **extra}


def config(tmp_path: Path, agents: dict | None = None, **gateway: Any):
    gw = {"public_url": BASE, "token": TOKEN, "workdir": str(tmp_path / "work"), **gateway}
    return parse({"gateway": gw, "agents": agents if agents is not None else {}}, base=tmp_path, env={})


def http_for(app, token: str | None = TOKEN) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=10,
                             headers={"Authorization": f"Bearer {token}"} if token else {})


async def client_for(http: httpx.AsyncClient, agent: str):
    """An A2A client for /<agent>, built the way the harness finds it: from the card under the agent's address."""
    card = (await http.get(f"/{agent}/.well-known/agent-card.json")).json()
    return await create_client(ParseDict(card, AgentCard()), ClientConfig(streaming=False, httpx_client=http))


def ask(text: str, context_id: str | None = None, *, task_id: str | None = None, metadata: dict | None = None,
        request_metadata: dict | None = None, now: bool = False) -> SendMessageRequest:
    m = new_text_message(text, context_id=context_id, task_id=task_id, role=Role.ROLE_USER)
    if metadata:
        m.metadata.update(metadata)
    r = SendMessageRequest(message=m, **({"configuration": SendMessageConfiguration(return_immediately=True)} if now else {}))
    if request_metadata:
        r.metadata.update(request_metadata)
    return r


async def final(client, text, context_id=None, **kw):
    out = None
    async for r in client.send_message(ask(text, context_id, **kw)):
        out = r
    return out.task


@pytest.fixture
async def gateway(tmp_path):
    """Every agent mounted: claude on a scripted run, the scaffolds on tests/fake_agent.py."""
    script = Script()
    cfg = config(tmp_path, {"claude": {"enabled": True}, "cursor": fake_cli(), "codex": fake_cli(), "omp": fake_cli()})
    app = create_app(cfg, agent_kwargs={"claude": {"run": script}})
    http = http_for(app)
    yield app, http, script
    await http.aclose()
