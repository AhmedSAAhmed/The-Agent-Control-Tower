# 01_spec - pin down what we are building

One job: turn the challenge brief into a spec the build can be checked against.

## Inputs
- Reference (every run): `../_shared/doo-context.md`
- Reference (every run): `../app/policy.yaml`

## Process
1. Restate the brief's required features, deliverables and scoring weights.
2. Name each agent: job, tenant, tools in scope, what makes its actions risky.
3. Name the drills (failure tests) and the observable proof each one leaves.

## Outputs
- `brief.md` -> `output/`

## Check
A person compares `output/brief.md` with the challenge page: every "What you'll build" bullet and every deliverable maps to a feature or a doc.
