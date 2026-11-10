"""Guardian: watches behaviour across calls and trips the circuit breaker.

Policy judges one call at a time. The guardian judges the pattern: repeated security
denials, runaway call rates, and decisions drifting away from the policy baseline.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque


class Guardian:
    def __init__(self, cfg: dict, tower):
        self.cfg = cfg
        self.tower = tower
        self.calls: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.strikes: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.drift_alerted: set[str] = set()

    def call(self, agent) -> None:
        now = time.time()
        q = self.calls[agent.id]
        q.append(now)
        recent = [t for t in q if now - t <= 30]
        limit = self.cfg["max_tool_calls_per_30s"]
        if len(recent) > limit and agent.status != "quarantined":
            self.tower.quarantine(agent, f"runaway loop: {len(recent)} tool calls in 30s (limit {limit})",
                                  rule="rate")

    def strike(self, agent, verdict, event_id: int) -> None:
        now = time.time()
        q = self.strikes[agent.id]
        q.append((now, verdict.critical, event_id, verdict.rules))
        window = [s for s in q if now - s[0] <= self.cfg["strike_window_seconds"]]
        critical = [s for s in window if s[1]]
        if agent.status == "quarantined":
            return
        if len(critical) >= self.cfg["critical_strikes_to_quarantine"]:
            rules = sorted({r for s in critical for r in s[3]})
            self.tower.quarantine(agent, f"{len(critical)} security violations in "
                                  f"{self.cfg['strike_window_seconds']}s ({', '.join(rules)})",
                                  rule="strikes", evidence=[s[2] for s in critical])
        elif len(window) >= self.cfg["denials_to_quarantine"]:
            self.tower.quarantine(agent, f"{len(window)} denied actions in {self.cfg['strike_window_seconds']}s",
                                  rule="denials", evidence=[s[2] for s in window])

    def decision(self, agent, choice: str, expected: str) -> None:
        agent.decisions.append(choice == expected)
        if choice != expected:
            agent.emit("drift.sample", f"Decision '{choice}' differs from policy baseline '{expected}'", "info",
                       choice=choice, expected=expected)
        if len(agent.decisions) >= 5 and agent.drift >= self.cfg["drift_alert_ratio"]:
            if agent.id not in self.drift_alerted:
                self.drift_alerted.add(agent.id)
                agent.emit("drift.alert", f"{agent.name} disagrees with policy baseline on "
                           f"{int(agent.drift * 100)}% of recent decisions", "warn", drift=agent.drift)
        elif agent.id in self.drift_alerted and agent.drift < self.cfg["drift_alert_ratio"] / 2:
            self.drift_alerted.discard(agent.id)
