# 02_build - the runtime in app/

One job: change the control plane, agents or cockpit without breaking the invariants below.

## Inputs
- Working (this run): `../01_spec/output/brief.md`
- Reference (every run): `../_shared/event-schema.md`
- Reference (every run): `../_shared/risk-policy.md`
- Reference (every run): `../_shared/theme.md`

Do NOT load: `../_local/`, test fixtures, the whole `app/templates/` folder unless the change is visual.

## Map of app/

| File | Role |
|---|---|
| `app/main.py` | FastAPI: pages, HTMX partials, intervention POSTs, `/stream` SSE, audit export |
| `app/tower.py` | Control plane: fleet, tool gateway, pause/resume/kill/restart, quarantine, drills, KPIs |
| `app/control/policy.py` | Per-call verdict: allow / approve / deny, plus the drift baseline |
| `app/control/guardian.py` | Cross-call behaviour: strikes, rate, drift → quarantine |
| `app/control/approvals.py` | Approval queue (futures, TTL, fail closed) |
| `app/events.py` | SQLite event store (SHA-256 chain) + in-process bus |
| `app/llm.py` | Groq → Gemini → OpenRouter router, fleet rate cap, Prompt Guard scan |
| `app/agents/` | Agent runtime (`base.py`) and the three jobs (`workers.py`) |
| `app/sim/world.py` | Fictional systems, work generator, the rogue ticket |
| `app/policy.yaml` | Tenants, agents, scopes, thresholds, guardian limits |

## Process
1. Keep every agent side effect behind `Tower.gateway`.
2. Emit an event for every state change; operator actions use actor `operator:<name>`.
3. Put new thresholds in `app/policy.yaml`, not in code.
4. Run `python -m pytest -q`.

## Outputs
- Code changes in `../app/`

## Check
`python -m pytest -q` passes and `python -m uvicorn app.main:app` serves `/` with live events.
