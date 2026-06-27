import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("orcd.restic.restic")


@dataclass
class ResticConfig:
    binary: str
    password_file: str
    compression: str
    keep_daily: int
    keep_weekly: int
    cache_dir: Optional[str] = None


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


def run_command_stream(
    args: List[str],
    log_path: Path,
    line_handler: Callable[[str], bool],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    """Run a command, streaming stdout to line_handler one line at a time.

    Avoids holding the whole stdout in memory (the cause of multi-GB spikes on
    `restic ls` of large repos). line_handler returns False to stop early, in which
    case the child process is terminated. stderr is small for ls (errors/locks only)
    and is collected after stdout EOF. Returns (exit_code, stderr).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd_preview = " ".join(args)
    _log.debug("restic stream cmd: %s", cmd_preview)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {cmd_preview}\n")
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    stopped_early = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if not line_handler(line):
                stopped_early = True
                break
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if stopped_early and proc.poll() is None:
            proc.terminate()
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        if proc.stderr is not None:
            proc.stderr.close()
        code = proc.wait()
    if stderr:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(stderr)
        except OSError:
            pass
    if stopped_early:
        # Early stop (e.g. hit max_paths) is an intentional success, not a restic failure.
        code = 0
    if code != 0:
        _log.warning("restic stream fail exit=%s cmd=%s stderr=%s", code, cmd_preview[:200], (stderr or "")[:1500])
    return code, stderr


class ResticService:
    def __init__(self, cfg: ResticConfig) -> None:
        self.cfg = cfg

    def _base_args(self, repo: str) -> List[str]:
        args = [
            self.cfg.binary,
            "--repo",
            repo,
            "--password-file",
            self.cfg.password_file,
        ]
        # Global flag; must precede the subcommand. Pins the metadata cache to a persistent,
        # fast location instead of relying on the service user's ambient $HOME/.cache.
        if self.cfg.cache_dir:
            args += ["--cache-dir", self.cfg.cache_dir]
        return args

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

    def ls(self, repo: str, snapshot: str, log_path: Path) -> Tuple[int, str, str]:
        """List files in snapshot. Stdout is one path per line."""
        args = self._base_args(repo) + ["ls", snapshot]
        return run_command(args, log_path)

    def ls_paths(
        self,
        repo: str,
        snapshot: str,
        log_path: Path,
        max_paths: Optional[int] = None,
    ) -> Tuple[int, List[str], str, bool]:
        """List a snapshot's file paths, streaming restic's stdout line-by-line.

        Unlike ls(), this never buffers the entire (potentially multi-GB) output as a
        single string: it filters to real paths on the fly and can stop early at
        max_paths. Returns (exit_code, paths, stderr, truncated).
        """
        args = self._base_args(repo) + ["ls", snapshot]
        paths: List[str] = []
        state = {"truncated": False}

        def handle(line: str) -> bool:
            s = line.rstrip("\n")
            # restic ls prints one absolute path per line; skip the occasional header/blank.
            if not s or not s.startswith("/"):
                return True
            if max_paths is not None and len(paths) >= max_paths:
                state["truncated"] = True
                return False  # signal the runner to stop early
            paths.append(s)
            return True

        code, stderr = run_command_stream(args, log_path, handle)
        summary = f"# ls_paths: {len(paths)} path(s)" + (" (truncated)" if state["truncated"] else "")
        try:
            with log_path.open("a", encoding="utf-8") as handle_f:
                handle_f.write(summary + "\n")
        except OSError:
            pass
        return code, paths, stderr, state["truncated"]

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
