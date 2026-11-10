#!/bin/sh
set -e

# Crée les tables manquantes (remplace Alembic)
python -m app.create_tables

exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --forwarded-allow-ips "*" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --access-logfile -
