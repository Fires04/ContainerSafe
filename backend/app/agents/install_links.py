"""One-time install links — same purpose as FiresLog's own
agents/install_links.py: let an operator run a single `curl | bash`
command on a fresh host instead of hand-copying a token into a `.env`
file. In-memory only (not DB-backed) since a link is short-lived and
this app is single-process — same choice FiresLog made for its own link
store.

The bearer token itself only ever appears embedded in the ONE script
response this link serves (see routers/agent_install.py) — the code in
the URL is a meaningless, already-consumed value by the time anyone
could see it in shell history; the token never appears there at all.
"""

import secrets
import time
from dataclasses import dataclass

LINK_TTL_SECONDS = 900  # 15 minutes


@dataclass
class InstallLink:
    agent_id: int
    token: str
    server_url: str
    bundle_url: str


class InstallLinkStore:
    def __init__(self) -> None:
        self._links: dict[str, tuple[InstallLink, float]] = {}  # code -> (link, expires_at)

    def create(self, agent_id: int, token: str, server_url: str, bundle_url: str) -> str:
        self._cleanup_expired()
        code = secrets.token_urlsafe(24)
        self._links[code] = (
            InstallLink(agent_id=agent_id, token=token, server_url=server_url, bundle_url=bundle_url),
            time.monotonic() + LINK_TTL_SECONDS,
        )
        return code

    def consume(self, code: str) -> InstallLink | None:
        """Single-use: pops the link so a retried/replayed GET of the
        same URL fails cleanly rather than re-serving the token forever."""
        self._cleanup_expired()
        entry = self._links.pop(code, None)
        if entry is None:
            return None
        link, expires_at = entry
        if expires_at < time.monotonic():
            return None
        return link

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [c for c, (_, exp) in self._links.items() if exp < now]
        for c in expired:
            del self._links[c]


_store = InstallLinkStore()


def get_install_link_store() -> InstallLinkStore:
    return _store
