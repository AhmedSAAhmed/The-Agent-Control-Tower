"""Approval queue: a risky tool call parks here as a future the agent awaits."""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field


@dataclass
class Approval:
    id: str
    agent_id: str
    tenant_id: str
    task_id: str
    tool: str
    args: dict
    risk: int
    reasons: list[str]
    created: float = field(default_factory=time.time)
    status: str = "pending"          # pending | approved | denied | expired | voided
    decided_by: str = ""
    note: str = ""
    future: asyncio.Future | None = None

    @property
    def age_s(self) -> int:
        return int(time.time() - self.created)


class ApprovalQueue:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self.items: dict[str, Approval] = {}
        self._ids = itertools.count(1)

    def create(self, agent, task_id: str, tool: str, args: dict, risk: int, reasons: list[str]) -> Approval:
        a = Approval(f"APR-{next(self._ids):04d}", agent.id, agent.tenant_id, task_id, tool, args, risk, reasons,
                     future=asyncio.get_running_loop().create_future())
        self.items[a.id] = a
        return a

    def pending(self, tenant_id: str | None = None) -> list[Approval]:
        rows = [a for a in self.items.values() if a.status == "pending"
                and (not tenant_id or a.tenant_id == tenant_id)]
        return sorted(rows, key=lambda a: (-a.risk, a.created))

    def recent(self, tenant_id: str | None = None, limit: int = 8) -> list[Approval]:
        rows = [a for a in self.items.values() if a.status != "pending"
                and (not tenant_id or a.tenant_id == tenant_id)]
        return sorted(rows, key=lambda a: -a.created)[:limit]

    def resolve(self, approval_id: str, status: str, by: str, note: str = "") -> Approval | None:
        a = self.items.get(approval_id)
        if not a or a.status != "pending":
            return None
        a.status, a.decided_by, a.note = status, by, note
        if a.future and not a.future.done():
            a.future.set_result(status == "approved")
        return a

    def void_for_agent(self, agent_id: str, by: str, note: str) -> list[Approval]:
        return [self.resolve(a.id, "voided", by, note) for a in self.pending() if a.agent_id == agent_id]

    def expired(self) -> list[Approval]:
        now = time.time()
        return [a for a in self.pending() if now - a.created > self.ttl]
