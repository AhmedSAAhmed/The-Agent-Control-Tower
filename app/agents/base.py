"""Agent runtime. Agents think with an LLM and act only through the tower's tool gateway.

Between every step an agent passes a checkpoint, which is where pause, quarantine and
kill take effect: an operator command lands within one step, never "after the task".
"""
from __future__ import annotations

import asyncio
import itertools
import json
import random
import time
from collections import deque
from dataclasses import dataclass, field

_task_ids = itertools.count(1)


class ToolDenied(Exception):
    pass


class TaskAborted(Exception):
    pass


@dataclass
class Task:
    id: str
    title: str
    ref: str
    started: float = field(default_factory=time.time)
    ended: float | None = None
    status: str = "running"          # running | done | blocked | aborted
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    outcome: str = ""

    @property
    def duration_s(self) -> float:
        return round((self.ended or time.time()) - self.started, 1)


class Agent:
    kind = "base"

    def __init__(self, spec: dict, tenant: dict, tower):
        self.id = spec["id"]
        self.name = spec["name"]
        self.role = spec["role"]
        self.tenant_id = spec["tenant"]
        self.tenant = tenant
        self.budget_usd = float(spec.get("budget_usd", 0.25))
        self.tower = tower
        self.status = "starting"     # running | idle | paused | waiting_approval | quarantined | killed
        self.gate = asyncio.Event()
        self.gate.set()
        self.revoked = False         # credentials revoked: the gateway refuses every call
        self.abort_current = False
        self.tainted = False         # current task input flagged as prompt injection
        self.task: Task | None = None
        self.tasks: deque[Task] = deque(maxlen=60)
        self.tokens_in = self.tokens_out = 0
        self.cost_usd = 0.0
        self.tasks_done = 0
        self.last_action = "Booting"
        self.blocker = ""
        self.decisions: deque[bool] = deque(maxlen=tower.policy.policy["guardian"]["drift_window"])
        self.runner: asyncio.Task | None = None

    # ---- derived -------------------------------------------------------------------
    @property
    def drift(self) -> float:
        return 0.0 if not self.decisions else round(1 - sum(self.decisions) / len(self.decisions), 2)

    @property
    def budget_pct(self) -> int:
        return min(100, int(100 * self.cost_usd / self.budget_usd)) if self.budget_usd else 0

    # ---- plumbing ------------------------------------------------------------------
    def emit(self, type: str, summary: str, severity: str = "info", **data) -> dict:
        return self.tower.bus.emit(type=type, summary=summary, severity=severity, actor=f"agent:{self.id}",
                                   tenant_id=self.tenant_id, agent_id=self.id,
                                   task_id=self.task.id if self.task else None, data=data)

    async def checkpoint(self) -> None:
        if not self.gate.is_set():
            await self.gate.wait()
        if self.abort_current:
            self.abort_current = False
            raise TaskAborted("task aborted by the control plane")

    async def pace(self, seconds: float | None = None) -> None:
        await asyncio.sleep(self.tower.step_delay if seconds is None else seconds)
        await self.checkpoint()

    async def tool(self, tool_name: str, /, **args) -> dict:
        return await self.tower.gateway(self, tool_name, args)

    async def think(self, system: str, user: str, fallback, purpose: str) -> dict:
        await self.checkpoint()
        self.last_action = f"Thinking: {purpose}"
        res = await self.tower.llm.complete_json(system, user)
        if res.ok:
            data = res.data
            self._account(res.tokens_in, res.tokens_out, res.cost_usd)
            self.emit("agent.think", f"{purpose}: {str(data.get('rationale', ''))[:160]}",
                      provider=res.provider, model=res.model, tokens_in=res.tokens_in,
                      tokens_out=res.tokens_out, cost_usd=round(res.cost_usd, 6), latency_ms=res.latency_ms,
                      prompt=user[:1200], output=data)
        else:
            if self.tower.llm.mode == "live":
                self.blocker = f"LLM unavailable ({res.error})"
                self.emit("agent.blocked", f"No LLM provider available: {res.error}", "warn")
                raise TaskAborted(res.error)
            data = fallback()
            self.emit("agent.think", f"{purpose} (scripted brain): {data.get('rationale', '')[:160]}",
                      provider="scripted", model="scripted", tokens_in=0, tokens_out=0, cost_usd=0,
                      fallback_reason=res.error, prompt=user[:1200], output=data)
        if self.task:
            self.task.llm_calls += 1
        await self.pace()
        return data

    def _account(self, tin: int, tout: int, cost: float) -> None:
        self.tokens_in += tin; self.tokens_out += tout; self.cost_usd += cost
        if self.task:
            self.task.tokens_in += tin; self.task.tokens_out += tout; self.task.cost_usd += cost
        if self.cost_usd >= self.budget_usd and self.status not in {"paused", "quarantined", "killed"}:
            self.tower.pause(self.id, by="tower", reason=f"budget exhausted (${self.cost_usd:.4f})")

    def record_decision(self, choice: str, work: dict) -> None:
        expected = self.tower.policy.baseline(self, work)
        self.tower.guardian.decision(self, choice, expected)

    # ---- lifecycle -----------------------------------------------------------------
    async def run(self) -> None:
        self.status = "running"
        while True:
            try:
                await self.checkpoint()
            except TaskAborted:
                continue
            if not self.tower.active and not self.tower.world.forced.get(self.id):
                if self.status == "running":
                    self.status, self.last_action = "idle", "Idle: no operator watching (saves free LLM quota)"
                await asyncio.sleep(2)
                continue
            if self.status == "idle":
                self.status = "running"
            work = self.tower.world.next_work(self)
            self._begin(work)
            try:
                await self.handle(work)
                self._end("done")
            except ToolDenied as exc:
                self._end("blocked", str(exc))
            except TaskAborted as exc:
                self._end("aborted", str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # an agent bug must surface on the tower, never kill the runner silently
                self.emit("agent.error", f"Unhandled {type(exc).__name__}: {exc}", "critical")
                self._end("failed", type(exc).__name__)
            gap = self.tower.task_gap * random.uniform(0.7, 1.3)
            await asyncio.sleep(gap)

    def _begin(self, work: dict) -> None:
        self.task = Task(f"T-{next(_task_ids):05d}", work["title"], work["ref"])
        self.tasks.appendleft(self.task)
        self.tainted, self.abort_current, self.blocker = False, False, ""
        self.last_action = f"Started {work['title']}"
        safe = {k: v for k, v in work.items() if k not in {"title"}}
        self.emit("task.started", work["title"], work=safe)

    def _end(self, status: str, outcome: str = "") -> None:
        if not self.task:
            return
        t = self.task
        t.status, t.ended = status, time.time()
        t.outcome = t.outcome or outcome
        if status == "done":
            self.tasks_done += 1
        sev = "info" if status == "done" else "warn"
        self.emit("task.completed", f"{t.title} → {status}{': ' + t.outcome if t.outcome else ''}", sev,
                  status=status, tokens_in=t.tokens_in, tokens_out=t.tokens_out,
                  cost_usd=round(t.cost_usd, 6), duration_s=t.duration_s)
        self.last_action = f"{status.title()}: {t.title}"
        self.task = None

    async def handle(self, work: dict) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def j(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)
