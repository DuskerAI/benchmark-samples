#!/bin/bash
set -euo pipefail

# Start PostgreSQL via the official entrypoint (handles initdb on first run)
docker-entrypoint.sh postgres &

echo "Waiting for PostgreSQL..."
until pg_isready -h localhost -U postgres 2>/dev/null; do
    sleep 1
done
echo "PostgreSQL ready."

# Apply schema
PGPASSWORD=secret psql -U postgres -d orders_db -f /app/migrations/001_schema.sql

# Run Node.js in a restart loop so agents can kill/rebuild and it auto-restarts
while true; do
    node /app/dist/index.js 2>&1 || true
    sleep 1
done
