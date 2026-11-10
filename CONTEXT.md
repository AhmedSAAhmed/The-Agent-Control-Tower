# Agent Control Tower - the pipeline

The flow in one line: pin the brief, build the control plane and cockpit, prove it contains a rogue agent, ship it.

| Stage | Job |
|---|---|
| `01_spec` | Brief, agents, tenants, success criteria |
| `02_build` | Runtime code in `app/` |
| `03_verify` | Tests and drills pass |
| `04_ship` | Render deploy, submission docs |

Each stage's own `CONTEXT.md` holds its inputs, outputs and check.

Factory (stable, every run): `_shared/` (domain, risk policy notes, event schema, theme) and `app/policy.yaml`.
Product (new each run): `01_spec/output/`, `app/`, `03_verify/output/check.md`, `04_ship/output/`.

Status is whatever exists: a stage is complete when its output exists and any `check.md` there says pass.

ICM deliberately stops at the runtime boundary: real-time multi-agent orchestration needs framework
code (asyncio workers, an event bus, a gateway), so `app/` is ordinary Python, documented by
`02_build/CONTEXT.md` instead of being split into stage folders.
