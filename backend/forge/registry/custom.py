"""Workspace-aware agent resolution: built-in agents first, then the workspace's custom agents."""
from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from forge.models import AgentDefinitionRow
from forge.registry.agents import AgentDefinition, BUILTIN_AGENTS, agent_from_json


def workspace_agents(db: Session, workspace_id: uuid.UUID) -> dict[str, AgentDefinition]:
    agents = dict(BUILTIN_AGENTS)
    for r in db.execute(select(AgentDefinitionRow).where(AgentDefinitionRow.workspace_id == workspace_id,
                                                         AgentDefinitionRow.deleted_at.is_(None))).scalars():
        if r.key not in BUILTIN_AGENTS:
            agents[r.key] = agent_from_json(r.key, r.definition)
    return agents


def agent_lookup(db: Session, workspace_id: uuid.UUID) -> Callable[[str], AgentDefinition | None]:
    agents = workspace_agents(db, workspace_id)
    return agents.get
