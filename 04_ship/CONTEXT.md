# 04_ship - deploy and submit

One job: get a live URL anyone can open and the written deliverables the brief asks for.

## Inputs
- Working (this run): `../03_verify/output/check.md`
- Reference (every run): `../render.yaml`

## Process
1. Stop if `check.md` does not start with `pass`.
2. Push to GitHub; on Render create a Blueprint from `render.yaml`, then set the three API keys.
3. Open `/healthz`, then `/`, and run the prompt-injection drill on the live URL.
4. Fill the submission form from `output/submission-notes.md`.

## Outputs
- `architecture.md`, `thesis.md`, `submission-notes.md` -> `output/`

## Check
A person opens the live URL in a private window, runs one drill and one approval without help.
