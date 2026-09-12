"""Server-driven ping/pong heartbeat over a live /ws/agent connection —
same shape as FiresLog's own heartbeat.py. The agent's own WS client
connects with ping_interval disabled at the protocol level (see
agent/containersafe_agent/wsclient.py) specifically so this
application-level heartbeat is the only one in play, not two competing
mechanisms.
"""

import asyncio
import time

from fastapi import WebSocket

from .. import config


class HeartbeatTimeout(Exception):
    pass


class HeartbeatMonitor:
    """One instance per live /ws/agent connection. `notify_pong()` is
    called by ws_agent.py's read loop whenever a pong arrives; `run()` is
    the background loop that pings on an interval and raises
    HeartbeatTimeout if too much time passes without one — ws_agent.py
    races this against its own read loop and closes the socket on
    whichever finishes first."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._last_pong = time.monotonic()
        self._last_ping_sent: float | None = None

    def notify_pong(self) -> float:
        now = time.monotonic()
        self._last_pong = now
        rtt_ms = (now - self._last_ping_sent) * 1000 if self._last_ping_sent is not None else 0.0
        return rtt_ms

    async def run(self) -> None:
        interval = config.AGENT_HEARTBEAT_INTERVAL_SECONDS
        timeout = config.AGENT_HEARTBEAT_TIMEOUT_SECONDS
        while True:
            await asyncio.sleep(interval)
            if time.monotonic() - self._last_pong > timeout:
                raise HeartbeatTimeout(f"no pong received in over {timeout}s")
            self._last_ping_sent = time.monotonic()
            await self._websocket.send_json({"type": "ping"})
