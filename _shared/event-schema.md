# Event schema - the one stream everything writes to

Every row in `events` (SQLite) is also pushed to the SSE stream. Columns:
`id, ts, tenant_id, agent_id, task_id, type, actor, severity, summary, data (JSON), prev_hash, hash`.

`hash = sha256(prev_hash + canonical_json(id, ts, tenant_id, agent_id, task_id, type, actor, severity, summary, data))`,
genesis `prev_hash` is 64 zeros. Editing, deleting or reordering any row breaks `verify_chain()`.

## Actors
`agent:<id>` · `operator:<name>` (from the name box in the header) · `tower` (automatic control-plane actions).

## Types

| Family | Types |
|---|---|
| Task | `task.started`, `task.completed` |
| Reasoning | `agent.think` (provider, model, tokens, cost, prompt, output), `agent.observe`, `agent.blocked`, `agent.error` |
| Input guard | `guard.scan`, `guard.injection` |
| Gateway | `tool.executed`, `tool.denied` (rules, risk) |
| Approvals | `approval.requested`, `approval.approved`, `approval.denied`, `approval.expired`, `approval.voided` |
| Operator | `agent.paused`, `agent.resumed`, `agent.killed`, `agent.restarted`, `agent.budget_raised`, `system.mode` |
| Guardian | `guardian.quarantine`, `guardian.released`, `drift.sample`, `drift.alert` |
| System | `system.boot`, `drill.started` |

Replay for an agent is simply its events in order; nothing is reconstructed.
