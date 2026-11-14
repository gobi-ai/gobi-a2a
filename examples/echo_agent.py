"""A minimal third-party A2A agent, standalone: it answers each task with the task's text reversed.

It depends on the open-source A2A Python SDK only (not on gobi-a2a), and it speaks everything a Gobi harness needs:
a public Agent Card that asks for HTTP Bearer, JSON-RPC SendMessage / GetTask / CancelTask (and streaming), progress as
status messages, the result as an artifact, and what the run cost as the final status message's metadata.
See docs/protocol.md, "Implementing a third-party agent".

    pip install "a2a-sdk[http-server]>=1.3,<2" uvicorn
    ECHO_TOKEN=$(openssl rand -hex 24) ECHO_PUBLIC_URL=https://<your-domain> python examples/echo_agent.py

Then add https://<your-domain> and the token as a worker in the Gobi app. tests/test_protocol.py drives this file the
way the harness does.
"""
from __future__ import annotations

import hmac
import os

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentSkill, HTTPAuthSecurityScheme, SecurityRequirement,
                       SecurityScheme, StringList, TaskState)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, PROTOCOL_VERSION_CURRENT, TransportProtocol
from starlette.applications import Starlette
from starlette.responses import JSONResponse


class Reverser(AgentExecutor):
    """One task = one run. Emits: SUBMITTED (the task) -> WORKING -> WORKING + a note -> artifact -> COMPLETED + cost."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:                                          # a new task: announce it first
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue, task.id, task.context_id)
        text = context.get_user_input().strip()
        if not text:                                              # refuse before doing anything: REJECTED
            await up.reject(up.new_agent_message([new_text_part("Nothing to do: the message has no text.")]))
            return
        await up.start_work()                                     # WORKING
        await up.update_status(TaskState.TASK_STATE_WORKING, up.new_agent_message([new_text_part("Reversing ...")]))
        answer = text[::-1]
        await up.add_artifact([new_text_part(answer)], name="result")
        spent = {"cost_usd": 0.0, "usage": {"input_tokens": len(text), "output_tokens": len(answer)}}
        await up.complete(up.new_agent_message([new_text_part(answer)], metadata=spent))   # COMPLETED, terminal

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task                               # nothing long-running to stop here
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def agent_card(url: str, *, auth: bool) -> AgentCard:
    card = AgentCard(
        name="Reverser", description="Answers with the text of the task reversed. An A2A example.", version="1",
        supported_interfaces=[AgentInterface(url=url.rstrip("/") + "/", protocol_binding=TransportProtocol.JSONRPC.value,
                                             protocol_version=PROTOCOL_VERSION_CURRENT)],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        skills=[AgentSkill(id="reverse", name="Reverse text", description="Reverses the task's text.", tags=["example"])])
    if auth:
        card.security_schemes["bearer"].CopyFrom(SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")))
        card.security_requirements.append(SecurityRequirement(schemes={"bearer": StringList()}))
    return card


class Bearer:
    """Every request but the card's needs `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str | None):
        self.app, self.token = app, (token or "").encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.token and scope["path"] != AGENT_CARD_WELL_KNOWN_PATH:
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, b"Bearer " + self.token):
                await JSONResponse({"error": "unauthorized"}, 401, {"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        await self.app(scope, receive, send)


def make_app(url: str, token: str | None):
    card = agent_card(url, auth=bool(token))
    handler = DefaultRequestHandler(agent_executor=Reverser(), task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/"))
    return Bearer(app, token)


if __name__ == "__main__":
    import uvicorn

    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8001"))
    token = os.environ.get("ECHO_TOKEN") or None
    if not token:
        print("warning: ECHO_TOKEN is not set, so the agent is open to anyone who can reach it")
    uvicorn.run(make_app(os.environ.get("ECHO_PUBLIC_URL") or f"http://{host}:{port}", token), host=host, port=port)
