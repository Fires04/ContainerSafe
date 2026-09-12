"""Backup destinations. StorageBackend is the interface backup.py/restore.py
write/read archives through; concrete backends live in this package.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import StorageTarget


class StorageError(Exception):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def put(self, local_path: Path, remote_relpath: str) -> str:
        """Copy a local file to this backend, return a locator string
        recorded on the BackupRun row."""

    @abstractmethod
    def get(self, remote_relpath: str, local_dest: Path) -> None:
        """Fetch a previously-put file back to a local path (used by
        restore.py to pull an archive before extracting it)."""

    @abstractmethod
    def delete(self, remote_relpath: str) -> None:
        """Remove a previously-put file (used by retention pruning)."""


def build_backend(target: "StorageTarget") -> StorageBackend:
    """Single place backup.py and restore.py both go through to turn a
    StorageTarget row into a live backend instance — keeps target.type
    dispatch from being duplicated (and drifting) between the two."""
    if target.type == "local":
        from .local import LocalStorageBackend

        return LocalStorageBackend(target.config_json)
    if target.type == "rclone":
        from .rclone import RcloneStorageBackend

        return RcloneStorageBackend(target.config_json)
    raise StorageError(f"Unknown storage target type: {target.type!r}")
