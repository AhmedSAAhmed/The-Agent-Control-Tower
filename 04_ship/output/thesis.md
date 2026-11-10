# Agent operations is the new SRE, and its unit is the action, not the model

In two years the hard problem will not be making agents capable. It will be letting them act on
systems that matter without a human reading every transcript. The discipline that solves this will
look like SRE did for services: budgets, blast radius, circuit breakers, on-call, postmortems.
Its unit of control is the action, not the model and not the prompt.

Three convictions follow.

**Agents should hold no credentials.** Every side effect should be a request to a gateway the
operator owns. Then pause and kill are enforced, not requested: a revoked agent is harmless even if
its model keeps trying. Prompt filters help; they are not a control plane.

**Approval must be structural.** Humans cannot review everything, so policy decides what needs them:
amount, novelty, blast radius, tainted input. The agent waits inside its own call;
no code path continues without a decision. Undecided approvals fail closed. The craft is tuning thresholds so
humans see the 2% that matters and trust the rest.

**The audit log is the product.** Regulators will ask what an agent did,
why, and who allowed it. Replay of reasoning, tamper-evident event chains and per-tenant export will
be table stakes, the way access logs became table stakes for cloud.

By 2028 fleets span vendors and models, so the control plane must be model-agnostic and sit at
the tool boundary. Drift becomes a first-class metric, measured against
deterministic policy baselines. Operators manage agents the way SREs manage
services: by SLOs on outcomes (resolution rate, refund leakage, false holds), with error budgets
that automatically narrow an agent's autonomy when it misbehaves.

The company that owns this control surface owns AI operations. For an Arabic-first platform serving
banks, telcos and governments, it is also the trust story that wins the regulated deal.
