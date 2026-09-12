import re

from fastapi import APIRouter, HTTPException, Request

from .. import agent_tokens, config, db
from ..agents.install_links import get_install_link_store
from ..agents.registry import get_agent_registry
from ..models import Agent, BackupJob, DiscoveredContainer, StorageTarget
from ..schemas import AgentEnrollResult, AgentIn, AgentOut

router = APIRouter(prefix="/api/agents", tags=["agents"])

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(400, "Name must contain at least one letter or digit")
    return slug


def _to_out(agent: Agent) -> AgentOut:
    return AgentOut(
        id=agent.id,
        host_id=agent.host_id,
        online=get_agent_registry().is_online(agent.host_id),
        token_prefix=agent.token_prefix,
        created_at=agent.created_at,
        connected_at=agent.connected_at,
        last_seen_at=agent.last_seen_at,
        last_heartbeat_rtt_ms=agent.last_heartbeat_rtt_ms,
        agent_version=agent.agent_version,
    )


def _derive_urls(request: Request) -> tuple[str, str]:
    """Returns (ws_server_url, http_base). Prefers the operator-configured
    AGENT_SERVER_URL (see config.py) when set, so a proxied/public
    deployment doesn't leak whatever internal host/IP the browser
    happened to use for this one request; otherwise derives both
    straight from the incoming request (fine for a direct-LAN setup)."""
    if config.AGENT_SERVER_URL:
        ws_url = config.AGENT_SERVER_URL
        scheme = "https" if ws_url.startswith("wss://") else "http"
        http_base = f"{scheme}://{ws_url.split('://', 1)[1].split('/', 1)[0]}"
    else:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_url = f"{scheme}://{request.url.netloc}/ws/agent"
        http_base = str(request.base_url).rstrip("/")
    return ws_url, http_base


@router.get("", response_model=list[AgentOut])
def list_agents():
    with db.get_session() as session:
        rows = session.query(Agent).order_by(Agent.host_id).all()
        return [_to_out(a) for a in rows]


@router.post("", response_model=AgentEnrollResult, status_code=201)
def enroll_agent(payload: AgentIn, request: Request):
    """Generates a new agent + bearer token. The plaintext token is
    returned exactly once, here — it's never stored (only its
    HMAC-SHA256 hash, see agent_tokens.py) or logged anywhere again, so
    losing this response means re-enrolling. See docs/MULTI_HOST_PLAN.md.

    Also mints a one-time install link (agents/install_links.py) from
    this same still-in-scope plaintext token, so the response can offer
    a single `curl | bash` command as an alternative to hand-copying the
    .env snippet."""
    host_id = _slugify(payload.host_id)
    token = agent_tokens.generate_token()

    with db.get_session() as session:
        if session.query(Agent).filter_by(host_id=host_id).first():
            raise HTTPException(409, f"An agent named {host_id!r} already exists")
        agent = Agent(
            host_id=host_id, token_hash=agent_tokens.hash_token(token), token_prefix=agent_tokens.token_prefix(token)
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)

        ws_url, http_base = _derive_urls(request)
        env_snippet = f"SERVER_URL={ws_url}\nAGENT_TOKEN={token}\n"

        bundle_url = f"{http_base}/agent-install/bundle.tar.gz"
        code = get_install_link_store().create(agent.id, token, ws_url, bundle_url)
        install_url = f"{http_base}/agent-install/{code}"
        curl_command = f"curl -fsSL {install_url} | bash"

        return AgentEnrollResult(
            agent=_to_out(agent),
            token=token,
            env_snippet=env_snippet,
            install_url=install_url,
            curl_command=curl_command,
        )


@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: int):
    with db.get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "Not found")
        host_id = agent.host_id

        in_use = session.query(BackupJob).filter_by(host_id=host_id).count()
        if in_use:
            raise HTTPException(409, f"{in_use} backup job(s) still reference this agent's host — delete those first")
        target_in_use = session.query(StorageTarget).filter_by(host_id=host_id).count()
        if target_in_use:
            raise HTTPException(409, f"{target_in_use} storage target(s) still reference this agent's host")

        session.query(DiscoveredContainer).filter_by(host_id=host_id).delete()
        session.delete(agent)
        session.commit()
    get_agent_registry().detach(host_id)
