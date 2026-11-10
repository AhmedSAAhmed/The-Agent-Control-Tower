# Agent Control Tower

An operator cockpit for a fleet of AI customer-service agents that act on real systems. Built for the
DOO builder challenge: watch the fleet live, approve risky actions, pause or kill any agent, replay its
reasoning, track cost per task, and see a rogue agent caught and contained.

- **Live demo:** _add Render URL_
- **Architecture:** [04_ship/output/architecture.md](04_ship/output/architecture.md) (also `/architecture` in the app)
- **Thesis:** [04_ship/output/thesis.md](04_ship/output/thesis.md)
- **Notes for reviewers:** [04_ship/output/submission-notes.md](04_ship/output/submission-notes.md)

## What runs

Four agents, three jobs, three fictional GCC tenants that mirror sectors DOO serves:

| Agent | Tenant | Job |
|---|---|---|
| Noor | Pearl Bank (BHD) | KYC onboarding: screening, approve / reject / escalate |
| Faisal | Pearl Bank (BHD) | Withdrawals: verify beneficiary, pay or flag |
| Layla | Dilmun Telecom (BHD) | Customer support in Gulf Arabic and English |
| Saad | Rimal Eats (SAR) | Customer support in Gulf Arabic and English |

Agents reason with free LLMs (Groq → Gemini → OpenRouter, scripted fallback) and act only through the
control plane's tool gateway, which allows, holds for a human, or blocks every call.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cp .env.example .env                            # optional: add free API keys
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000 and click a drill. Without keys the agents use their scripted brain, so a
clean clone always runs.

## Test

```bash
.venv/Scripts/python -m pytest -q
```

13 tests cover the policy verdicts, the hash chain detecting tampering, rogue-agent containment with
zero egress, the runaway-loop breaker, approvals blocking until a human decides, pause / resume / kill,
and tenant tagging.

## Deploy (Render free)

New → Blueprint → this repo. `render.yaml` defines the service; set `GROQ_API_KEY`,
`GEMINI_API_KEY` and `OPENROUTER_API_KEY` in the dashboard.

## Repository layout

The repo follows ICM (Interpretable Context Methodology): [AGENTS.md](AGENTS.md) routes, each
numbered stage folder has a `CONTEXT.md` contract, and `_shared/` holds the stable rules. Runtime code is in `app/`.
