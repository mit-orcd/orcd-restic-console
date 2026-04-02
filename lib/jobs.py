import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .utils import read_json, write_json

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


class JobManager:
    """
    Job state is persisted to job_store JSON so all Gunicorn workers share the same view.
    A sibling .lock file is used with flock(2) for cross-process consistency.
    """

    def __init__(self, store_path: Path, max_workers: int) -> None:
        self.store_path = Path(store_path).resolve()
        self.lock_path = self.store_path.parent / (self.store_path.name + ".lock")
        self.process_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def _load_jobs(self) -> Dict[str, dict]:
        raw = read_json(self.store_path)
        return raw.get("jobs", {}) if raw else {}

    def _save_jobs(self, jobs: Dict[str, dict]) -> None:
        write_json(self.store_path, {"jobs": jobs})

    @contextmanager
    def _flock(self, exclusive: bool) -> Any:
        """Cross-process lock around job store reads/writes (Linux/macOS)."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not _HAS_FCNTL:
            self.process_lock.acquire()
            try:
                yield
            finally:
                self.process_lock.release()
            return
        with open(self.lock_path, "a+", encoding="utf-8") as lf:
            op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lf.fileno(), op)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def list_jobs(self) -> Dict[str, dict]:
        with self._flock(exclusive=False):
            return dict(self._load_jobs())

    def _update_job(self, job_id: str, mutator: Callable[[dict], None]) -> None:
        with self._flock(exclusive=True):
            jobs = self._load_jobs()
            if job_id not in jobs:
                return
            mutator(jobs[job_id])
            self._save_jobs(jobs)

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
        with self._flock(exclusive=True):
            jobs = self._load_jobs()
            jobs[job_id] = job
            self._save_jobs(jobs)

        def _run() -> None:
            self._update_job(
                job_id,
                lambda j: j.update({"status": "running", "started_at": int(time.time())}),
            )
            try:
                result = handler()
                self._update_job(
                    job_id,
                    lambda j: j.update(
                        {
                            "status": "completed",
                            "result": result,
                            "finished_at": int(time.time()),
                        }
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._update_job(
                    job_id,
                    lambda j: j.update(
                        {
                            "status": "failed",
                            "error": str(exc),
                            "finished_at": int(time.time()),
                        }
                    ),
                )

        self.executor.submit(_run)
        return job_id
