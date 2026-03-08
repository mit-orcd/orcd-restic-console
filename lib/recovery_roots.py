"""Recovery (restore-from) roots: configurable list of backup root dirs under /mnt with backup_ prefix."""
from pathlib import Path
from threading import Lock
from typing import List, Optional

from .utils import read_yaml, write_yaml

MNT = Path("/mnt")
BACKUP_PREFIX = "backup_"


def _allowed_base_dirs() -> List[Path]:
    """List dirs under /mnt that exist and have name starting with backup_."""
    if not MNT.exists() or not MNT.is_dir():
        return []
    out = []
    for p in MNT.iterdir():
        if p.is_dir() and p.name.startswith(BACKUP_PREFIX):
            out.append(p.resolve())
    return sorted(out)


def validate_backup_root(path_str: str) -> Path:
    """
    Validate that path is an allowed backup root: under /mnt, name starts with backup_, exists.
    Returns resolved Path or raises ValueError.
    """
    p = Path(path_str).resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("Path does not exist or is not a directory")
    try:
        p.relative_to(MNT.resolve())
    except ValueError:
        raise ValueError("Path must be under /mnt")
    # Allow /mnt/backup_foo or /mnt/backup_foo/anything (subdirs of backup_*)
    # So we require that the first component under /mnt starts with backup_
    parts = p.relative_to(MNT.resolve()).parts
    if not parts or not parts[0].startswith(BACKUP_PREFIX):
        raise ValueError("Path must be under a directory named with prefix backup_ (e.g. /mnt/backup_*)")
    return p


def list_allowed_roots() -> List[dict]:
    """List dirs that can be used as backup roots: under /mnt, name backup_*, exist.
    Includes each /mnt/backup_* and their immediate subdirs (e.g. /mnt/backup_software/software).
    """
    result = []
    seen = set()
    for base in _allowed_base_dirs():
        path_str = str(base)
        if path_str not in seen:
            seen.add(path_str)
            result.append({"path": path_str, "name": base.name})
        for sub in base.iterdir():
            if sub.is_dir():
                sp = str(sub.resolve())
                if sp not in seen:
                    seen.add(sp)
                    result.append({"path": sp, "name": f"{base.name}/{sub.name}"})
    return sorted(result, key=lambda x: x["path"])


class RecoveryRootsStore:
    """Load/save recovery roots from YAML. Keys are used in API (e.g. user_home); name is display name."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.lock = Lock()

    def load(self) -> List[dict]:
        """List of {key, name, path}."""
        with self.lock:
            data = read_yaml(self.config_path)
        roots = data.get("roots") or []
        return list(roots)

    def save(self, roots: List[dict]) -> None:
        with self.lock:
            write_yaml(self.config_path, {"roots": roots})

    def get_path_by_key(self, key: str) -> Optional[Path]:
        for r in self.load():
            if r.get("key") == key:
                return Path(r["path"]).resolve()
        return None

    def add(self, key: str, name: str, path: str) -> None:
        key = key.strip().lower().replace(" ", "_")
        if not key or not name.strip():
            raise ValueError("key and name are required")
        path = str(validate_backup_root(path))
        roots = self.load()
        if any(r.get("key") == key for r in roots):
            raise ValueError("A root with this key already exists")
        roots.append({"key": key, "name": name.strip(), "path": path})
        self.save(roots)

    def remove(self, key: str) -> None:
        roots = [r for r in self.load() if r.get("key") != key]
        if len(roots) == len(self.load()):
            raise KeyError("Recovery root not found")
        self.save(roots)
