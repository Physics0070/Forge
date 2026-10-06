"""Time-sliced execution for serverless hosts (Vercel): a bounded worker run triggered by HTTP.

A slice leases jobs, executes them, and makes running agents checkpoint + suspend at `budget_s` so the
function returns before the platform's hard limit. Suspended nodes resume (from their checkpoint) in
the next slice. If a function is killed anyway, its lease expires and the next slice's reaper requeues it.
"""
from __future__ import annotations

import random
import threading
import time
import uuid
from typing import Any

from forge.engine.worker import Worker


def run_tick(*, budget_s: float = 150.0, parallel: int = 4, label: str = "tick") -> dict[str, Any]:
    w = Worker(worker_id=f"{label}-{uuid.uuid4().hex[:8]}", concurrency=parallel, poll_interval=0.2)
    w.heartbeat()
    reaped = w.reap()
    w.yield_at = time.monotonic() + budget_s
    stop = threading.Event()

    def renew() -> None:  # keep leases alive while this slice works
        while not stop.wait(15):
            try:
                w.heartbeat()
            except Exception:
                pass

    t = threading.Thread(target=renew, daemon=True)
    t.start()
    started = time.monotonic()
    try:
        processed = w.run_until_idle(max_seconds=budget_s, parallel=parallel)
    finally:
        stop.set()
    if random.random() < 0.05:  # occasional housekeeping (retention, rate-limit pruning)
        try:
            w.maintenance()
        except Exception:
            pass
    return {"processed": processed, "reaped": reaped, "seconds": round(time.monotonic() - started, 2)}
