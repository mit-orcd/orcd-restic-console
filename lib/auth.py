"""Simple file-based auth. Users file: username, role, password (space or tab separated)."""
from pathlib import Path
from typing import Optional, Tuple

USERS_FILE_DEFAULT = "/db/restic/sec/users"


def _parse_users_file(path: Path) -> list[Tuple[str, str, str]]:
    """Return list of (username, role, password)."""
    if not path.exists():
        return []
    users = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)  # max 3 parts (third is password, may contain spaces)
        if len(parts) >= 3:
            users.append((parts[0], parts[1], parts[2]))
        elif len(parts) == 2:
            users.append((parts[0], parts[1], ""))
    return users


def verify_user(users_path: str, username: str, password: str) -> Optional[str]:
    """
    Verify credentials. Returns role if valid, None otherwise.
    Password in file is compared as plain text (can be extended to support hashes later).
    """
    path = Path(users_path)
    for u, role, stored in _parse_users_file(path):
        if u == username and stored == password:
            return role
    return None


def get_all_roles(users_path: str) -> dict[str, str]:
    """Return {username: role} for all users (no passwords)."""
    path = Path(users_path)
    return {u: role for u, role, _ in _parse_users_file(path)}
