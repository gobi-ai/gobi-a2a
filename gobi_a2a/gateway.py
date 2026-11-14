"""The gateway: one FastAPI app that mounts each enabled agent's A2A server under its name.

    GET  /healthz                                   liveness (public)
    GET  /                                          the gateway and its agents, with each one's address
    GET  /v1/agents                                 the agents, each with its full Agent Card
    GET  /<agent>/.well-known/agent-card.json       the agent's card (public)
    POST /<agent>/                                  the agent's A2A JSON-RPC endpoint

An agent's address, the one to give the Gobi app, is <public_url>/<agent>: the harness reads the card under it and
sends work to the URL the card names, <public_url>/<agent>/.
"""
from __future__ import annotations

import logging
from typing import Any

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

from gobi_a2a import __version__
from gobi_a2a.agents import AGENTS
from gobi_a2a.agents.common import Mounted
from gobi_a2a.auth import BearerAuth
from gobi_a2a.config import Config

log = logging.getLogger("gobi_a2a")


def create_app(config: Config, *, agent_kwargs: dict[str, dict[str, Any]] | None = None) -> FastAPI:
    """The gateway for `config`. `agent_kwargs` passes extra arguments to an agent's build (tests: claude's `run`)."""
    gw = config.gateway
    mounted: dict[str, Mounted] = {}
    for name, cfg in config.enabled.items():
        token = config.token(name)
        mounted[name] = AGENTS[name].build(name, cfg, url=f"{gw.url}/{name}/", workdir=config.workdir(name), auth=bool(token),
                                           token=token, **(agent_kwargs or {}).get(name, {}))
        log.info("mounted %s at /%s (%s)", name, name, "bearer" if token else "OPEN: no token")
    if not mounted:
        log.warning("no agent is enabled in the config: the gateway serves discovery only")

    app = FastAPI(title="gobi-a2a", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config, app.state.agents = config, mounted

    def entry(m: Mounted) -> dict[str, Any]:
        base = f"{gw.url}/{m.name}"
        return {"id": m.name, "name": m.card.name, "description": m.card.description, "address": base,
                "endpoint": base + "/", "card": base + AGENT_CARD_WELL_KNOWN_PATH, "auth": "bearer" if m.token else "none"}

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> dict[str, Any]:
        return {"name": "gobi-a2a", "version": __version__, "url": gw.url,
                "agents": [entry(m) for m in mounted.values()],
                "disabled": [n for n in AGENTS if n not in mounted]}

    @app.get("/v1/agents")
    async def agents() -> dict[str, Any]:
        return {"agents": [{**entry(m), "agent_card": MessageToDict(m.card)} for m in mounted.values()]}

    for name, m in mounted.items():
        app.mount(f"/{name}", m.app, name=name)

    app.add_middleware(BearerAuth, token=gw.token, agent_tokens={n: m.token for n, m in mounted.items()})
    return app
