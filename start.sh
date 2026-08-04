#!/usr/bin/env bash
# Railway startup: run migration, then start the server.

cd "$(dirname "$0")"

# Run database migration (non-fatal — the app can still serve pages)
python init_db.py || echo "Migration skipped or failed (non-fatal)"

# Create the data directory for user projects
mkdir -p "${OPENWRITE_DATA:-/tmp/openwrite_data}/users"

# Start the FastAPI server from the backend directory (imports expect cwd=backend/)
cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8001}"
