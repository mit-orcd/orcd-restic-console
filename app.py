import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, abort, jsonify, render_template, request

from lib.config import AppConfig, ConfigStore
from lib.jobs import JobManager
from lib.restic import ResticConfig, ResticService, run_command

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "backups.yml"
APP_CONFIG_PATH = BASE_DIR / "config" / "app.yml"

store = ConfigStore(CONFIG_PATH, APP_CONFIG_PATH)
app_config = store.load_app_config()
app = Flask(__name__)
app.secret_key = app_config.secret_key

job_manager = JobManager(Path(app_config.job_store), app_config.max_jobs)
restic_service = ResticService(
    ResticConfig(
        binary=app_config.restic_binary,
        password_file=app_config.restic_password_file,
        compression=app_config.restic_compression,
        keep_daily=app_config.keep_daily,
        keep_weekly=app_config.keep_weekly,
    )
)


def _log_path(fs_id: str, action: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(app_config.log_dir) / f"{fs_id}-{action}-{stamp}.log"


def _resolve_repo(fs_entry: dict, dest_entry: dict) -> str:
    if fs_entry.get("repo"):
        return fs_entry["repo"]
    repo_suffix = fs_entry.get("repo_suffix") or fs_entry.get("name") or fs_entry.get("id")
    if dest_entry["type"] == "local":
        base = dest_entry["path"].rstrip("/")
        return f"{base}/{repo_suffix}"
    if dest_entry["type"] == "s3":
        endpoint = dest_entry.get("endpoint") or app_config.aws_default_endpoint
        prefix = dest_entry.get("prefix", "").strip("/")
        path = f"{prefix}/{repo_suffix}" if prefix else repo_suffix
        return f"s3:{endpoint}/{dest_entry['bucket']}/{path}"
    raise ValueError(f"Unsupported destination type: {dest_entry['type']}")


def _require_restore_root(target_path: str) -> str:
    restore_root = Path(app_config.restore_root).resolve()
    target = Path(target_path).resolve()
    if restore_root not in target.parents and target != restore_root:
        raise ValueError("Restore target must be inside restore_root")
    return str(target)


def _get_filesystem(fs_id: str) -> dict:
    data = store.load_backups()
    fs_entry = store.find_filesystem(data["filesystems"], fs_id)
    if not fs_entry:
        abort(404, f"filesystem {fs_id} not found")
    return fs_entry


def _get_destination(dest_id: str) -> dict:
    data = store.load_backups()
    dest_entry = store.find_destination(data["destinations"], dest_id)
    if not dest_entry:
        abort(404, f"destination {dest_id} not found")
    return dest_entry


def _job_backup(fs_id: str, fs_entry: dict, dest_entry: dict) -> Dict[str, Any]:
    repo = _resolve_repo(fs_entry, dest_entry)
    log_path = _log_path(fs_id, "backup")

    code, _, _ = restic_service.snapshots(repo, log_path)
    if code != 0:
        init_code, _, _ = restic_service.init_repo(repo, log_path)
        if init_code != 0:
            raise RuntimeError("restic init failed")

    restic_service.unlock(repo, log_path)
    restic_service.forget_prune(repo, log_path)

    tag = f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
    code, _, _ = restic_service.backup(fs_entry["source_path"], repo, tag, log_path)
    if code != 0:
        raise RuntimeError("restic backup failed")

    return {"repo": repo, "tag": tag}


def _job_restore(fs_id: str, fs_entry: dict, dest_entry: dict, snapshot_id: str, target_path: str) -> Dict[str, Any]:
    repo = _resolve_repo(fs_entry, dest_entry)
    log_path = _log_path(fs_id, "restore")
    target_path = _require_restore_root(target_path)

    restic_service.unlock(repo, log_path)
    code, _, _ = restic_service.restore(repo, snapshot_id, target_path, log_path)
    if code != 0:
        raise RuntimeError("restic restore failed")

    return {"repo": repo, "snapshot": snapshot_id, "target": target_path}


@app.route("/")
def index() -> str:
    data = store.load_backups()
    jobs = job_manager.list_jobs()
    return render_template(
        "index.html",
        filesystems=data["filesystems"],
        destinations=data["destinations"],
        jobs=sorted(jobs.values(), key=lambda item: item["created_at"], reverse=True)[:25],
        restore_root=app_config.restore_root,
    )


@app.route("/api/filesystems", methods=["GET"])
def list_filesystems() -> Any:
    data = store.load_backups()
    return jsonify(data["filesystems"])


@app.route("/api/filesystems", methods=["POST"])
def add_filesystem() -> Any:
    payload = request.get_json(force=True)
    required = ["id", "name", "source_path", "destination_id"]
    for field in required:
        if not payload.get(field):
            abort(400, f"missing field {field}")
    data = store.load_backups()
    if store.find_filesystem(data["filesystems"], payload["id"]):
        abort(409, "filesystem id already exists")
    data["filesystems"].append(
        {
            "id": payload["id"],
            "name": payload["name"],
            "source_path": payload["source_path"],
            "destination_id": payload["destination_id"],
            "repo_suffix": payload.get("repo_suffix") or payload["id"],
        }
    )
    store.save_backups(data)
    return jsonify({"status": "ok"})


@app.route("/api/filesystems/<fs_id>", methods=["DELETE"])
def remove_filesystem(fs_id: str) -> Any:
    data = store.load_backups()
    fs_entry = store.find_filesystem(data["filesystems"], fs_id)
    if not fs_entry:
        abort(404, "filesystem not found")
    data["filesystems"] = [fs for fs in data["filesystems"] if fs.get("id") != fs_id]
    store.save_backups(data)
    return jsonify({"status": "ok"})


@app.route("/api/destinations", methods=["GET"])
def list_destinations() -> Any:
    data = store.load_backups()
    return jsonify(data["destinations"])


@app.route("/api/destinations", methods=["POST"])
def add_destination() -> Any:
    payload = request.get_json(force=True)
    required = ["id", "name", "type"]
    for field in required:
        if not payload.get(field):
            abort(400, f"missing field {field}")
    if payload["type"] not in ("local", "s3"):
        abort(400, "type must be local or s3")
    data = store.load_backups()
    if store.find_destination(data["destinations"], payload["id"]):
        abort(409, "destination id already exists")
    if payload["type"] == "local" and not payload.get("path"):
        abort(400, "local destination requires path")
    if payload["type"] == "s3" and not payload.get("bucket"):
        abort(400, "s3 destination requires bucket")
    data["destinations"].append(payload)
    store.save_backups(data)
    return jsonify({"status": "ok"})


@app.route("/api/destinations/<dest_id>", methods=["DELETE"])
def remove_destination(dest_id: str) -> Any:
    data = store.load_backups()
    dest_entry = store.find_destination(data["destinations"], dest_id)
    if not dest_entry:
        abort(404, "destination not found")
    data["destinations"] = [dest for dest in data["destinations"] if dest.get("id") != dest_id]
    store.save_backups(data)
    return jsonify({"status": "ok"})


@app.route("/api/snapshots/<fs_id>", methods=["GET"])
def list_snapshots(fs_id: str) -> Any:
    fs_entry = _get_filesystem(fs_id)
    dest_entry = _get_destination(fs_entry["destination_id"])
    repo = _resolve_repo(fs_entry, dest_entry)
    log_path = _log_path(fs_id, "snapshots")
    code, stdout, _ = restic_service.snapshots(repo, log_path)
    if code != 0:
        abort(500, "failed to load snapshots")
    return jsonify(json.loads(stdout))


@app.route("/api/backup/<fs_id>", methods=["POST"])
def run_backup(fs_id: str) -> Any:
    fs_entry = _get_filesystem(fs_id)
    dest_entry = _get_destination(fs_entry["destination_id"])

    job_id = job_manager.submit(
        "backup",
        fs_id,
        _log_path(fs_id, "backup"),
        lambda: _job_backup(fs_id, fs_entry, dest_entry),
    )
    return jsonify({"job_id": job_id})


@app.route("/api/restore/<fs_id>", methods=["POST"])
def run_restore(fs_id: str) -> Any:
    payload = request.get_json(force=True)
    snapshot_id = payload.get("snapshot_id")
    target_path = payload.get("target_path")
    if not snapshot_id or not target_path:
        abort(400, "snapshot_id and target_path are required")
    fs_entry = _get_filesystem(fs_id)
    dest_entry = _get_destination(fs_entry["destination_id"])

    job_id = job_manager.submit(
        "restore",
        fs_id,
        _log_path(fs_id, "restore"),
        lambda: _job_restore(fs_id, fs_entry, dest_entry, snapshot_id, target_path),
    )
    return jsonify({"job_id": job_id})


@app.route("/api/jobs", methods=["GET"])
def list_jobs() -> Any:
    return jsonify(job_manager.list_jobs())


@app.route("/api/s3/buckets", methods=["POST"])
def create_s3_bucket() -> Any:
    payload = request.get_json(force=True)
    name = payload.get("name")
    region = payload.get("region") or app_config.aws_default_region
    endpoint = payload.get("endpoint") or app_config.aws_default_endpoint
    profile = payload.get("profile")
    if not name:
        abort(400, "name is required")

    log_path = _log_path("s3", "bucket")
    args = [app_config.aws_binary, "s3api", "create-bucket", "--bucket", name, "--region", region]
    if endpoint:
        args.extend(["--endpoint-url", f"https://{endpoint}"])
    if profile:
        args.extend(["--profile", profile])
    code, _, _ = run_command(args, log_path)
    if code != 0:
        abort(500, "aws create-bucket failed")
    return jsonify({"status": "ok", "bucket": name})


if __name__ == "__main__":
    os.makedirs(app_config.log_dir, exist_ok=True)
    debug = os.environ.get("APP_DEBUG") == "1"
    app.run(host=app_config.host, port=app_config.port, debug=debug)
