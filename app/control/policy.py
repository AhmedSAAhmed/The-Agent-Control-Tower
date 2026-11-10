"""Policy engine: one verdict per tool call - allow, approve (needs a human) or deny."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import APP_DIR

URL_RE = re.compile(r"https?://([^/\s\"']+)", re.I)


@dataclass
class Verdict:
    action: str                     # allow | approve | deny
    risk: int = 0                   # 0-100
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    critical: bool = False          # a security violation, not just a business limit


def load_policy(path: Path | None = None) -> dict:
    with open(path or APP_DIR / "policy.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class PolicyEngine:
    def __init__(self, policy: dict):
        self.policy = policy
        self.tenants = policy["tenants"]
        self.scopes = {k: set(v) for k, v in policy["scopes"].items()}
        self.allow_hosts = set(policy["egress_allowlist"])

    def evaluate(self, agent, tool: str, args: dict, tainted: bool = False) -> Verdict:
        t = self.tenants[agent.tenant_id]
        v = Verdict("allow", risk=5)

        def deny(rule: str, reason: str, critical: bool = True, risk: int = 95):
            v.action, v.critical, v.risk = "deny", v.critical or critical, max(v.risk, risk)
            v.rules.append(rule); v.reasons.append(reason)

        def approve(rule: str, reason: str, risk: int):
            if v.action != "deny":
                v.action = "approve"
            v.risk = max(v.risk, risk)
            v.rules.append(rule); v.reasons.append(reason)

        # --- security rules (always first, always critical) ---
        if tool not in self.scopes.get(agent.kind, set()):
            deny("scope", f"'{tool}' is outside the {agent.kind} agent's scope")
        hosts = URL_RE.findall(" ".join(str(x) for x in args.values()))
        if tool.startswith("http.") or hosts:
            bad = [h for h in hosts if h.lower() not in self.allow_hosts]
            if bad or tool.startswith("http."):
                deny("egress", f"data egress to non-allowlisted host {', '.join(bad) or 'unknown'}")
        if tool.startswith(("admin.", "iam.")) or "limit" in tool:
            deny("privilege", "agent attempted to change its own permissions or limits")
        if "export" in tool or str(args.get("limit", "")).lower() in {"all", "*"}:
            deny("bulk_pii", "bulk export of customer PII")
        if agent.cost_usd >= agent.budget_usd:
            deny("budget", f"budget exhausted (${agent.cost_usd:.4f} of ${agent.budget_usd:.2f})",
                 critical=False, risk=60)

        if v.action == "deny":
            return v  # business limits only apply to calls that are otherwise legitimate

        # --- business rules ---
        if tool == "payments.execute":
            amount = float(args.get("amount", 0))
            if amount > t["withdrawal_hard_cap"]:
                deny("hard_cap", f"{amount:,.3f} {t['currency']} exceeds hard cap", critical=False, risk=90)
            elif amount > t["withdrawal_approval_over"]:
                approve("amount", f"{amount:,.3f} {t['currency']} is above the "
                        f"{t['withdrawal_approval_over']:,} approval threshold", 70)
            if args.get("agent_flag"):
                approve("agent_flag", f"agent flagged it: {args['agent_flag']}", 80)
            if float(args.get("beneficiary_age_hours", 9999)) < t["new_beneficiary_hours"]:
                approve("new_beneficiary", "beneficiary was added less than "
                        f"{t['new_beneficiary_hours']}h ago", 75)
        if tool == "kyc.decide" and args.get("decision") in {"approve", "escalate"}:
            if args.get("pep"):
                approve("pep", "applicant is a politically exposed person", 80)
            if int(args.get("risk_score", 0)) >= t.get("kyc_risk_approval_over", 101):
                approve("kyc_risk", f"risk score {args.get('risk_score')} needs a second pair of eyes", 72)
            if args.get("decision") == "escalate" and v.action == "allow":
                approve("escalate", "agent escalated the case to a human", 60)
        if tool == "support.refund":
            amount = float(args.get("amount", 0))
            if amount > t["refund_hard_cap"]:
                deny("hard_cap", f"refund {amount:,.0f} {t['currency']} exceeds hard cap", critical=False, risk=85)
            elif amount > t["refund_approval_over"]:
                approve("refund", f"refund {amount:,.0f} {t['currency']} is above the "
                        f"{t['refund_approval_over']} {t['currency']} threshold", 55)
        if tainted and v.action == "allow" and tool in {"support.refund", "payments.execute", "kyc.decide"}:
            approve("tainted", "input was flagged as a prompt injection; write actions need a human", 85)
        return v

    def baseline(self, agent, work: dict) -> str:
        """The deterministic answer policy expects. Used to measure LLM decision drift."""
        if agent.kind == "kyc":
            if work.get("sanctions") or work.get("docs") != "complete":
                return "reject"
            return "escalate" if work.get("pep") else "approve"
        if agent.kind == "withdrawals":
            new = work.get("beneficiary_age_hours", 9999) < self.tenants[agent.tenant_id]["new_beneficiary_hours"]
            return "hold" if new else "execute"
        amount = work.get("disputed_amount", 0)
        return "refund" if amount else "answer"
