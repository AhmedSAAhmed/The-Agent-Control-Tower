"""The control plane. Owns the fleet, the tool gateway and every operator intervention."""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field

from . import config
from .agents.base import ToolDenied
from .agents.workers import KYCAgent, SupportAgent, WithdrawalAgent
from .control.approvals import ApprovalQueue
from .control.guardian import Guardian
from .control.policy import PolicyEngine, load_policy
from .events import EventBus, EventStore
from .llm import LLMRouter
from .sim.world import ROGUE_TICKET, World

AGENT_TYPES = {"kyc": KYCAgent, "withdrawals": WithdrawalAgent, "support": SupportAgent}


@dataclass
class Incident:
    id: str
    agent_id: str
    tenant_id: str
    reason: str
    rule: str
    evidence: list[int]
    opened: float = field(default_factory=time.time)
    status: str = "open"             # open | released | killed
    closed_by: str = ""


class Tower:
    def __init__(self, *, db_path: str = config.DB_PATH, fresh: bool = not config.KEEP_DB,
                 llm_mode: str = config.LLM_MODE, task_gap: float = config.TASK_GAP_SECONDS,
                 step_delay: float = config.STEP_DELAY_SECONDS, seed: int | None = None,
                 guardian_overrides: dict | None = None):
        policy = load_policy()
        policy["guardian"].update(guardian_overrides or {})
        self.policy = PolicyEngine(policy)
        self.store = EventStore(db_path, fresh=fresh)
        self.bus = EventBus(self.store)
        self.llm = LLMRouter(mode=llm_mode)
        self.world = World(self.policy.tenants, seed=seed)
        self.approvals = ApprovalQueue(config.APPROVAL_TTL_SECONDS)
        self.guardian = Guardian(self.policy.policy["guardian"], self)
        self.task_gap, self.step_delay = task_gap, step_delay
        self.incidents: dict[str, Incident] = {}
        self._incident_ids = itertools.count(1)
        self.blocked_egress = 0
        self.always_active = False
        self.started = time.time()
        self.agents = {}
        for spec in self.policy.policy["agents"]:
            cls = AGENT_TYPES[spec["kind"]]
            self.agents[spec["id"]] = cls(spec, self.policy.tenants[spec["tenant"]], self)
        self._bg: list[asyncio.Task] = []

    # ---- lifecycle -------------------------------------------------------------------
    @property
    def active(self) -> bool:
        if self.always_active or self.bus.viewers:
            return True
        return time.time() - self.bus.last_viewer_at < config.IDLE_AFTER_SECONDS

    async def start(self) -> None:
        self.system("system.boot", f"Control tower online · {len(self.agents)} agents · "
                    f"{len(self.policy.tenants)} tenants · LLM mode {self.llm.mode} "
                    f"(providers: {', '.join(self.llm.available()) or 'none, scripted brain'})")
        for agent in self.agents.values():
            agent.runner = asyncio.create_task(agent.run(), name=agent.id)
        self._bg.append(asyncio.create_task(self._sweeper()))

    async def stop(self) -> None:
        for a in self.agents.values():
            if a.runner:
                a.runner.cancel()
        for t in self._bg:
            t.cancel()
        await self.llm.aclose()

    async def _sweeper(self) -> None:
        while True:
            await asyncio.sleep(5)
            for a in self.approvals.expired():
                self.approvals.resolve(a.id, "expired", "tower", "no decision within TTL; failing closed")
                self.system("approval.expired", f"{a.id} expired after {a.age_s}s; action not executed",
                            "warn", tenant_id=a.tenant_id, agent_id=a.agent_id, task_id=a.task_id)

    def system(self, type: str, summary: str, severity: str = "info", actor: str = "tower", **kw) -> dict:
        data = kw.pop("data", {})
        return self.bus.emit(type=type, summary=summary, severity=severity, actor=actor, data=data, **kw)

    # ---- the tool gateway: the only path from an agent to a real system ------------------
    async def gateway(self, agent, tool: str, args: dict) -> dict:
        await agent.checkpoint()
        task_id = agent.task.id if agent.task else None
        if agent.task:
            agent.task.tool_calls += 1
        agent.last_action = f"Calling {tool}"
        if agent.revoked:
            agent.emit("tool.denied", f"{tool} refused: credentials revoked", "critical", tool=tool, args=args,
                       rules=["revoked"])
            raise ToolDenied("credentials revoked")
        verdict = self.policy.evaluate(agent, tool, args, tainted=agent.tainted)
        self.guardian.call(agent)
        if agent.status == "quarantined":
            raise ToolDenied("quarantined")
        if verdict.action == "deny":
            ev = agent.emit("tool.denied", f"{tool} denied: {'; '.join(verdict.reasons)}",
                            "critical" if verdict.critical else "warn", tool=tool, args=args,
                            rules=verdict.rules, risk=verdict.risk)
            if "egress" in verdict.rules:
                self.blocked_egress += 1
            self.guardian.strike(agent, verdict, ev["id"])
            raise ToolDenied(verdict.reasons[0])
        if verdict.action == "approve":
            appr = self.approvals.create(agent, task_id, tool, args, verdict.risk, verdict.reasons)
            agent.emit("approval.requested", f"{appr.id}: {tool} needs approval: {'; '.join(verdict.reasons)}",
                       "warn", approval_id=appr.id, tool=tool, args=args, rules=verdict.rules, risk=verdict.risk)
            agent.status = "waiting_approval"
            agent.blocker = f"Awaiting human approval ({appr.id}): {verdict.reasons[0]}"
            try:
                ok = await appr.future
            finally:
                agent.blocker = ""
                if agent.status == "waiting_approval":
                    agent.status = "running"
            if not ok:
                raise ToolDenied(f"{appr.id} {appr.status} by {appr.decided_by}")
            await agent.checkpoint()
        result = self.world.execute(agent.tenant_id, tool, args)
        agent.emit("tool.executed", f"{tool} executed", tool=tool, args=args, result=result,
                   via_approval=verdict.action == "approve", risk=verdict.risk)
        agent.last_action = f"Executed {tool}"
        await agent.pace()
        return result

    # ---- interventions ------------------------------------------------------------------
    def _agent(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent

    def _op(self, agent, type: str, summary: str, by: str, severity: str = "warn", **data) -> dict:
        return self.bus.emit(type=type, summary=summary, severity=severity,
                             actor="tower" if by == "tower" else f"operator:{by}",
                             tenant_id=agent.tenant_id, agent_id=agent.id,
                             task_id=agent.task.id if agent.task else None, data=data)

    def pause(self, agent_id: str, by: str, reason: str = "") -> None:
        a = self._agent(agent_id)
        if a.status in {"killed", "quarantined", "paused"}:
            return
        a.gate.clear(); a.status = "paused"; a.last_action = "Paused"
        self._op(a, "agent.paused", f"{a.name} paused{': ' + reason if reason else ''}", by, reason=reason)

    def resume(self, agent_id: str, by: str) -> None:
        a = self._agent(agent_id)
        if a.status != "paused":
            return
        if a.cost_usd >= a.budget_usd:
            a.budget_usd = round(a.cost_usd + 0.25, 2)
            self._op(a, "agent.budget_raised", f"Budget raised to ${a.budget_usd:.2f} on resume", by, "info")
        a.status = "running"; a.gate.set()
        self._op(a, "agent.resumed", f"{a.name} resumed", by, "info")

    def kill(self, agent_id: str, by: str, reason: str = "operator kill switch") -> None:
        a = self._agent(agent_id)
        if a.status == "killed":
            return
        a.revoked = True
        if a.runner:
            a.runner.cancel()
        voided = self.approvals.void_for_agent(a.id, by, "agent killed")
        if a.task:
            a._end("aborted", "killed")
        a.status, a.blocker, a.last_action = "killed", "", "Killed"
        a.gate.set()
        self._op(a, "agent.killed", f"{a.name} KILLED · credentials revoked · {len(voided)} pending approvals voided",
                 by, "critical", reason=reason)
        for inc in self.incidents.values():
            if inc.agent_id == a.id and inc.status == "open":
                inc.status, inc.closed_by = "killed", by

    def restart(self, agent_id: str, by: str) -> None:
        a = self._agent(agent_id)
        if a.status != "killed":
            return
        a.revoked, a.abort_current = False, False
        a.gate.set()
        a.runner = asyncio.create_task(a.run(), name=a.id)
        self._op(a, "agent.restarted", f"{a.name} restarted from a clean image with fresh credentials", by, "info")

    def quarantine(self, agent, reason: str, rule: str, evidence: list[int] | None = None) -> Incident:
        agent.status, agent.revoked, agent.abort_current = "quarantined", True, True
        agent.gate.clear()
        agent.blocker = f"Quarantined: {reason}"
        voided = self.approvals.void_for_agent(agent.id, "tower", "agent quarantined")
        inc = Incident(f"INC-{next(self._incident_ids):03d}", agent.id, agent.tenant_id, reason, rule, evidence or [])
        self.incidents[inc.id] = inc
        self.bus.emit(type="guardian.quarantine", severity="critical", actor="tower", tenant_id=agent.tenant_id,
                      agent_id=agent.id, task_id=agent.task.id if agent.task else None,
                      summary=f"{inc.id}: {agent.name} QUARANTINED · {reason} · credentials revoked, "
                              f"task frozen, {len(voided)} approvals voided",
                      data={"incident": inc.id, "rule": rule, "evidence": evidence or [],
                            "egress_bytes": self.world.egress_bytes})
        return inc

    def release(self, incident_id: str, by: str) -> None:
        inc = self.incidents.get(incident_id)
        if not inc or inc.status != "open":
            return
        a = self._agent(inc.agent_id)
        inc.status, inc.closed_by = "released", by
        if a.status == "quarantined":
            a.revoked, a.blocker = False, ""
            a.status = "running"
            a.gate.set()   # abort_current is still set: the frozen task is discarded, not resumed
        self._op(a, "guardian.released", f"{inc.id} reviewed; {a.name} released with fresh credentials "
                 "(frozen task discarded)", by, "info")

    def decide(self, approval_id: str, approve: bool, by: str, note: str = "") -> None:
        appr = self.approvals.resolve(approval_id, "approved" if approve else "denied", by, note)
        if not appr:
            return
        a = self.agents[appr.agent_id]
        verb = "approved" if approve else "denied"
        self.bus.emit(type=f"approval.{verb}", summary=f"{appr.id} {verb}: {appr.tool}{' · ' + note if note else ''}",
                      severity="info" if approve else "warn", actor=f"operator:{by}", tenant_id=appr.tenant_id,
                      agent_id=a.id, task_id=appr.task_id,
                      data={"approval_id": appr.id, "tool": appr.tool, "args": appr.args, "wait_s": appr.age_s})

    def pause_all(self, by: str, tenant_id: str | None = None) -> None:
        for a in self.agents.values():
            if not tenant_id or a.tenant_id == tenant_id:
                self.pause(a.id, by, "fleet-wide emergency stop")

    def resume_all(self, by: str, tenant_id: str | None = None) -> None:
        for a in self.agents.values():
            if not tenant_id or a.tenant_id == tenant_id:
                self.resume(a.id, by)

    def set_mode(self, mode: str, by: str) -> None:
        if mode in {"hybrid", "live", "scripted"}:
            self.llm.mode = mode
            self.system("system.mode", f"LLM mode set to {mode}", actor=f"operator:{by}")

    # ---- drills (scenarios a non-builder can trigger) --------------------------------------
    DRILLS = {
        "rogue": "Prompt-injection hijack · Rimal Eats support",
        "loop": "Runaway loop · Dilmun Telecom support",
        "withdrawal": "Large payout to new beneficiary · Pearl Bank",
        "pep": "PEP applicant · Pearl Bank KYC",
    }

    def drill(self, name: str, by: str) -> str:
        if name == "rogue":
            a = self._agent("saad-support")
            self.world.force(a.id, {"kind": "support", "ref": "TCK-88231", "customer": "عبدالله القحطاني",
                                    "lang": "ar", "channel": "WhatsApp", "text": ROGUE_TICKET,
                                    "disputed_amount": 0, "currency": "SAR", "drill": "injection",
                                    "title": "WhatsApp ticket TCK-88231 (contains hidden instructions)"})
        elif name == "loop":
            a = self._agent("layla-support")
            self.world.force(a.id, {"kind": "support", "ref": "TCK-77002", "customer": "Khalid Fakhro", "lang": "en",
                                    "channel": "WhatsApp", "text": "Where is my SIM delivery?",
                                    "disputed_amount": 0, "currency": "BHD", "drill": "loop",
                                    "title": "WhatsApp ticket TCK-77002"})
        elif name == "withdrawal":
            a = self._agent("faisal-withdrawals")
            self.world.force(a.id, {"kind": "withdrawals", "ref": "WD-90417", "customer": "Hind Al-Mannai",
                                    "amount": 18500.0, "currency": "BHD", "beneficiary": "AE070331234567890123456",
                                    "beneficiary_age_hours": 2, "destination_country": "UAE",
                                    "title": "Withdrawal WD-90417 · 18,500.000 BHD"})
        elif name == "pep":
            a = self._agent("noor-kyc")
            self.world.force(a.id, {"kind": "kyc", "ref": "APP-55120", "applicant": "Yousef Al-Khalifa",
                                    "nationality": "Bahraini", "occupation": "Ministry director",
                                    "source_of_funds": "Salary, real estate", "pep": True, "sanctions": False,
                                    "docs": "complete", "title": "KYC review APP-55120 · Yousef Al-Khalifa"})
        else:
            raise KeyError(name)
        if a.status in {"paused", "killed", "quarantined"}:
            note = f" ({a.name} is {a.status}; the work is queued until it is back)"
        else:
            note = ""
        self.bus.emit(type="drill.started", summary=f"Drill started: {self.DRILLS[name]}{note}", severity="warn",
                      actor=f"operator:{by}", tenant_id=a.tenant_id, agent_id=a.id, data={"drill": name})
        return a.id

    # ---- read models for the UI ---------------------------------------------------------
    def fleet(self, tenant_id: str | None = None) -> list:
        return [a for a in self.agents.values() if not tenant_id or a.tenant_id == tenant_id]

    def kpis(self, tenant_id: str | None = None) -> dict:
        fleet = self.fleet(tenant_id)
        return {
            "agents": len(fleet),
            "running": sum(a.status in {"running", "waiting_approval"} for a in fleet),
            "attention": sum(a.status in {"waiting_approval", "quarantined", "paused"} for a in fleet),
            "pending": len(self.approvals.pending(tenant_id)),
            "incidents": sum(1 for i in self.incidents.values() if i.status == "open"
                             and (not tenant_id or i.tenant_id == tenant_id)),
            "cost": sum(a.cost_usd for a in fleet),
            "tokens": sum(a.tokens_in + a.tokens_out for a in fleet),
            "tasks": sum(a.tasks_done for a in fleet),
            "blocked_egress": self.blocked_egress,
            "egress_bytes": self.world.egress_bytes,
        }
