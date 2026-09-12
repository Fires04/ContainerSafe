"""Low-level archive mechanics — an agent-side copy of
backend/app/archive.py (same helper-container tar/untar mechanism,
duplicated rather than shared, see discovery.py's module docstring for
why). `untar_staging_to_target` isn't used by M-Agent-1 (backup only) but
is included now since M-Agent-2 (agent-side restore) will need the exact
same mechanism.
"""

import re
from pathlib import Path

import docker


class ArchiveError(Exception):
    pass


def sanitize(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip("/"))
    return cleaned or "root"


def tar_source_to_staging(
    client: docker.DockerClient,
    source_host_path: str,
    staging_dir_host: str,
    member_name: str,
    staging_dir_local: Path,
) -> Path:
    container = client.containers.run(
        image="alpine:3.20",
        command=["tar", "-czf", f"/staging/{member_name}.tar.gz", "-C", "/src", "."],
        volumes={
            source_host_path: {"bind": "/src", "mode": "ro"},
            staging_dir_host: {"bind": "/staging", "mode": "rw"},
        },
        detach=True,
    )
    try:
        result = container.wait()
        logs = container.logs()
    finally:
        container.remove(force=True)
    if result.get("StatusCode") != 0:
        raise ArchiveError(f"tar failed for {member_name}: {logs.decode(errors='replace')}")
    return staging_dir_local / f"{member_name}.tar.gz"


def untar_staging_to_target(
    client: docker.DockerClient,
    archive_dir_host: str,
    archive_filename: str,
    target_host_path: str,
) -> None:
    container = client.containers.run(
        image="alpine:3.20",
        command=["tar", "-xzf", f"/archive/{archive_filename}", "-C", "/dst"],
        volumes={
            archive_dir_host: {"bind": "/archive", "mode": "ro"},
            target_host_path: {"bind": "/dst", "mode": "rw"},
        },
        detach=True,
    )
    try:
        result = container.wait()
        logs = container.logs()
    finally:
        container.remove(force=True)
    if result.get("StatusCode") != 0:
        raise ArchiveError(f"untar failed: {logs.decode(errors='replace')}")
