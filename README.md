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
- **role**: `admin` or `user`. Only `admin` can open the Maintenance page.  
- **password**: stored **encrypted** (symmetric encryption using `app.secret_key`). On login, the typed password is compared against the decrypted value.

**Default password / force reset:** If the password field for a user is the literal plain text `orcd`, that user must set a new password on first sign-in. After signing in with `orcd`, they are prompted to enter and confirm a new password; it is then encrypted and saved in the users file.

**New users:** Add a line with password `orcd` to force the user to set a password on first login, or add a line with an already-encrypted password (e.g. after running a small script that calls the app’s encryption). For convenience you can temporarily add `username   role   orcd` and the user will be forced to set a new password at first login.

Create the file and directory if missing, e.g. `mkdir -p /db/restic/sec`. Ensure `app.secret_key` in `config/app.yml` is set to a strong secret (used for sessions and password encryption).

## Configuration

Edit:

- `config/app.yml` for restic binary, password file, retention, log paths.
- `config/backups.yml` for filesystem sources and destinations.

Restore targets must live under `paths.restore_root` in `config/app.yml`.

**Recovery panel "Restore from" options** are defined in `config/recovery_roots.yml`. Only paths under `/mnt` whose directory name has the prefix `backup_` are allowed. Admins can add or remove roots from the **Maintenance** page (admin role required).

## Notes

- The UI calls restic directly via CLI. Ensure `restic` and `aws` (if using S3) are installed.
- The GUI assumes filesystems are already mounted on the server.
