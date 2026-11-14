"""What every agent behind the gateway shares: its config's common fields, the per-run model/effort a caller may pick,
the Agent Card, and the A2A app a mounted agent is.

An agent module exposes:

    Config                      a pydantic model of its block under `agents:` in a2a.yaml (subclass of AgentConfig)
    build(name, cfg, *, url, workdir, auth, **kw) -> Mounted

and the gateway mounts `Mounted.app` at /<name>.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentSkill, HTTPAuthSecurityScheme, SecurityRequirement,
                       SecurityScheme, StringList)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.applications import Starlette

NOTE_CHARS = 2000                                 # one progress message, at most
SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")   # the Claude Agent SDK's EffortLevel; a test keeps them equal
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}")   # claude-opus-5-5, opus, claude-x[1m], gpt-5, a Bedrock id
SCHEME = "bearer"


class AgentConfig(BaseModel):
    """The fields every agent's block in a2a.yaml has. Unknown keys are an error, so a typo does not pass silently."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    name: str | None = None                       # the name on the Agent Card
    description: str | None = None                # what the card says it is for; the harness shows it to its super agent
    token: str | None = None                      # this agent's own bearer token, instead of the gateway's
    workdir: Path | None = None                   # one folder per A2A context under it; default <gateway.workdir>/<agent>

    @field_validator("name", "description", "token", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v


def check_model(v: Any) -> str | None:
    if v in (None, ""):
        return None
    m = v.strip() if isinstance(v, str) else ""
    if not MODEL_NAME.fullmatch(m):
        raise ValueError(f"{v!r} is not a model name")
    return m


def check_effort(v: Any) -> str | None:
    if v in (None, ""):
        return None
    e = v.strip().lower() if isinstance(v, str) else ""
    if e not in EFFORTS:
        raise ValueError(f"{v!r} is not one of {', '.join(EFFORTS)}")
    return e


class BadOverride(ValueError):
    """A caller's model or effort that cannot be used: the task is rejected with these words."""


def overrides(*metadata: dict | None) -> dict[str, str]:
    """{"model"?, "effort"?} a caller asked for, from the first metadata that names each. Checked, normalized.
    "reasoning_effort" is another name for "effort"."""
    out: dict[str, Any] = {}
    for md in metadata:
        for k, names in (("model", ("model",)), ("effort", ("effort", "reasoning_effort"))):
            v = next((md[n] for n in names if md.get(n) not in (None, "")), None) if isinstance(md, dict) else None
            if k not in out and v is not None:
                out[k] = v
    try:
        if "model" in out:
            out["model"] = check_model(out["model"])
    except ValueError:
        raise BadOverride(f"model {out['model']!r} is not a model name") from None
    try:
        if "effort" in out:
            out["effort"] = check_effort(out["effort"])
    except ValueError:
        raise BadOverride(f"effort {out['effort']!r} is not one of {', '.join(EFFORTS)}") from None
    return out


def workspace(base: Path, context_id: str) -> Path:
    """The folder an A2A context works in. Tasks in the same context share it."""
    ws = base / SAFE_ID.sub("_", context_id)[:100]
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def card(url: str, name: str, description: str, *, auth: bool, skills: list[AgentSkill]) -> AgentCard:
    """An Agent Card whose JSON-RPC endpoint is `url` (with a trailing slash: the harness follows no redirects)."""
    c = AgentCard(
        name=name, description=description, version="1",
        supported_interfaces=[AgentInterface(url=url.rstrip("/") + "/", protocol_binding=TransportProtocol.JSONRPC.value,
                                             protocol_version=PROTOCOL_VERSION_CURRENT)],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"], skills=skills)
    if auth:
        c.security_schemes[SCHEME].CopyFrom(SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")))
        c.security_requirements.append(SecurityRequirement(schemes={SCHEME: StringList()}))
    return c


@dataclass
class Mounted:
    """One agent, ready to mount: its A2A app (card + JSON-RPC at its root), its card, and its executor."""
    name: str
    app: Starlette
    card: AgentCard
    executor: AgentExecutor
    token: str | None                             # the bearer token its requests need; None: open


def a2a_app(executor: AgentExecutor, agent_card: AgentCard) -> Starlette:
    """GET /.well-known/agent-card.json and POST / (A2A JSON-RPC), relative to wherever it is mounted."""
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card)
    app = Starlette(routes=create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/"))
    app.state.executor = executor
    return app
