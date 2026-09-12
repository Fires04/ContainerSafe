"""Public (unauthenticated) endpoints the one-command agent installer
hits — registered directly on `app` in main.py, not behind the browser
cookie auth the rest of the API uses, same reasoning as /ws/agent: these
run unattended on a fresh host with no session cookie at all. Security
comes from the one-time, short-TTL code (see agents/install_links.py),
not from login — the bearer token only ever appears embedded in the ONE
script response a given code serves.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from ..agents.install_links import get_install_link_store

router = APIRouter()

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "agent-install.sh.template"
_TEMPLATE = _TEMPLATE_PATH.read_text()
_BUNDLE_PATH = Path("/app/agent-bundle.tar.gz")


@router.get("/agent-install/bundle.tar.gz")
async def agent_bundle():
    """Just source code (agent/ + agentcore/), no secrets — always
    available, not gated by a one-time code. Built once at image-build
    time, see the Dockerfile's `agent-bundle` stage."""
    if not _BUNDLE_PATH.exists():
        raise HTTPException(404, "Agent bundle not built into this image")
    return FileResponse(_BUNDLE_PATH, media_type="application/gzip", filename="containersafe-agent.tar.gz")


@router.get("/agent-install/{code}")
async def agent_install_script(code: str, request: Request):
    link = get_install_link_store().consume(code)
    if link is None:
        return PlainTextResponse(
            "# This install link has already been used or has expired.\n"
            "# Generate a new one from the server's Agents page.\n",
            status_code=410,
            media_type="text/plain",
        )
    script = (
        _TEMPLATE.replace("__SERVER_URL__", link.server_url)
        .replace("__AGENT_TOKEN__", link.token)
        .replace("__BUNDLE_URL__", link.bundle_url)
    )
    return PlainTextResponse(script, media_type="text/x-shellscript")
