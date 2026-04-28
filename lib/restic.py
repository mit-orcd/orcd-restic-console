import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("orcd.restic.restic")


@dataclass
class ResticConfig:
    binary: str
    password_file: str
    compression: str
    keep_daily: int
    keep_weekly: int


def run_command(
    args: List[str],
    log_path: Path,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd_preview = " ".join(args)
    _log.debug("restic cmd: %s", cmd_preview)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {cmd_preview}\n")
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout:
            handle.write(result.stdout)
        if result.stderr:
            handle.write(result.stderr)
        code = result.returncode
        if code == 0:
            _log.debug("restic ok exit=0 cmd=%s", cmd_preview[:200])
        else:
            err_snip = (result.stderr or result.stdout or "")[:1500]
            _log.warning("restic fail exit=%s cmd=%s stderr=%s", code, cmd_preview[:200], err_snip)
        return code, result.stdout, result.stderr


class ResticService:
    def __init__(self, cfg: ResticConfig) -> None:
        self.cfg = cfg

    def _base_args(self, repo: str) -> List[str]:
        return [
            self.cfg.binary,
            "--repo",
            repo,
            "--password-file",
            self.cfg.password_file,
        ]

    def snapshots(self, repo: str, log_path: Path) -> Tuple[int, str, str]:
        args = self._base_args(repo) + ["snapshots", "--json"]
        return run_command(args, log_path)

    def init_repo(self, repo: str, log_path: Path) -> Tuple[int, str, str]:
        args = self._base_args(repo) + ["init"]
        return run_command(args, log_path)

    def unlock(self, repo: str, log_path: Path) -> Tuple[int, str, str]:
        args = self._base_args(repo) + ["unlock"]
        return run_command(args, log_path)

    def forget_prune(self, repo: str, log_path: Path) -> Tuple[int, str, str]:
        args = self._base_args(repo) + [
            "forget",
            "--keep-daily",
            str(self.cfg.keep_daily),
            "--keep-weekly",
            str(self.cfg.keep_weekly),
            "--prune",
        ]
        return run_command(args, log_path)

    def backup(self, source: str, repo: str, tag: str, log_path: Path) -> Tuple[int, str, str]:
        args = self._base_args(repo) + [
            "backup",
            source,
            "--tag",
            tag,
            "--compression",
            self.cfg.compression,
            "--verbose",
            "--skip-if-unchanged",
        ]
        return run_command(args, log_path)

    def ls_args(self, repo: str, snapshot: str) -> List[str]:
        """Arguments for `restic … ls <snapshot>` (same as executed by `ls`)."""
        return self._base_args(repo) + ["ls", snapshot]

    def ls(self, repo: str, snapshot: str, log_path: Path) -> Tuple[int, str, str]:
        """List files in snapshot. Stdout is one path per line."""
        return run_command(self.ls_args(repo, snapshot), log_path)

    def restore(
        self,
        repo: str,
        snapshot: str,
        target_path: str,
        log_path: Path,
        include_paths: Optional[Sequence[str]] = None,
        exclude_paths: Optional[Sequence[str]] = None,
    ) -> Tuple[int, str, str]:
        args = self._base_args(repo) + [
            "restore",
            snapshot,
            "--target",
            target_path,
        ]
        if include_paths:
            for p in include_paths:
                args.extend(["--include", p])
        if exclude_paths:
            for p in exclude_paths:
                args.extend(["--exclude", p])
        return run_command(args, log_path)
