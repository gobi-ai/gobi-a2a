"""Bearer auth for the whole gateway, as one ASGI middleware.

Public, so a caller can learn how to sign in before it has a credential:
    GET /healthz
    GET /<agent>/.well-known/agent-card.json      (each card says it wants HTTP Bearer)

Everything else carries `Authorization: Bearer <token>`: under /<agent>/, that agent's token (its own, or the
gateway's); anywhere else (/, /v1/agents), the gateway's. Compared in constant time. With no token for a path (the
gateway runs with no_auth and the agent has no token of its own), the path is open.
"""
from __future__ import annotations

import hmac

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC = {"/healthz"}


class BearerAuth:
    def __init__(self, app: ASGIApp, *, token: str | None, agent_tokens: dict[str, str | None]):
        self.app = app
        self.token = (token or "").encode()
        self.agents = {name: (t or "").encode() for name, t in agent_tokens.items()}

    def needed(self, path: str) -> bytes:
        """The token `path` needs; b"" for none."""
        if path in PUBLIC:
            return b""
        first, _, rest = path.lstrip("/").partition("/")
        if first in self.agents:
            if "/" + rest == AGENT_CARD_WELL_KNOWN_PATH:
                return b""
            return self.agents[first]
        return self.token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            want = self.needed(scope["path"])
            if want:
                got = dict(scope["headers"]).get(b"authorization", b"")
                if not hmac.compare_digest(got, b"Bearer " + want):
                    await JSONResponse({"error": "unauthorized"}, status_code=401,
                                       headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                    return
        await self.app(scope, receive, send)
