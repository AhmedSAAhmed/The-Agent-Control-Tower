# 03_verify - prove the tower holds

One job: show the control plane does what the spec promises, including the rogue-agent case.

## Inputs
- Working (this run): `../app/`
- Reference (every run): `../01_spec/output/brief.md`

## Process
1. Run `python -m pytest -q` from the repo root (scripted brain, no network).
2. Run the app, trigger each drill from the Fleet page, and confirm its proof from the spec's Drills table.
3. On the Audit page press Verify chain.

## Outputs
- `check.md` -> `output/` (first line `pass` or `fail: <why>`)

## Check
A script: pytest exit code 0. A person: the four drills leave the proof listed in the spec.
