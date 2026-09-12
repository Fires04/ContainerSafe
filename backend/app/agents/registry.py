"""In-memory registry of live /ws/agent connections — same shape as
FiresLog's own AgentConnectionRegistry. One process-wide instance
(get_agent_registry()). Tracks which host_id currently has an open
WebSocket, and correlates single request/reply pairs sent down to an
agent via req_id-keyed asyncio.Futures (used for discovery.request and
the initial backup.request ack; the backup archive stream itself is
correlated by run_id instead — see backup.py — since it's a multi-message
stream, not a single reply).
"""

import asyncio
import logging
import uuid

from fastapi import WebSocket

log = logging.getLogger("containersafe.agents.registry")


class AgentOfflineError(Exception):
    pass


class AgentTimeoutError(Exception):
    pass


_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from main.py's lifespan startup — lets send_from_thread()
    below schedule an async send onto the loop that actually owns the
    WebSocket connections, from code that runs off that loop entirely
    (backup.py's run_backup_job executes via FastAPI's BackgroundTasks or
    APScheduler, both worker threads, never the event loop itself)."""
    global _main_loop
    _main_loop = loop


class AgentConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future] = {}

    def attach(self, host_id: str, websocket: WebSocket) -> None:
        self._connections[host_id] = websocket

    def detach(self, host_id: str) -> None:
        self._connections.pop(host_id, None)

    def is_online(self, host_id: str) -> bool:
        return host_id in self._connections

    async def send(self, host_id: str, message: dict) -> None:
        websocket = self._connections.get(host_id)
        if websocket is None:
            raise AgentOfflineError(f"Agent {host_id!r} is not connected")
        await websocket.send_json(message)

    async def request(self, host_id: str, message: dict, timeout: float) -> dict:
        """Sends `message` (with a fresh req_id) to host_id and awaits
        the matching reply delivered via deliver()."""
        websocket = self._connections.get(host_id)
        if websocket is None:
            raise AgentOfflineError(f"Agent {host_id!r} is not connected")
        req_id = uuid.uuid4().hex
        payload = {**message, "req_id": req_id}
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future
        try:
            await websocket.send_json(payload)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            raise AgentTimeoutError(f"Agent {host_id!r} did not reply in time") from None
        finally:
            self._pending.pop(req_id, None)

    def send_from_thread(self, host_id: str, message: dict, timeout: float = 5.0) -> None:
        """Synchronous entry point for a worker thread (backup.py) to
        push a message down to a connected agent, via
        asyncio.run_coroutine_threadsafe onto the main event loop that
        actually owns the connection. Raises AgentOfflineError
        synchronously (via future.result()) if the agent isn't
        connected, same as calling send() directly would."""
        if _main_loop is None:
            raise AgentOfflineError("Server event loop not ready yet")
        future = asyncio.run_coroutine_threadsafe(self.send(host_id, message), _main_loop)
        future.result(timeout=timeout)

    def deliver(self, req_id: str, message: dict) -> None:
        """Called by ws_agent.py's read loop when a reply with a
        matching req_id arrives — resolves the pending Future from
        request() above. A req_id with no pending Future (already timed
        out, or an unsolicited message) is silently ignored."""
        future = self._pending.get(req_id)
        if future is not None and not future.done():
            future.set_result(message)


_registry = AgentConnectionRegistry()


def get_agent_registry() -> AgentConnectionRegistry:
    return _registry
