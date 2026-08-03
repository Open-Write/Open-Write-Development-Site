#!/usr/bin/env bash
# Railway startup: run migration, then start the server.
set -e

cd "$(dirname "$0")"

# Run database migration
python init_db.py

# Create the data directory for user projects
mkdir -p "${OPENWRITE_DATA:-/tmp/openwrite_data}/users"

# Start the FastAPI server from the backend directory (imports expect cwd=backend/)
cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8001}"
