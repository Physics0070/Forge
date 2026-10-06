"""Builders for engine tests: real Postgres, real worker, scripted provider."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from forge.db import session_scope
from forge.engine import core
from forge.engine.runtime import set_provider
from forge.engine.worker import Worker
from forge.models import (Artifact, Event, ModelCall, Project, Repository, Run, RunNode, ToolCall, User, Workflow, WorkflowVersion,
                          Workspace, WorkspaceMember)
from forge.repos import import_zip
from forge.storage import get_store
from forge.workflow.ir import Workflow as IR


def agent_node(id: str, agent: str = "planner", **kw) -> dict[str, Any]:
    n = {"id": id, "type": "AGENT", "agentId": agent, "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
         "retryPolicy": {"maxAttempts": 2, "backoff": "none", "jitter": 0}, "timeoutS": 30}
    n.update(kw)
    return n


def edge(src: str, dst: str, **kw) -> dict[str, Any]:
    return {"id": f"{src}-{dst}", "source": src, "target": dst, **kw}


def ir(nodes: list[dict], edges: list[dict] = (), **kw) -> dict[str, Any]:
    return IR.from_json({"name": "t", "goal": "test goal", "nodes": nodes, "edges": list(edges), **kw}).to_json()


class Env:
    """A user + workspace + project (+ optional repo) wired straight into the DB."""

    def __init__(self):
        with session_scope() as db:
            u = User(email=f"{uuid.uuid4().hex[:8]}@t.dev", password_hash="x")
            db.add(u)
            db.flush()
            ws = Workspace(name="w", created_by=u.id)
            db.add(ws)
            db.flush()
            db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner"))
            p = Project(workspace_id=ws.id, name="p", created_by=u.id)
            db.add(p)
            db.flush()
            self.user_id, self.ws, self.project = u.id, ws.id, p.id
        self.repo_id: uuid.UUID | None = None

    def add_repo(self, files: dict[str, bytes]) -> uuid.UUID:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, d in files.items():
                z.writestr(n, d)
        snap = import_zip(buf.getvalue())
        with session_scope() as db:
            r = Repository(workspace_id=self.ws, project_id=self.project, kind="zip", status="ready", file_count=snap.file_count,
                           size_bytes=snap.size_bytes)
            db.add(r)
            db.flush()
            r.storage_key = f"repos/{self.ws}/{self.project}/{r.id}.tar.gz"
            get_store().put(r.storage_key, snap.archive)
            self.repo_id = r.id
        return self.repo_id

    def version(self, ir_json: dict[str, Any], approved: bool = True) -> uuid.UUID:
        with session_scope() as db:
            wf = Workflow(workspace_id=self.ws, project_id=self.project, name="wf", created_by=self.user_id)
            db.add(wf)
            db.flush()
            wv = WorkflowVersion(workspace_id=self.ws, workflow_id=wf.id, version=1, ir=ir_json, ir_hash=IR.from_json(ir_json).content_hash(),
                                 created_by=self.user_id, approved_by=self.user_id if approved else None,
                                 approved_at=core.utcnow() if approved else None)
            db.add(wv)
            db.flush()
            return wv.id

    def start(self, ir_json: dict[str, Any], *, input: dict | None = None, repo: bool = False, **kw) -> uuid.UUID:
        vid = self.version(ir_json)
        with session_scope() as db:
            wv = db.get(WorkflowVersion, vid)
            run = core.create_run(db, wv=wv, project_id=self.project, created_by=self.user_id,
                                  input=input or {"objective": "audit"}, repository_id=self.repo_id if repo else None, **kw)
            return run.id


def work(provider, *, parallel: int = 4, seconds: float = 60) -> Worker:
    set_provider(provider)
    w = Worker(concurrency=parallel, poll_interval=0.05)
    w.run_until_idle(max_seconds=seconds, parallel=parallel)
    return w


def run_row(run_id) -> Run:
    with session_scope() as db:
        r = db.get(Run, run_id)
        db.expunge(r)
        return r


def nodes_of(run_id) -> dict[str, RunNode]:
    with session_scope() as db:
        return {rn.node_id: rn for rn in db.execute(select(RunNode).where(RunNode.run_id == run_id)).scalars()}


def events_of(run_id, type: str | None = None) -> list[Event]:
    with session_scope() as db:
        q = select(Event).where(Event.run_id == run_id).order_by(Event.id)
        if type:
            q = q.where(Event.type == type)
        return list(db.execute(q).scalars())


def states(run_id) -> dict[str, str]:
    return {k: v.state for k, v in nodes_of(run_id).items()}


def model_calls(run_id) -> list[ModelCall]:
    with session_scope() as db:
        return list(db.execute(select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.ts)).scalars())


def tool_calls(run_id) -> list[ToolCall]:
    with session_scope() as db:
        return list(db.execute(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.ts)).scalars())


def artifacts(run_id, type: str | None = None) -> list[Artifact]:
    with session_scope() as db:
        q = select(Artifact).where(Artifact.run_id == run_id)
        if type:
            q = q.where(Artifact.type == type)
        return list(db.execute(q).scalars())
