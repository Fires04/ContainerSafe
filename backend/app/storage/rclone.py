"""rclone-backed StorageBackend for SFTP/SMB/S3/WebDAV-class remote
targets. Uses rclone's on-the-fly connection-string syntax
(":type,param=value,...:/path") via subprocess — no rclone config file
on disk, so a target's connection details live only in this app's own DB
(with the password obscured, see obscure_password() and targets.py).
"""

import subprocess
from pathlib import Path

from . import StorageBackend, StorageError


def obscure_password(plaintext: str) -> str:
    """Wraps `rclone obscure` — rclone's own reversible-but-not-plaintext
    encoding for passwords embedded in a connection string. Not real
    encryption (rclone documents this), just enough that a plaintext
    password is never the thing persisted to the DB or shown back in the UI.
    """
    result = subprocess.run(
        ["rclone", "obscure", plaintext],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


# Which of a target's config fields become which rclone connection-string
# parameter, per remote type. "host" et al. are this app's own generic
# field names (see targets.py's UI form); rclone's own parameter names
# differ per backend, so this is the one place that mapping lives.
_PARAM_MAP: dict[str, dict[str, str]] = {
    "sftp": {"host": "host", "port": "port", "user": "user", "pass_obscured": "pass"},
    "smb": {"host": "host", "port": "port", "user": "user", "pass_obscured": "pass", "domain": "domain"},
    "webdav": {"host": "url", "user": "user", "pass_obscured": "pass", "vendor": "vendor"},
    "s3": {
        "host": "endpoint",
        "user": "access_key_id",
        "pass_obscured": "secret_access_key",
        "provider": "provider",
        "region": "region",
    },
    # FTPS isn't a separate rclone backend — it's the same "ftp" backend
    # with explicit_tls (AUTH TLS / STARTTLS) turned on, so the UI offers
    # it as one "ftp" target type plus a checkbox rather than a second type.
    "ftp": {"host": "host", "port": "port", "user": "user", "pass_obscured": "pass", "explicit_tls": "explicit_tls"},
}


class RcloneStorageBackend(StorageBackend):
    def __init__(self, target_config: dict):
        self.remote_type = target_config.get("remote_type")
        self.path = (target_config.get("path") or "").strip("/")
        if self.remote_type not in _PARAM_MAP:
            raise StorageError(f"Unsupported rclone remote_type: {self.remote_type!r}")

        field_map = _PARAM_MAP[self.remote_type]
        self._params = {}
        for our_name, rclone_name in field_map.items():
            value = target_config.get(our_name)
            if not value:
                continue
            # rclone's config parser expects lowercase true/false, not
            # Python's str(True) == "True".
            self._params[rclone_name] = "true" if value is True else ("false" if value is False else value)

    @staticmethod
    def _quote(value: str) -> str:
        """rclone's on-the-fly connection-string values must be quoted
        whenever they contain ":" or "," (both are syntactically
        significant there) — e.g. a WebDAV url= like "http://host:8080".
        Quote unconditionally rather than trying to guess: it's always
        valid, per rclone's own value-quoting rules (single-quote wrap,
        embedded quotes doubled)."""
        return "'" + str(value).replace("'", "''") + "'"

    def _remote(self, extra_path: str = "") -> str:
        param_str = ",".join(f"{k}={self._quote(v)}" for k, v in self._params.items())
        full_path = f"{self.path}/{extra_path}".strip("/") if extra_path else self.path
        return f":{self.remote_type},{param_str}:/{full_path}"

    def _run(self, *args: str) -> str:
        result = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise StorageError(f"rclone {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def put(self, local_path: Path, remote_relpath: str) -> str:
        self._run("copyto", str(local_path), self._remote(remote_relpath))
        return remote_relpath

    def get(self, remote_relpath: str, local_dest: Path) -> None:
        local_dest.parent.mkdir(parents=True, exist_ok=True)
        self._run("copyto", self._remote(remote_relpath), str(local_dest))

    def delete(self, remote_relpath: str) -> None:
        self._run("deletefile", self._remote(remote_relpath))
