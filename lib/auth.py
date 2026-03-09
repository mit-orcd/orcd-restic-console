"""File-based auth. Passwords in file are encrypted (or plain 'orcd' to force reset)."""
import hashlib
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

USERS_FILE_DEFAULT = "/db/restic/sec/users"
PLAINTEXT_FORCE_RESET = "orcd"


def _fernet_key(secret_key: str) -> bytes:
    """Derive a 32-byte Fernet key from the app secret key."""
    digest = hashlib.sha256(secret_key.encode()).digest()
    return urlsafe_b64encode(digest)


def encrypt_password(secret_key: str, password: str) -> str:
    """Encrypt password for storage in the users file."""
    f = Fernet(_fernet_key(secret_key))
    return f.encrypt(password.encode()).decode()


def decrypt_password(secret_key: str, encrypted: str) -> Optional[str]:
    """Decrypt stored password. Returns None if invalid/corrupt."""
    try:
        f = Fernet(_fernet_key(secret_key))
        return f.decrypt(encrypted.encode()).decode()
    except (InvalidToken, Exception):
        return None


def _parse_users_file(path: Path) -> list[Tuple[str, str, str]]:
    """Return list of (username, role, password)."""
    if not path.exists():
        return []
    users = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) >= 3:
            users.append((parts[0], parts[1], parts[2]))
        elif len(parts) == 2:
            users.append((parts[0], parts[1], ""))
    return users


def verify_user(
    users_path: str, username: str, password: str, secret_key: str
) -> Optional[str]:
    """
    Verify credentials.
    - Returns role if valid.
    - Returns 'must_reset' if user has plain 'orcd' and typed 'orcd' (must set new password).
    - Returns None otherwise.
    - If stored value is not 'orcd' and decrypt fails, tries plain-text match and migrates to encrypted on success.
    """
    path = Path(users_path)
    for u, role, stored in _parse_users_file(path):
        if u != username:
            continue
        if stored == PLAINTEXT_FORCE_RESET:
            if password == PLAINTEXT_FORCE_RESET:
                return "must_reset"
            return None
        decrypted = decrypt_password(secret_key, stored)
        if decrypted is not None and decrypted == password:
            return role
        # Migration: stored may be legacy plain text
        if stored == password:
            update_user_password(users_path, username, password, secret_key)
            return role
        return None
    return None


def update_user_password(
    users_path: str, username: str, new_password: str, secret_key: str
) -> bool:
    """Update the user's password in the file (stored encrypted). Returns True if updated."""
    path = Path(users_path)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    encrypted = encrypt_password(secret_key, new_password)
    new_lines = []
    found = False
    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            new_lines.append(line)
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] == username:
            new_lines.append(f"{parts[0]}\t{parts[1]}\t{encrypted}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + ("\n" if lines else ""), encoding="utf-8")
    return True


def get_all_roles(users_path: str) -> dict[str, str]:
    """Return {username: role} for all users (no passwords)."""
    path = Path(users_path)
    return {u: role for u, role, _ in _parse_users_file(path)}
