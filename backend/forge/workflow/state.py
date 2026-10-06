"""Validated state machines for nodes and runs. The only way state changes in the engine."""
from __future__ import annotations

from enum import Enum


class NodeState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RECOVERING = "RECOVERING"
    # Not in the original list: a branch not taken (CONDITION) or a RECOVERY that was never needed.
    SKIPPED = "SKIPPED"


class RunState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


N = NodeState
R = RunState

NODE_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    N.PENDING: frozenset({N.READY, N.SKIPPED, N.CANCELLED, N.BLOCKED, N.FAILED}),
    N.READY: frozenset({N.RUNNING, N.CANCELLED, N.BLOCKED, N.SKIPPED}),
    # RUNNING -> READY: lease expired / worker died, job is requeued (recovery)
    # RUNNING -> PENDING: RETRY-node loop-back (waits for the re-run target to settle again)
    N.RUNNING: frozenset({N.SUCCESS, N.FAILED, N.RETRYING, N.WAITING_APPROVAL, N.VERIFICATION_FAILED,
                          N.BLOCKED, N.CANCELLED, N.READY, N.PENDING}),
    N.RETRYING: frozenset({N.READY, N.FAILED, N.CANCELLED}),
    N.WAITING_APPROVAL: frozenset({N.SUCCESS, N.READY, N.FAILED, N.CANCELLED, N.SKIPPED}),
    N.VERIFICATION_FAILED: frozenset({N.RECOVERING, N.RETRYING, N.FAILED, N.CANCELLED}),
    N.RECOVERING: frozenset({N.SUCCESS, N.READY, N.FAILED, N.CANCELLED}),
    N.BLOCKED: frozenset({N.READY, N.FAILED, N.CANCELLED}),
    # FAILED -> READY only through an explicit user "retry" (or RETRY-node loop-back)
    # FAILED -> RECOVERING: a RECOVERY node is repairing this node's output
    N.FAILED: frozenset({N.READY, N.RECOVERING}),
    N.SUCCESS: frozenset({N.READY}),  # only a RETRY-node loop-back may re-run a succeeded node
    N.CANCELLED: frozenset(),
    N.SKIPPED: frozenset(),
}

RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    R.PENDING: frozenset({R.RUNNING, R.CANCELLED, R.FAILED}),
    R.RUNNING: frozenset({R.PAUSED, R.WAITING_APPROVAL, R.BLOCKED, R.SUCCESS, R.FAILED, R.CANCELLED}),
    R.PAUSED: frozenset({R.RUNNING, R.CANCELLED}),
    R.WAITING_APPROVAL: frozenset({R.RUNNING, R.BLOCKED, R.SUCCESS, R.CANCELLED, R.FAILED}),
    R.BLOCKED: frozenset({R.RUNNING, R.WAITING_APPROVAL, R.SUCCESS, R.CANCELLED, R.FAILED}),
    R.FAILED: frozenset({R.RUNNING}),  # user retry resumes from checkpoints
    R.SUCCESS: frozenset(),
    R.CANCELLED: frozenset(),
}

NODE_TERMINAL = frozenset({N.SUCCESS, N.FAILED, N.CANCELLED, N.SKIPPED})
NODE_SETTLED = frozenset({N.SUCCESS, N.SKIPPED})  # downstream may proceed


class IllegalTransition(Exception):
    def __init__(self, kind: str, src: str, dst: str):
        super().__init__(f"Illegal {kind} transition {src} -> {dst}")
        self.kind, self.src, self.dst = kind, src, dst


def check_node_transition(src: NodeState | str, dst: NodeState | str) -> NodeState:
    s, d = NodeState(src), NodeState(dst)
    if d not in NODE_TRANSITIONS[s]:
        raise IllegalTransition("node", s.value, d.value)
    return d


def check_run_transition(src: RunState | str, dst: RunState | str) -> RunState:
    s, d = RunState(src), RunState(dst)
    if d not in RUN_TRANSITIONS[s]:
        raise IllegalTransition("run", s.value, d.value)
    return d
