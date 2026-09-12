"""WebSocket endpoint agents connect to (see POST /api/agents for
enrollment). Bearer-token authenticated at the handshake itself — see
agent_tokens.py — deliberately separate from the browser cookie auth
(FireAuth) the rest of the API uses; registered directly on `app` in
main.py, not through a require_login-gated router.

Protocol (agent <-> server), see docs/MULTI_HOST_PLAN.md for the full
design:

Agent -> server:
    {"type": "hello", "agent_version"}
    {"type": "pong"}
    {"type": "discovery.result", "containers": [...]}          # inspect_one()-shaped dicts
    {"type": "backup.stage", "run_id", "stage", "current"?, "total"?}
    {"type": "backup.chunk", "run_id", "seq", "data_b64"}
    {"type": "backup.complete", "run_id", "total_size"}
    {"type": "backup.error", "run_id", "message"}

Server -> agent:
    {"type": "ping"}
    {"type": "discovery.request"}
    {"type": "backup.request", "run_id", "docker_id", "identity_key", "include_bind_mounts"}
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import agent_backup, agent_tokens, discovery
from ..agents.heartbeat import HeartbeatMonitor, HeartbeatTimeout
from ..agents.registry import get_agent_registry
from ..db import get_session
from ..models import Agent

log = logging.getLogger("containersafe.ws_agent")

router = APIRouter()


async def _authenticate(websocket: WebSocket) -> Agent | None:
    auth_header = websocket.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:].strip()
    if not token:
        return None
    token_hash = agent_tokens.hash_token(token)

    def _lookup() -> Agent | None:
        with get_session() as session:
            return session.query(Agent).filter_by(token_hash=token_hash).first()

    return await asyncio.to_thread(_lookup)


def _mark_connected(agent_id: int) -> None:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is not None:
            agent.connected_at = datetime.now(timezone.utc)
            agent.last_seen_at = agent.connected_at
            session.commit()


def _mark_disconnected(agent_id: int) -> None:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is not None:
            agent.connected_at = None
            agent.last_seen_at = datetime.now(timezone.utc)
            session.commit()


def _mark_heartbeat(agent_id: int, rtt_ms: float, agent_version: str | None) -> None:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            return
        agent.last_seen_at = datetime.now(timezone.utc)
        agent.last_heartbeat_rtt_ms = int(rtt_ms)
        if agent_version:
            agent.agent_version = agent_version
        session.commit()


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket) -> None:
    agent = await _authenticate(websocket)
    if agent is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    host_id = agent.host_id
    agent_id = agent.id
    registry = get_agent_registry()
    registry.attach(host_id, websocket)
    await asyncio.to_thread(_mark_connected, agent_id)
    log.info("agent %r connected", host_id)

    monitor = HeartbeatMonitor(websocket)
    last_agent_version: dict[str, str | None] = {"value": None}

    async def read_loop() -> None:
        while True:
            raw = await websocket.receive_json()
            await _handle_message(raw, host_id, agent_id, monitor, registry, last_agent_version)

    read_task = asyncio.create_task(read_loop())
    heartbeat_task = asyncio.create_task(monitor.run())
    try:
        done, pending = await asyncio.wait([read_task, heartbeat_task], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, HeartbeatTimeout)):
                raise exc
    except (WebSocketDisconnect, HeartbeatTimeout) as exc:
        log.info("agent %r disconnected: %s", host_id, exc)
    finally:
        registry.detach(host_id)
        await asyncio.to_thread(_mark_disconnected, agent_id)
        try:
            await websocket.close()
        except Exception:
            pass


async def _handle_message(
    raw: dict,
    host_id: str,
    agent_id: int,
    monitor: HeartbeatMonitor,
    registry,
    last_agent_version: dict[str, str | None],
) -> None:
    msg_type = raw.get("type")

    if msg_type == "hello":
        last_agent_version["value"] = raw.get("agent_version")
        await asyncio.to_thread(_mark_heartbeat, agent_id, 0.0, raw.get("agent_version"))
        # Ask right away so newly-(re)connected agent's containers show
        # up without waiting for the operator to click "Refresh".
        await registry.send(host_id, {"type": "discovery.request"})
    elif msg_type == "pong":
        rtt_ms = monitor.notify_pong()
        await asyncio.to_thread(_mark_heartbeat, agent_id, rtt_ms, last_agent_version["value"])
    elif msg_type == "discovery.result":
        await asyncio.to_thread(discovery.sync_from_agent, host_id, raw.get("containers") or [])
    elif msg_type == "backup.stage":
        await asyncio.to_thread(
            agent_backup.handle_stage, raw["run_id"], raw.get("stage"), raw.get("current"), raw.get("total")
        )
    elif msg_type == "backup.chunk":
        await asyncio.to_thread(agent_backup.handle_chunk, raw["run_id"], raw["data_b64"])
    elif msg_type == "backup.complete":
        await asyncio.to_thread(agent_backup.handle_complete, raw["run_id"], raw.get("total_size", 0))
    elif msg_type == "backup.error":
        await asyncio.to_thread(agent_backup.handle_error, raw["run_id"], raw.get("message", "unknown error"))
    else:
        log.warning("agent %r sent unknown message type: %r", host_id, msg_type)
