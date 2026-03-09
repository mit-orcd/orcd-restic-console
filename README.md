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

## Notes

- The UI calls restic directly via CLI. Ensure `restic` and `aws` (if using S3) are installed.
- The GUI assumes filesystems are already mounted on the server.
