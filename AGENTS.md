# Agent Control Tower - entry file

Operator cockpit for a fleet of AI customer-service agents (DOO builder challenge). The runtime is
Python framework code in `app/`; the folders around it follow ICM: numbered stages carry the build
order, `_shared/` holds the stable rules, and each folder states its own job in a `CONTEXT.md`.

## Where things live

| Need | Go to |
|---|---|
| The build pipeline and stage status | [CONTEXT.md](CONTEXT.md) |
| What the challenge asks for, the agents we run | [01_spec/CONTEXT.md](01_spec/CONTEXT.md) |
| Change runtime code | [02_build/CONTEXT.md](02_build/CONTEXT.md) |
| Run or add tests, the rogue-agent proof | [03_verify/CONTEXT.md](03_verify/CONTEXT.md) |
| Deploy to Render, submission docs | [04_ship/CONTEXT.md](04_ship/CONTEXT.md) |
| Domain facts: DOO, tenants, sectors | [_shared/doo-context.md](_shared/doo-context.md) |
| How risk policy works (source of truth is `app/policy.yaml`) | [_shared/risk-policy.md](_shared/risk-policy.md) |
| Event types and the audit chain | [_shared/event-schema.md](_shared/event-schema.md) |
| Visual language (Navy theme) | [_shared/theme.md](_shared/theme.md) |
| Blank starters: agent spec, drill, decision record | `_templates/` |
| Link checker | `python -I _tools/links.py . --skip _local` |

## Ground rules for any agent working here

1. Agents in `app/` act only through `Tower.gateway`; never add a path from an agent to `World` directly.
2. Every intervention emits an event; never mutate state without a matching `bus.emit`.
3. `app/policy.yaml` is the one home for thresholds, scopes and tenants. Docs link to it, never copy it.
4. `_local/` is local-only (reference repos, Loom script) and is git-ignored. Never commit it.
5. Commits carry no AI co-author trailer.
6. Run `python -m pytest -q` before you call a change done.
