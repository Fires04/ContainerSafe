"""Message dispatcher for the agent side of /ws/agent — handles
discovery.request (report this host's containers) and backup.request
(assemble + stream a backup archive up to the server). See
docs/MULTI_HOST_PLAN.md for the full protocol/rationale — most notably:
this agent never sees storage-target credentials, it only ever streams
raw archive bytes to the server, which alone decides where they end up.
"""

import asyncio
import base64
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

import docker
import docker.errors

from containersafe_agentcore.archive import ArchiveError
from containersafe_agentcore.assemble import assemble_backup_archive
from containersafe_agentcore.discovery import list_discovered

from . import config

log = logging.getLogger("containersafe_agent.dispatch")

CHUNK_SIZE = 512 * 1024  # 512KB raw (~700KB once base64-encoded per message)


class Dispatcher:
    def __init__(self, send: Callable[[dict], Awaitable[None]]) -> None:
        self._send = send
        self._client = docker.from_env()
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "discovery.request":
            await self._handle_discovery_request()
        elif msg_type == "backup.request":
            # Backgrounded so the read loop keeps servicing pings (and
            # could, in principle, handle a second concurrent request)
            # while this container's own backup is still running.
            task = asyncio.create_task(self._handle_backup_request(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            log.warning("server sent unknown message type: %r", msg_type)

    async def stop_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    async def _handle_discovery_request(self) -> None:
        try:
            containers = await asyncio.to_thread(list_discovered, self._client, config.AGENT_CONTAINER_NAME)
        except Exception:
            log.exception("discovery failed")
            containers = []
        await self._send({"type": "discovery.result", "containers": containers})

    async def _handle_backup_request(self, message: dict) -> None:
        run_id = message.get("run_id")
        docker_id = message.get("docker_id")
        identity_key = message.get("identity_key")
        host_id = message.get("host_id") or "unknown"
        include_bind_mounts = bool(message.get("include_bind_mounts", True))

        staging_local = Path(config.DATA_DIR) / "staging" / str(run_id)
        staging_local.mkdir(parents=True, exist_ok=True)
        staging_host = str(Path(config.HOST_DATA_DIR) / "staging" / str(run_id))

        log_lines: list[str] = []

        def log_line(msg: str) -> None:
            log_lines.append(msg)
            log.info("[run %s] %s", run_id, msg)

        loop = asyncio.get_running_loop()

        def on_stage(stage: str, current: int | None, total: int | None) -> None:
            # Called from the worker thread assemble_backup_archive runs
            # in (via asyncio.to_thread below) — schedule the actual send
            # back onto this coroutine's own event loop.
            asyncio.run_coroutine_threadsafe(
                self._send(
                    {"type": "backup.stage", "run_id": run_id, "stage": stage, "current": current, "total": total}
                ),
                loop,
            )

        try:
            final_path = await asyncio.to_thread(
                assemble_backup_archive,
                self._client,
                docker_id,
                identity_key,
                host_id,
                include_bind_mounts,
                staging_local,
                staging_host,
                config.AGENT_VERSION,
                on_stage,
                log_line,
            )

            await self._send({"type": "backup.stage", "run_id": run_id, "stage": "uploading"})
            total_size = final_path.stat().st_size
            seq = 0
            with final_path.open("rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    await self._send(
                        {
                            "type": "backup.chunk",
                            "run_id": run_id,
                            "seq": seq,
                            "data_b64": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                    seq += 1
            await self._send({"type": "backup.complete", "run_id": run_id, "total_size": total_size})
            log.info("[run %s] sent %d bytes in %d chunk(s)", run_id, total_size, seq)

        except (ArchiveError, docker.errors.DockerException, OSError) as exc:
            log.exception("backup run %s failed", run_id)
            await self._send({"type": "backup.error", "run_id": run_id, "message": str(exc)})
        finally:
            shutil.rmtree(staging_local, ignore_errors=True)
