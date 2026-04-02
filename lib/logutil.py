"""Application logging: enable with APP_DEBUG=1 or LOG_LEVEL=DEBUG, optional file in app.yml."""
import logging
import os
import sys
from pathlib import Path
from typing import Optional


def setup_app_logging(
    *,
    debug_log_file: Optional[str] = None,
    default_level: str = "INFO",
) -> logging.Logger:
    """
    Root logger for the app (orcd.restic).
    - APP_DEBUG=1 or LOG_LEVEL=DEBUG -> DEBUG on stderr.
    - paths.debug_log_file in app.yml -> also append DEBUG+ to that file when debug enabled.
    """
    log = logging.getLogger("orcd.restic")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)

    env_level = (os.environ.get("LOG_LEVEL") or "").upper()
    if os.environ.get("APP_DEBUG") == "1" or env_level == "DEBUG":
        level = logging.DEBUG
    else:
        level = getattr(logging, default_level.upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(level)
    stderr.setFormatter(fmt)
    log.addHandler(stderr)

    if debug_log_file and (level == logging.DEBUG or os.environ.get("APP_DEBUG") == "1"):
        p = Path(debug_log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(p, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)

    log.propagate = False
    return log
