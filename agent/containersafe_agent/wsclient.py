"""Outbound WebSocket client — connects to the server, authenticates
with a Bearer token at the handshake, replies to heartbeats, and
reconnects forever with exponential backoff on any failure. Same shape
as FiresLog's own agent wsclient.py (see docs/MULTI_HOST_PLAN.md) — the
websockets library's own ping_interval is disabled since the SERVER
drives heartbeats at the application level (a {"type": "ping"} we reply
{"type": "pong"} to below); a second, independent protocol-level ping
underneath would just be redundant.
"""

import asyncio
import json
import logging

import websockets

from . import config
from .dispatch import Dispatcher

log = logging.getLogger("containersafe_agent.wsclient")

RECONNECT_BASE_DELAY = 0.5
RECONNECT_MAX_DELAY = 15.0


async def _connect_once() -> None:
    headers = {"Authorization": f"Bearer {config.AGENT_TOKEN}"}
    async with websockets.connect(config.SERVER_URL, additional_headers=headers, ping_interval=None) as ws:
        log.info("connected to %s", config.SERVER_URL)

        async def send(message: dict) -> None:
            await ws.send(json.dumps(message))

        dispatcher = Dispatcher(send)
        await send({"type": "hello", "agent_version": config.AGENT_VERSION})
        try:
            async for raw in ws:
                message = json.loads(raw)
                if message.get("type") == "ping":
                    await send({"type": "pong"})
                    continue
                await dispatcher.handle(message)
        finally:
            await dispatcher.stop_all()


async def run() -> None:
    """Runs forever, reconnecting on any failure, until cancelled. A
    clean (however brief) session resets the backoff — only sustained
    failure keeps stretching the delay out to the 15s cap."""
    delay = RECONNECT_BASE_DELAY
    while True:
        try:
            await _connect_once()
            delay = RECONNECT_BASE_DELAY
            log.info("disconnected from %s — reconnecting in %.1fs", config.SERVER_URL, delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - log and retry, an agent must never crash out
            log.warning("connection to %s failed: %s — retrying in %.1fs", config.SERVER_URL, exc, delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY)
