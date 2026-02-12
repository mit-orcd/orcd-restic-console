import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict

from .utils import read_json, write_json


class JobManager:
    def __init__(self, store_path: Path, max_workers: int) -> None:
        self.store_path = store_path
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.jobs: Dict[str, dict] = self._load_jobs()

    def _load_jobs(self) -> Dict[str, dict]:
        raw = read_json(self.store_path)
        return raw.get("jobs", {}) if raw else {}

    def _save(self) -> None:
        write_json(self.store_path, {"jobs": self.jobs})

    def list_jobs(self) -> Dict[str, dict]:
        with self.lock:
            return dict(self.jobs)

    def submit(
        self,
        job_type: str,
        fs_id: str,
        log_path: Path,
        handler: Callable[[], Dict[str, Any]],
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "type": job_type,
            "filesystem_id": fs_id,
            "status": "queued",
            "created_at": int(time.time()),
            "started_at": None,
            "finished_at": None,
            "log_path": str(log_path),
            "result": None,
            "error": None,
        }
        with self.lock:
            self.jobs[job_id] = job
            self._save()

        def _run() -> None:
            with self.lock:
                self.jobs[job_id]["status"] = "running"
                self.jobs[job_id]["started_at"] = int(time.time())
                self._save()
            try:
                result = handler()
                with self.lock:
                    self.jobs[job_id]["status"] = "completed"
                    self.jobs[job_id]["result"] = result
            except Exception as exc:  # noqa: BLE001 - surface errors in job status
                with self.lock:
                    self.jobs[job_id]["status"] = "failed"
                    self.jobs[job_id]["error"] = str(exc)
            finally:
                with self.lock:
                    self.jobs[job_id]["finished_at"] = int(time.time())
                    self._save()

        self.executor.submit(_run)
        return job_id
