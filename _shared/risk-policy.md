# Risk policy - how a tool call is judged

Source of truth: `app/policy.yaml` (values) and `app/control/policy.py` (rules). This note explains
the model; it does not repeat the numbers.

## Two layers

1. **Policy engine, per call.** Every tool call gets one verdict:
   - `deny` (security, always first, *critical*): out of scope, egress to a non-allowlisted host,
     bulk PII export, an agent changing its own limits or permissions. Also non-critical denials for
     budget exhausted and hard caps.
   - `approve` (a human decides): payouts above the tenant threshold or to a beneficiary younger than
     the tenant window, payouts the agent itself flagged, PEP or high-risk KYC approvals, refunds
     above threshold, and any write action on a task whose input Prompt Guard flagged.
   - `allow` otherwise.
2. **Guardian, across calls.** Quarantines an agent on 2 critical strikes or 3 denials inside the
   strike window, or on a tool-call rate above the runaway limit. Raises a drift alert when an
   agent's decisions disagree with the policy baseline too often.

## Containment

Quarantine = credentials revoked at the gateway + worker frozen at its next checkpoint + pending
approvals voided + incident opened. A human releases (frozen task discarded, fresh credentials) or
kills. Approvals that nobody decides expire and **fail closed**.

## Why approval is a future, not a flag

The agent awaits the human's decision inside its own call stack. There is no code path that can
"continue anyway"; the side effect happens only after `approved` resolves the future.
