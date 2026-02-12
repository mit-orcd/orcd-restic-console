from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from .utils import read_yaml, write_yaml


@dataclass
class AppConfig:
    host: str
    port: int
    secret_key: str
    max_jobs: int
    restic_binary: str
    restic_password_file: str
    restic_compression: str
    keep_daily: int
    keep_weekly: int
    log_dir: str
    job_store: str
    restore_root: str
    aws_binary: str
    aws_default_region: str
    aws_default_endpoint: str


class ConfigStore:
    def __init__(self, config_path: Path, app_config_path: Path) -> None:
        self.config_path = config_path
        self.app_config_path = app_config_path
        self.lock = Lock()

    def load_backups(self) -> Dict[str, List[dict]]:
        with self.lock:
            data = read_yaml(self.config_path)
        data.setdefault("destinations", [])
        data.setdefault("filesystems", [])
        return data

    def save_backups(self, data: dict) -> None:
        with self.lock:
            write_yaml(self.config_path, data)

    def load_app_config(self) -> AppConfig:
        raw = read_yaml(self.app_config_path)
        app = raw.get("app", {})
        restic = raw.get("restic", {})
        paths = raw.get("paths", {})
        aws = raw.get("aws", {})
        return AppConfig(
            host=app.get("host", "0.0.0.0"),
            port=int(app.get("port", 8080)),
            secret_key=app.get("secret_key", "change-me"),
            max_jobs=int(app.get("max_jobs", 4)),
            restic_binary=restic.get("binary", "/usr/local/bin/restic"),
            restic_password_file=restic.get("password_file", "/root/.backup_pass"),
            restic_compression=restic.get("compression", "auto"),
            keep_daily=int(restic.get("keep_daily", 14)),
            keep_weekly=int(restic.get("keep_weekly", 2)),
            log_dir=paths.get("log_dir", "./data/logs"),
            job_store=paths.get("job_store", "./data/job_history.json"),
            restore_root=paths.get("restore_root", "/mnt/restores"),
            aws_binary=aws.get("binary", "/usr/local/bin/aws"),
            aws_default_region=aws.get("default_region", "us-east-1"),
            aws_default_endpoint=aws.get("default_endpoint", "s3.amazonaws.com"),
        )

    @staticmethod
    def find_destination(destinations: List[dict], dest_id: str) -> Optional[dict]:
        return next((dest for dest in destinations if dest.get("id") == dest_id), None)

    @staticmethod
    def find_filesystem(filesystems: List[dict], fs_id: str) -> Optional[dict]:
        return next((fs for fs in filesystems if fs.get("id") == fs_id), None)
