"""Local-dev Postgres without Docker (pip `pgserver`). NOT for production.

    python scripts/dev_db.py            # starts Postgres, prints the DATABASE_URL, blocks
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pgserver

PGDATA = Path(__file__).resolve().parents[2] / "data" / "pgdata"


def main() -> None:
    PGDATA.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    uri = srv.get_uri()
    print(f"READY {uri}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
