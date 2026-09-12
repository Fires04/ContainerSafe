import shutil
from pathlib import Path

from .. import config
from . import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Writes under config.BACKUP_DIR (the ./backup bind mount). Also
    covers NFS: mount the NFS share at the compose/host level under
    ./backup and point a target's "path" at that subdirectory — no
    NFS-specific code needed.
    """

    def __init__(self, target_config: dict):
        subpath = (target_config.get("path") or "").strip("/")
        self.root = (config.BACKUP_DIR / subpath) if subpath else config.BACKUP_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, local_path: Path, remote_relpath: str) -> str:
        dest = self.root / remote_relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return remote_relpath  # the locator is backend-relative, not an absolute host path

    def get(self, remote_relpath: str, local_dest: Path) -> None:
        local_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.root / remote_relpath, local_dest)

    def delete(self, remote_relpath: str) -> None:
        (self.root / remote_relpath).unlink(missing_ok=True)
