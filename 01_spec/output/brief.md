# Spec - the Agent Control Tower for DOO

Challenge: "How do humans manage an AI workforce when agents can act across real systems?"
Build the cockpit that lets a human operate, audit and intervene on a fleet of agents in real time.

## Required features → where they live

| Brief asks for | Built as |
|---|---|
| Live fleet: status, recent actions, blockers, drift | Fleet page cards (status, current task, last action, blocker, drift %), SSE-driven |
| Approval queue for risky actions | Gateway parks the call as a future; queue sorted by risk; approve / deny with replay link |
| Per-agent kill switch | Kill revokes credentials, cancels the worker, voids its approvals; restart from a clean image |
| Cost and token usage per agent per task | Costs page + per-task table on each agent page; budgets auto-pause |
| Replay reasoning for the last N steps | Agent page replay: N selector, step player, prompt / output / tool args / verdict per step |
| At least 3 simulated agents doing different jobs | KYC onboarding, withdrawals, customer support (x2 tenants) = 4 agents, 3 jobs |

## Deliverables

| Deliverable | Where |
|---|---|
| Working build + live demo | `app/`, Render (`render.yaml`) |
| 90-second Loom | recorded by the owner; script kept local only |
| Architecture snapshot | `/architecture` page and `../../04_ship/output/architecture.md` |
| Failure test: rogue agent caught and contained | Prompt-injection drill + `tests/test_tower.py::test_rogue_agent_is_contained_with_zero_egress` |
| Two-year thesis (≤300 words) | `../../04_ship/output/thesis.md` |
| Bonus: multi-tenant | 3 tenants, tenant on every event, per-tenant policy, tenant-scoped views and stop |
| Bonus: exportable audit log | CSV / JSON export with SHA-256 chain proof, Verify button |

## Agents

| Agent | Tenant | Job | Risky because |
|---|---|---|---|
| Noor | Pearl Bank | KYC onboarding | onboarding a PEP or sanctioned person is a regulatory breach |
| Faisal | Pearl Bank | Withdrawals | money leaves the bank; new beneficiaries are the classic fraud path |
| Layla | Dilmun Telecom | Customer support (AR/EN) | refunds, billing disputes |
| Saad | Rimal Eats | Customer support (AR/EN) | inbound text is untrusted: prompt-injection target |

## Drills

| Drill | Expected proof |
|---|---|
| Prompt-injection hijack (Saad) | guard.injection, 2 critical denials, quarantine, 0 bytes egress |
| Runaway loop (Layla) | rate breaker quarantine after >12 calls in 30 s |
| Large payout to new beneficiary (Faisal) | approval request with amount + new-beneficiary reasons |
| PEP applicant (Noor) | approval request, human decision in audit log |

## Scoring weights (from the brief)
Conceptual clarity 25 · Technical depth 25 · Demo quality 20 · Failure thinking 15 · Future thesis 15.
Disqualifiers: read-only dashboard, no running agents, no demo.
