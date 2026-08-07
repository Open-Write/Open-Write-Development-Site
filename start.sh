#!/usr/bin/env bash
# Railway startup: run migration, build frontend if needed, then start the server.

cd "$(dirname "$0")"

# Run database migration (non-fatal — the app can still serve pages)
python init_db.py || echo "Migration skipped or failed (non-fatal)"

# Build the frontend if Node.js is available (so source edits get compiled)
if command -v npm &>/dev/null && [ -d frontend/src ]; then
    echo "Building frontend from source…"
    cd frontend
    npm ci --silent 2>/dev/null || npm install --silent 2>/dev/null || true
    npx vite build 2>/dev/null || echo "Frontend build skipped (using pre-built dist)"
    cd ..
fi

# Create the data directory for user projects (uses Railway persistent volume at /data)
mkdir -p "${OPENWRITE_DATA:-/data/openwrite_data}/users"

# Start the FastAPI server from the backend directory (imports expect cwd=backend/)
cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8001}"
