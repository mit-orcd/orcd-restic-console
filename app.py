import json
import os
import shlex
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from lib.auth import get_all_roles, update_user_password, verify_user
from lib.config import AppConfig, ConfigStore
from lib.jobs import JobManager
from lib.logutil import setup_app_logging
from lib.recovery_roots import RecoveryRootsStore, list_allowed_roots
from lib.restic import ResticConfig, ResticService, run_command

BASE_DIR = Path(__file__).resolve().parent


def _resolve_app_path(path_str: str) -> Path:
    """Resolve config paths relative to app root so Gunicorn cwd does not break relative paths."""
    p = Path(path_str)
    return p.resolve() if p.is_absolute() else (BASE_DIR / p).resolve()
CONFIG_PATH = BASE_DIR / "config" / "backups.yml"
APP_CONFIG_PATH = BASE_DIR / "config" / "app.yml"
RECOVERY_ROOTS_PATH = BASE_DIR / "config" / "recovery_roots.yml"

store = ConfigStore(CONFIG_PATH, APP_CONFIG_PATH)
app_config = store.load_app_config()
recovery_roots_store = RecoveryRootsStore(RECOVERY_ROOTS_PATH)
log = setup_app_logging(debug_log_file=app_config.debug_log_file)
app = Flask(__name__)
app.secret_key = app_config.secret_key

