#!/bin/sh
# Fix persistent volume permissions — Railway mounts volumes as root,
# but the app runs as appuser (uid 1000). Without this, /data/* is not writable.
mkdir -p /data/backups /data/output /data/screenshots /data/uploads
chown -R 1000:1000 /data

# Drop to appuser and start the application
exec su -s /bin/sh appuser -c "uvicorn aixis_web.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-keep-alive 65 --timeout-graceful-shutdown 30"
