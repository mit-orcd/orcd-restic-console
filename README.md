# ORCD Restic Console

A lightweight Flask web UI for **restoring** ORCD restic backups, with an admin
**Maintenance** area for managing the available "Restore from" roots. The app shells out to
the `restic` CLI directly and works against both native `s3:` repositories and local /
mounted-path repositories.

The primary screen is **Restore**: pick a root → repository → snapshot, optionally
browse the snapshot's file list and set include/exclude paths, then verify and run the
restore. Recent restore jobs are listed below the form.

## Companion: backup engine

The repositories this console restores from are produced by
**[orcd-restic-backup](https://github.com/mit-orcd/orcd-restic-backup)**
(`restic_backup_s3.sh`), which backs up each user directory into its own restic
repository under `s3://<bucket>/<root>/<user>`, plus `restic_repair.sh` for repair.
This app is the read/restore side and shares the same restic version, password file
(`/root/.backup_pass`), repo layout, and `/stage` cache storage.

## Features

- Browse repositories and snapshots, list a snapshot's files, and restore (optionally
  filtered by include/exclude paths) to a sanctioned target directory.
- File-based login with per-user roles; admins additionally see **Maintenance**.
- Light/dark theme toggle, persisted per browser and applied before first paint.
- Persistent restic **metadata cache** (configurable, intended for fast/flash storage) so
  repeat snapshot/file listings don't re-read metadata from the backend.
- Streaming, memoized file listing to keep memory bounded on very large repositories.

## Repository layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask app: routes, login, restore/maintenance APIs |
| `wsgi.py` | Gunicorn entrypoint (`wsgi:application`) |
| `lib/` | `config`, `auth`, `jobs`, `restic`, `recovery_roots`, `logutil`, `utils` |
| `templates/`, `static/` | HTML (Jinja) and CSS/JS assets |
| `config/` | `app.yml`, `backups.yml`, `recovery_roots.yml` |
| `gunicorn.conf.py` | Gunicorn config (also `.example.py`) |
| `deploy.sh` | Dry-run-by-default deploy / rollback helper for the app server |

Runtime output (logs, job history) is written under `data/` and is git-ignored.

## Requirements

- Python 3.9+
- `restic` on `PATH` (the console calls it directly), and the `aws` CLI if you create S3
  buckets from the UI.
- Python packages (`requirements.txt`): Flask, PyYAML, cryptography, gunicorn.
- Backend repositories reachable as either `s3:…` URLs or already-mounted local paths.

## Configuration

### `config/app.yml`

```yaml
app:
  host: "0.0.0.0"
  port: 8080
  secret_key: "change-me"      # also derives the users-file encryption key (see Auth)
  max_jobs: 4                  # concurrent restore/maintenance jobs
restic:
  binary: "/usr/local/bin/restic"
  password_file: "/root/.backup_pass"
  compression: "auto"
  keep_daily: 14
  keep_weekly: 2
  cache_dir: "/stage/restore/console-cache"  # restic --cache-dir (persistent, fast storage)
  ls_max_paths: 500000         # cap paths returned by the file listing (0/empty = unlimited)
paths:
  log_dir: "./data/logs"
  job_store: "./data/job_history.json"
  restore_root: "/mnt/restores"
  backup_user_home: "/mnt/backup_home/home3"
  backup_software: "/mnt/backup_software/software"
  default_restore_target: "/stage/restore"
  restore_log: "/db/restic/log/restore.log"
  users_file: "/db/restic/sec/users"
  # debug_log_file: "./data/debug.log"   # optional, only used with APP_DEBUG/LOG_LEVEL=DEBUG
aws:
  binary: "/usr/local/bin/aws"
  default_region: "us-east-1"
  default_endpoint: "s3.amazonaws.com"
```

Relative `paths.*` are resolved from the directory containing `app.py`, so they work
regardless of the Gunicorn working directory. Restore targets are accepted only if they
are under `paths.restore_root` **or** `paths.default_restore_target`.

`restic.cache_dir` is passed to every restic invocation as `--cache-dir` and is created at
startup; point it at fast, persistent storage (e.g. `/stage`). See **Performance** below.

### `config/backups.yml`

Defines backup `destinations` (type `local` or `s3`) and `filesystems` (sources mapped to a
destination + repo suffix) used by the backup/destination management APIs.

### `config/recovery_roots.yml` and the Maintenance page

