"""Abstraction over "a Docker daemon this app can talk to."

MVP has exactly one implementation, LocalSocketEndpoint, wrapping the
mounted /var/run/docker.sock. Every module that needs a docker-py client
takes a DockerEndpoint, never a bare docker.DockerClient or socket path —
one seam a future multi-host/agent model plugs into. Same pattern as
FiresLog's LogProvider ABC.

Note this ABC is necessary but not sufficient for that future model on
its own — see docs/MULTI_HOST_PLAN.md for the full design (why a remote
host also needs its archive *assembly* step, not just Docker API calls,
to happen agent-side rather than through a remote client here).
"""

from abc import ABC, abstractmethod
from pathlib import Path

import docker

from . import config


class DockerEndpoint(ABC):
    host_id: str

    @abstractmethod
    def client(self) -> docker.DockerClient:
        """A docker-py client connected to this endpoint's daemon."""

    @abstractmethod
    def host_path_for_data(self, relative: str) -> str:
        """Translate a path relative to this app's own ./data directory
        into the HOST-absolute path the daemon behind this endpoint would
        need in order to bind-mount it into a helper container. Required
        because helper containers are created via the Docker API and
        started by the daemon, which resolves bind-mount sources against
        its own host filesystem — not against this app container's.
        """


class LocalSocketEndpoint(DockerEndpoint):
    """The only endpoint in the MVP: the Docker daemon reachable over the
    socket mounted into this app's own container."""

    host_id = "local"

    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None

    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def host_path_for_data(self, relative: str) -> str:
        return str(Path(config.HOST_DATA_DIR) / relative)


_ENDPOINTS: dict[str, DockerEndpoint] = {"local": LocalSocketEndpoint()}


def get_endpoint(host_id: str = "local") -> DockerEndpoint:
    try:
        return _ENDPOINTS[host_id]
    except KeyError:
        raise ValueError(f"Unknown host_id: {host_id!r}") from None


def all_endpoints() -> list[DockerEndpoint]:
    return list(_ENDPOINTS.values())
