"""Configurable data retention. Nothing is kept forever by accident.

Per workspace (table retention_policies): run_logs_days (events), traces_days (tool calls + checkpoints),
artifacts_days (artifacts + object storage), memory_days (memory entries). Only data of FINISHED runs is swept.
"""
from __future__ import annotations

import logging
import shutil
import time

from sqlalchemy import text

from forge.db import session_scope
from forge.logging import log
from forge.sandbox import workspace_root
from forge.storage import get_store

BATCH = 500


def sweep() -> dict[str, int]:
    counts = {"events": 0, "traces": 0, "artifacts": 0, "memory": 0, "run_dirs": 0}
    store = get_store()
    with session_scope() as db:
        pols = db.execute(text("SELECT workspace_id, run_logs_days, artifacts_days, memory_days, traces_days FROM retention_policies")).all()
    for ws, logs_d, art_d, mem_d, tr_d in pols:
        finished = "run_id IN (SELECT id FROM runs WHERE workspace_id=:ws AND completed_at IS NOT NULL AND completed_at < now() - make_interval(days => :d))"
        with session_scope() as db:
            counts["events"] += db.execute(text(f"DELETE FROM events WHERE workspace_id=:ws AND ts < now() - make_interval(days => :d) AND {finished}"),
                                           {"ws": ws, "d": logs_d}).rowcount or 0
        # events are append-only (trigger forbids UPDATE only; DELETE allowed for retention)
        with session_scope() as db:
            counts["traces"] += db.execute(text(f"DELETE FROM tool_calls WHERE workspace_id=:ws AND {finished}"), {"ws": ws, "d": tr_d}).rowcount or 0
            counts["traces"] += db.execute(text(f"DELETE FROM checkpoints WHERE workspace_id=:ws AND {finished}"), {"ws": ws, "d": tr_d}).rowcount or 0
        with session_scope() as db:
            rows = db.execute(text(f"""SELECT id, storage_key FROM artifacts WHERE workspace_id=:ws AND {finished.replace('run_id', 'artifacts.run_id')}
                                       OR (workspace_id=:ws AND expires_at IS NOT NULL AND expires_at < now()) LIMIT {BATCH}"""),
                              {"ws": ws, "d": art_d}).all()
            for _aid, key in rows:
                if key:
                    try:
                        store.delete(key)
                    except Exception:
                        logging.getLogger("forge").warning("could not delete object %s", key)
            if rows:
                db.execute(text("DELETE FROM artifacts WHERE id = ANY(:ids)"), {"ids": [r[0] for r in rows]})
            counts["artifacts"] += len(rows)
        with session_scope() as db:
            counts["memory"] += db.execute(text("""DELETE FROM memory_entries WHERE workspace_id=:ws AND
                (created_at < now() - make_interval(days => :d) OR (expires_at IS NOT NULL AND expires_at < now()))"""),
                                           {"ws": ws, "d": mem_d}).rowcount or 0
    # local run sandboxes older than 1 day
    root = workspace_root()
    if root.exists():
        cutoff = time.time() - 86400
        for runs_dir in root.glob("*/projects/*/runs/*"):
            try:
                if runs_dir.is_dir() and runs_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(runs_dir, ignore_errors=True)
                    counts["run_dirs"] += 1
            except OSError:
                continue
    log("retention_sweep", **counts)
    return counts

