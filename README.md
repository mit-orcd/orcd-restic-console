# Backup GUI

This is a lightweight Flask UI for managing restic backups, restores, and destinations.

## Quick start

```bash
cd gui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8080`. You will be prompted to sign in.


## Configuration

Edit:

- `config/app.yml` for restic binary, password file, retention, log paths.
- `config/backups.yml` for filesystem sources and destinations.

Restore targets must live under `paths.restore_root` in `config/app.yml`.

**Recovery panel "Restore from" options** are defined in `config/recovery_roots.yml`. Only paths under `/mnt` whose directory name has the prefix `backup_` are allowed. Admins can add or remove roots from the **Maintenance** page (admin role required).

## Production (Gunicorn)

See **DEPLOYMENT.md**. Use **`workers = 1`** in Gunicorn so restore jobs stay consistent across requests. Relative paths in `config/app.yml` are resolved from the directory that contains `app.py`.

## Debug logging

Set **`APP_DEBUG=1`** or **`LOG_LEVEL=DEBUG`** to log restore steps and every restic command (exit code, stderr on failure) to stderr. Optionally set **`paths.debug_log_file`** in `config/app.yml` to also append those DEBUG lines to a file.

## Notes

- The UI calls restic directly via CLI. Ensure `restic` and `aws` (if using S3) are installed.
- The GUI assumes filesystems are already mounted on the server.