# Behind nginx/Apache reverse proxy: set ORCD_BEHIND_PROXY=1 so redirects and url_for use correct scheme/host
if os.environ.get("ORCD_BEHIND_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)


def _require_auth():
    """Redirect to login if not authenticated."""
    ep = request.endpoint
    if not ep or ep == "static":
        return
    if ep in ("login", "logout"):
        return
    if session.get("user") is None:
        return redirect(url_for("login", next=request.url))


def _require_admin():
    """Abort 403 if not admin."""
    if session.get("role") != "admin":
        abort(403, "Admin role required")


@app.before_request
def before_request():
    _require_auth()


def _safe_next_url(form_key: str = "next") -> str:
    next_url = (request.form.get(form_key) or request.args.get("next") or "").strip() or "/"
    if next_url.startswith("//") or (next_url.startswith("/") and "://" in next_url):
        return "/"
    return next_url


def _login_render(**kwargs: Any) -> str:
    """Always pass full context for login.html (avoids template edge cases)."""
    ctx = {
        "next_url": "/",
        "error": None,
        "show_reset": False,
        "reset_username": "",
    }
    ctx.update(kwargs)
    return render_template("login.html", **ctx)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        next_url = request.args.get("next", "/")
        show_reset = bool(request.args.get("reset") and session.get("pending_reset"))
        return _login_render(
            next_url=next_url,
            show_reset=show_reset,
            reset_username=session.get("pending_reset", ""),
        )
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username:
        return _login_render(error="Username required", next_url=_safe_next_url())
    role = verify_user(app_config.users_file, username, password, app_config.secret_key)
    if role is None:
        return _login_render(error="Invalid username or password", next_url=_safe_next_url())
    if role == "must_reset":
        session["pending_reset"] = username
        return redirect(url_for("login", reset=1, next=request.form.get("next", "/")))
    # Only two roles are defined: admin and user; any other is treated as user
    session["user"] = username
    session["role"] = "admin" if role == "admin" else "user"
    return redirect(_safe_next_url())


@app.route("/reset-password", methods=["POST"])
def reset_password():
    if not session.get("pending_reset"):
        return _login_render(error="Reset not requested", next_url="/")
    username = (request.form.get("username") or "").strip()
    if username != session.get("pending_reset"):
        return _login_render(
            error="Invalid session",
            next_url="/",
            show_reset=True,
            reset_username=session.get("pending_reset", ""),
        )
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""
    if not new_password or len(new_password) < 1:
        return _login_render(
            error="New password is required",
            next_url=_safe_next_url(),
            show_reset=True,
            reset_username=username,
        )
    if new_password != confirm:
        return _login_render(
            error="New password and confirmation do not match",
            next_url=_safe_next_url(),
            show_reset=True,
            reset_username=username,
        )
    if not update_user_password(app_config.users_file, username, new_password, app_config.secret_key):
        return _login_render(
            error="Failed to update password",
            next_url="/",
            show_reset=True,
            reset_username=username,
        )
    session.pop("pending_reset", None)
    session["user"] = username
    role = get_all_roles(app_config.users_file).get(username, "user")
    session["role"] = "admin" if role == "admin" else "user"
    return redirect(_safe_next_url())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def inject_user():
    return {
        "current_user": session.get("user"),
        "is_admin": session.get("role") == "admin",
    }


job_manager = JobManager(_resolve_app_path(app_config.job_store), app_config.max_jobs)
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
    return _resolve_app_path(app_config.log_dir) / f"{fs_id}-{action}-{stamp}.log"


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


def _allow_restore_target(target_path: str) -> str:
    """Allow target under restore_root or under default_restore_target (/tmp)."""
    target = Path(target_path).resolve()
    allowed = [
        Path(app_config.restore_root).resolve(),
        Path(app_config.default_restore_target).resolve(),
    ]
    for root in allowed:
        if root in target.parents or target == root:
            return str(target)
    raise ValueError("Restore target must be inside restore_root or default_restore_target")


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


def _recovery_repo_root(root_key: str) -> Path:
    p = recovery_roots_store.get_path_by_key(root_key)
    if p is not None:
        return p
    # Legacy fallback from app.yml
    if root_key == "user_home":
        return Path(app_config.backup_user_home)
    if root_key == "software":
        return Path(app_config.backup_software)
    abort(400, "unknown recovery root")


def _restore_log_path() -> Path:
    return Path(app_config.restore_log)


def _append_restore_log(
    repo: str,
    snapshot_id: str,
    target_path: str,
    include_paths: list,
    exclude_paths: list,
    status: str,
    message: str = "",
) -> None:
    log_file = _restore_log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "repo": repo,
        "snapshot": snapshot_id,
        "target": target_path,
        "include_paths": include_paths or [],
        "exclude_paths": exclude_paths or [],
        "status": status,
        "message": message,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_restore_log_last_n(n: int = 10) -> List[Dict[str, Any]]:
    log_file = _restore_log_path()
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # Newest last in file, so reverse and take first n
    entries.reverse()
    return entries[:n]


def _job_recovery_restore(
    repo: str,
    snapshot_id: str,
    target_path: str,
    include_paths: list,
    exclude_paths: list,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if log_path is None:
        log_path = _resolve_app_path(app_config.log_dir) / f"recovery-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log.info(
        "recovery_restore start repo=%s snapshot=%s target=%s include=%s exclude=%s log=%s",
        repo,
        snapshot_id,
        target_path,
        len(include_paths or []),
        len(exclude_paths or []),
        log_path,
    )
    target_path = _allow_restore_target(target_path)
    log.debug("recovery_restore target resolved=%s", target_path)
    logged = False
    try:
        log.debug("recovery_restore unlock repo=%s", repo)
        u_code, _, u_err = restic_service.unlock(repo, log_path)
        log.debug("recovery_restore unlock exit=%s stderr=%s", u_code, (u_err or "").strip()[:500])
        code, _, stderr = restic_service.restore(
            repo,
            snapshot_id,
            target_path,
            log_path,
            include_paths=include_paths or None,
            exclude_paths=exclude_paths or None,
        )
        if code != 0:
            msg = stderr or "restic restore failed"
            log.error("recovery_restore restic failed exit=%s stderr=%s", code, msg[:2000])
            _append_restore_log(
                repo, snapshot_id, target_path, include_paths, exclude_paths, "failure", msg
            )
            logged = True
            raise RuntimeError(msg)
        log.info("recovery_restore success repo=%s snapshot=%s target=%s", repo, snapshot_id, target_path)
        _append_restore_log(
            repo, snapshot_id, target_path, include_paths, exclude_paths, "success"
        )
        return {"repo": repo, "snapshot": snapshot_id, "target": target_path}
    except Exception:
        log.exception("recovery_restore exception")
        if not logged:
            _append_restore_log(
                repo,
                snapshot_id,
                target_path,
                include_paths,
                exclude_paths,
                "failure",
                str(sys.exc_info()[1]),
            )
        raise


@app.route("/")
def index() -> str:
    # Require login: redirect to login screen before showing main page
    if not session.get("user"):
        return redirect(url_for("login", next=request.url or "/"))
    restore_jobs = _read_restore_log_last_n(10)
    # Normalize keys for template (ts -> time, snapshot -> snapshot, status, message)
    for j in restore_jobs:
        j.setdefault("time", j.get("ts", ""))
        j.setdefault("repo", j.get("repo", ""))
        j.setdefault("snapshot", j.get("snapshot", ""))
        j.setdefault("target", j.get("target", ""))
        j.setdefault("status", j.get("status", ""))
        j.setdefault("message", j.get("message", ""))
    return render_template(
        "index.html",
        restore_jobs=restore_jobs,
        default_restore_target=app_config.default_restore_target,
    )


@app.route("/maintenance")
def maintenance() -> str:
    # Only admin role can access maintenance screen
    if not session.get("user"):
        return redirect(url_for("login", next=request.url or url_for("maintenance")))
    _require_admin()
    return render_template("maintenance.html")


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


@app.route("/api/recovery/roots", methods=["GET"])
def list_recovery_roots() -> Any:
    """List recovery roots for the Restore-from dropdown."""
    roots = recovery_roots_store.load()
    return jsonify([{"key": r["key"], "name": r["name"]} for r in roots])


@app.route("/api/recovery/repos", methods=["GET"])
def list_recovery_repos() -> Any:
    root_key = request.args.get("root")
    if not root_key:
        abort(400, "root is required")
    base = _recovery_repo_root(root_key)
    if not base.exists() or not base.is_dir():
        return jsonify({"root": str(base), "repos": []})
    repos = []
    for p in sorted(base.iterdir()):
        if p.is_dir():
            repos.append({"name": p.name, "path": str(p)})
    return jsonify({"root": str(base), "repos": repos})


def _snapshots_with_unlock(repo: str, log_path: Path) -> Any:
    """Run restic snapshots; if repo is locked, unlock and retry once."""
    code, stdout, stderr = restic_service.snapshots(repo, log_path)
    if code == 0:
        return json.loads(stdout)
    err = (stderr or "").lower()
    if "lock" in err or "locked" in err:
        restic_service.unlock(repo, log_path)
        code, stdout, stderr = restic_service.snapshots(repo, log_path)
        if code == 0:
            return json.loads(stdout)
    abort(500, stderr or "failed to load snapshots")


@app.route("/api/recovery/snapshots", methods=["GET"])
def list_recovery_snapshots() -> Any:
    repo = request.args.get("repo")
    if not repo:
        abort(400, "repo path is required")
    repo_path = Path(repo)
    if not repo_path.exists() or not repo_path.is_dir():
        abort(404, "repo path not found")
    log_path = _log_path(repo_path.name, "snapshots")
    data = _snapshots_with_unlock(repo, log_path)
    return jsonify(data)


def _ls_with_unlock(repo: str, snapshot: str, log_path: Path) -> tuple:
    """Run restic ls; if repo is locked, unlock and retry once."""
    code, stdout, stderr = restic_service.ls(repo, snapshot, log_path)
    if code == 0:
        return stdout, stderr
    err = (stderr or "").lower()
    if "lock" in err or "locked" in err:
        restic_service.unlock(repo, log_path)
        code, stdout, stderr = restic_service.ls(repo, snapshot, log_path)
        if code == 0:
            return stdout, stderr
    abort(500, stderr or "failed to list snapshot")


@app.route("/api/recovery/ls", methods=["GET"])
def list_recovery_ls() -> Any:
    repo = request.args.get("repo")
    snapshot = request.args.get("snapshot")
    if not repo or not snapshot:
        abort(400, "repo and snapshot are required")
    log_path = _log_path(Path(repo).name, "ls")
    command = shlex.join(restic_service.ls_args(repo, snapshot))
    stdout, _ = _ls_with_unlock(repo, snapshot, log_path)
    # restic ls: one path per line (paths start with /)
    paths = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line and (line.startswith("/") or line == "/"):
            paths.append(line)
    return jsonify({"paths": paths, "command": command})


@app.route("/api/recovery/verify", methods=["POST"])
def verify_recovery() -> Any:
    payload = request.get_json(force=True)
    repo = payload.get("repo")
    snapshot = payload.get("snapshot_id")
    target_path = payload.get("target_path")
    include_paths = payload.get("include_paths") or []
    exclude_paths = payload.get("exclude_paths") or []
    if not repo or not snapshot or not target_path:
        abort(400, "repo, snapshot_id and target_path are required")
    try:
        _allow_restore_target(target_path)
    except ValueError as e:
        abort(400, str(e))
    return jsonify({
        "repo": repo,
        "snapshot_id": snapshot,
        "target_path": target_path,
        "include_paths": include_paths,
        "exclude_paths": exclude_paths,
        "summary": f"Restore {snapshot} from {repo} to {target_path}"
        + (f" (include: {len(include_paths)} path(s))" if include_paths else "")
        + (f" (exclude: {len(exclude_paths)} path(s))" if exclude_paths else ""),
    })


@app.route("/api/recovery/restore", methods=["POST"])
def run_recovery_restore() -> Any:
    payload = request.get_json(force=True)
    repo = payload.get("repo")
    snapshot_id = payload.get("snapshot_id")
    target_path = payload.get("target_path")
    include_paths = payload.get("include_paths") or []
    exclude_paths = payload.get("exclude_paths") or []
    if not repo or not snapshot_id or not target_path:
        abort(400, "repo, snapshot_id and target_path are required")
    job_log = _resolve_app_path(app_config.log_dir) / f"recovery-{time.strftime('%Y%m%d-%H%M%S')}.log"
    job_id = job_manager.submit(
        "recovery_restore",
        Path(repo).name,
        job_log,
        lambda jl=job_log: _job_recovery_restore(
            repo, snapshot_id, target_path, include_paths, exclude_paths, jl
        ),
    )
    log.info("recovery_restore job submitted job_id=%s repo=%s snapshot=%s", job_id, repo, snapshot_id)
    return jsonify({"job_id": job_id})


@app.route("/api/admin/recovery-roots", methods=["GET"])
def admin_list_recovery_roots() -> Any:
    _require_admin()
    roots = recovery_roots_store.load()
    allowed = list_allowed_roots()
    return jsonify({"roots": roots, "allowed_dirs": allowed})


@app.route("/api/admin/recovery-roots", methods=["POST"])
def admin_add_recovery_root() -> Any:
    _require_admin()
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    path = (payload.get("path") or "").strip()
    key = (payload.get("key") or "").strip().lower().replace(" ", "_")
    if not name or not path:
        abort(400, "name and path are required")
    if not key:
        key = name.lower().replace(" ", "_")
    try:
        recovery_roots_store.add(key, name, path)
        return jsonify({"status": "ok", "key": key})
    except ValueError as e:
        abort(400, str(e))


@app.route("/api/admin/recovery-roots/<key>", methods=["DELETE"])
def admin_remove_recovery_root(key: str) -> Any:
    _require_admin()
    try:
        recovery_roots_store.remove(key)
        return jsonify({"status": "ok"})
    except KeyError:
        abort(404, "Recovery root not found")


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
    os.makedirs(_resolve_app_path(app_config.log_dir), exist_ok=True)
    debug = os.environ.get("APP_DEBUG") == "1"
    app.run(host=app_config.host, port=app_config.port, debug=debug)