The Restore screen's **"Restore from"** list comes from `recovery_roots.yml`
(`{key, name, path}` entries). Admins manage this list on the **Maintenance** page. Only
directories **under `/mnt`** whose top-level name has the **`backup_`** prefix (e.g.
`/mnt/backup_home`, and their immediate subdirectories such as `/mnt/backup_home/home3`)
are offered/allowed, so a typo can't point the UI at an arbitrary path.

### Authentication (`paths.users_file`)

A plain file (default `/db/restic/sec/users`), one user per line:

```
# username   role    password
alice        admin   <encrypted-or-"orcd">
bob          user    <encrypted>
```

- Passwords are stored **encrypted** with a key derived from `app.secret_key` (Fernet over
  SHA-256). Because of this, **changing `secret_key` invalidates all stored passwords.**
- A literal password of `orcd` is a **force-reset** sentinel: logging in with `orcd`
  prompts the user to set a new password (which is then stored encrypted). Legacy
  plaintext passwords are transparently migrated to encrypted on first successful login.
- The `admin` role unlocks the **Maintenance** link/page.

## Running (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8080` and sign in.

## Production (Gunicorn + systemd)

Serve `wsgi:application` and keep **`workers = 1`** (restore jobs share an in-process job
store + lock; multiple workers would break job visibility). `gunicorn.conf.py` uses one
`gthread` worker with several threads for concurrent HTTP.

Example unit (`/etc/systemd/system/orcd-restic-console.service`):

```ini
[Unit]
Description=ORCD Backups (restic console) - Gunicorn
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/root/app/orcd-restic-console
Environment="PATH=/root/app/orcd-restic-console/.venv/bin:/usr/local/bin:/usr/bin"
ExecStart=/root/app/orcd-restic-console/.venv/bin/python3 -m gunicorn --config gunicorn.conf.py wsgi:application
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=60
PrivateTmp=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`restic`'s metadata cache lives under the service user's environment unless pinned; with
`restic.cache_dir` set it always uses that path. If the app runs behind nginx/Apache with
HTTPS, set **`ORCD_BEHIND_PROXY=1`** so login redirects use the correct scheme/host.

## Deployment and rollback (`deploy.sh`)

`deploy.sh` runs **on the app server** and is **dry-run by default** — it changes nothing
until you pass `--prod`.

```bash
./deploy.sh            # preview: fetch + incoming commits, no changes
./deploy.sh --prod     # fast-forward pull + restart service + health check
./deploy.sh --rollback # preview the commit it would reset to
./deploy.sh --rollback --prod   # roll back to the last deploy's commit + restart
./deploy.sh --status   # current commit, recorded rollback target, service state
```

It refuses to run on a dirty tree (protecting local `config/*.yml` edits; override with
`ALLOW_DIRTY=1`), records a rollback point before pulling, and pulls fast-forward only.
Env overrides: `SERVICE` (default `orcd-restic-console`), `BRANCH`, `REMOTE`, `ALLOW_DIRTY`.
The change set is normally code/config only; if `requirements.txt` changes, install deps in
the venv before/while restarting. After a deploy that touches `static/` or `templates/`, do
a hard browser refresh to drop cached assets.

## Performance (the `/stage` cache)

restic's local cache holds repository **metadata** (indexes and tree blobs), not file data.
Pinning `restic.cache_dir` to fast, persistent storage means repeat `snapshots` and file
listings are served locally instead of re-read from the backend (a large win when repos are
browsed through a mount). It also keeps the cache off the root filesystem.

File listing is **streamed** (never buffering the whole output), capped by
`restic.ls_max_paths` (the response carries `truncated: true` when the cap is hit), and
**memoized per `(repo, snapshot)`** for a short interval — repeat browsing of the same
snapshot returns quickly (`cached: true`). Bulk restore throughput remains backend/network
bound; the cache only accelerates locating data.

## Theming

A light/dark toggle in the header stores the choice in `localStorage`
(`backup-panel-theme`). The theme class is applied to `<html>` by a small inline script in
`<head>` before first paint, so navigating between pages does not flash the wrong theme.

## Debug logging

Set **`APP_DEBUG=1`** (or **`LOG_LEVEL=DEBUG`**) to log restore steps and every restic
command (with exit code and stderr on failure) to stderr. Set `paths.debug_log_file` to also
append those lines to a file.

## Notes

- The UI assumes source filesystems / backup repos are already mounted/reachable on the
  server.
- `data/` (logs, job history), `.venv/`, and `__pycache__/` are git-ignored; recreate the
  virtualenv from `requirements.txt` on a fresh checkout.
