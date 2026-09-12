import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import db
from ..agents.registry import AgentOfflineError, get_agent_registry
from ..discovery import sync
from ..docker_endpoint import get_endpoint
from ..models import Agent, DiscoveredContainer
from ..schemas import ContainerOut

log = logging.getLogger("containersafe.containers")

router = APIRouter(prefix="/api/containers", tags=["containers"])


@router.get("", response_model=list[ContainerOut])
def list_containers():
    with db.get_session() as session:
        rows = session.query(DiscoveredContainer).order_by(DiscoveredContainer.name).all()
        return [ContainerOut.model_validate(row) for row in rows]


@router.post("/refresh", response_model=list[ContainerOut])
def refresh_containers():
    """Re-syncs the local Docker endpoint synchronously, and asks every
    currently-connected agent for a fresh discovery.result (fire-and-forget
    — an agent's reply lands asynchronously via ws_agent.py and updates
    DiscoveredContainer separately, since there's no synchronous
    request/reply here to wait on; the returned list reflects whatever's
    already known at the moment of this call, same as it always did for
    "local")."""
    with db.get_session() as session:
        rows: list[DiscoveredContainer] = []
        errors: list[str] = []
        for endpoint in [get_endpoint("local")]:
            try:
                rows.extend(sync(session, endpoint))
            except Exception as exc:  # daemon unreachable, permission error, ...
                errors.append(f"{endpoint.host_id}: {exc}")

        registry = get_agent_registry()
        for agent in session.query(Agent).all():
            if not registry.is_online(agent.host_id):
                continue
            try:
                registry.send_from_thread(agent.host_id, {"type": "discovery.request"})
            except AgentOfflineError as exc:
                log.warning("Failed to request discovery from agent %r: %s", agent.host_id, exc)

        if errors and not rows:
            return JSONResponse({"detail": "; ".join(errors)}, status_code=502)
        all_rows = session.query(DiscoveredContainer).order_by(DiscoveredContainer.name).all()
        return [ContainerOut.model_validate(row) for row in all_rows]
