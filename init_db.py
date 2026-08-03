"""Run the schema.sql migration against the DATABASE_URL."""
import os
import sys
from pathlib import Path

import psycopg2

_REPO_ROOT = Path(__file__).resolve().parent
SCHEMA = _REPO_ROOT / "schema.sql"


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set — skipping migration.", file=sys.stderr)
        return
    sql = SCHEMA.read_text(encoding="utf-8")
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        print("Schema migration applied successfully.")
    except Exception as exc:
        print(f"Migration error (may be idempotent-safe): {exc}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
