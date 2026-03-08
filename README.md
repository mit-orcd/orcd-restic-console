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

## Security

Access is controlled by a users file (see `paths.users_file` in `config/app.yml`, default `/db/restic/sec/users`). Format: one line per user:

```
username   role   password
```

- **username**: login name  
- **role**: `admin` or any other role (e.g. `user`). Only `admin` can open the Maintenance page.  
- **password**: plain text (no spaces in the middle of the line, or use tab separator). Can be extended later to support hashed passwords.

Create the file and directory if missing, e.g. `mkdir -p /db/restic/sec` then add lines like `admin   admin   your-secret`.

## Configuration

Edit:

- `config/app.yml` for restic binary, password file, retention, log paths.
- `config/backups.yml` for filesystem sources and destinations.

Restore targets must live under `paths.restore_root` in `config/app.yml`.

**Recovery panel "Restore from" options** are defined in `config/recovery_roots.yml`. Only paths under `/mnt` whose directory name has the prefix `backup_` are allowed. Admins can add or remove roots from the **Maintenance** page (admin role required).

## Notes

- The UI calls restic directly via CLI. Ensure `restic` and `aws` (if using S3) are installed.
- The GUI assumes filesystems are already mounted on the server.
