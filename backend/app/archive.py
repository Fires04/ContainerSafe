"""Low-level archive mechanics: talking to helper containers to tar/untar
mount data via the Docker API. Orchestration (which job, which container,
DB bookkeeping, retention) lives in backup.py/restore.py — this module
only knows about tar and staging paths.
"""

import re
from pathlib import Path

import docker


class ArchiveError(Exception):
    pass


def sanitize(name: str) -> str:
    """Turn a mount name or host path into a safe archive member /
    filename fragment. Not required to be reversible — the original
    value is always kept alongside it in config.json's data manifest."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip("/"))
    return cleaned or "root"


def tar_source_to_staging(
    client: docker.DockerClient,
    source_host_path: str,
    staging_dir_host: str,
    member_name: str,
    staging_dir_local: Path,
) -> Path:
    """Runs a short-lived alpine+tar helper container (via the Docker
    API) that mounts `source_host_path` — a HOST path, exactly as the
    daemon resolves it, see docker_endpoint.py's host_path_for_data() —
    read-only, plus a staging directory read-write, then tars the former
    into `{staging_dir_host}/{member_name}.tar.gz`. Works identically for
    both named-volume and bind mounts: Docker's own container inspect
    already resolves a volume mount's "Source" to its real host
    mountpoint, so the caller never needs to special-case the two.
    Returns the LOCAL path (as seen by this app's own container) to the
    resulting file.
    """
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
    """Mirror of tar_source_to_staging for restore.py (M5): extracts
    `{archive_dir_host}/{archive_filename}` into `target_host_path`."""
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
