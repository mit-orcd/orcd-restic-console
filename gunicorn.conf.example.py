# Copy to gunicorn.conf.py and adjust. Use from the application root (where app.py lives).
# IMPORTANT: use workers = 1 unless you replace the job store with Redis etc. Multiple workers
# each run restores in separate processes; the job file + flock fix cross-worker job visibility.

bind = "0.0.0.0:8080"
workers = 1
# gthread + threads: safe concurrent HTTP in one worker (do not raise workers above 1 for jobs)
worker_class = "gthread"
threads = 4

timeout = 300
graceful_timeout = 60
keepalive = 2

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Debug: set in environment instead of editing this file:
#   Environment="APP_DEBUG=1" in systemd, or
#   export APP_DEBUG=1 && gunicorn ...
# Optional: LOG_LEVEL=DEBUG

proc_name = "orcd-restic-console"
