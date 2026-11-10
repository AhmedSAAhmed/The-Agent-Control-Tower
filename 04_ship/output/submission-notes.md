# Submission notes (paste into the form)

**What to look at (3 minutes):**
1. Open https://agent-control-tower-f4vv.onrender.com. Four agents across three fictional tenants are working on real free-tier LLMs;
   the event stream at the bottom is live (SSE), not polled.
2. Click **Prompt-injection hijack**. Within ~15 s Saad (Rimal Eats support) receives a WhatsApp ticket
   hiding instructions to export customer data. Llama Prompt Guard flags it; the drill then simulates a
   model that obeys. Two calls are denied at the gateway and the guardian quarantines the agent:
   credentials revoked, 0 bytes egress. Open **Incidents** for the evidence chain, then Release or Kill.
3. Click **Large payout to new beneficiary**, then approve or deny it in the **Approval queue**. Use
   "Why? (replay)" to see what the agent saw and decided.
4. Pause, resume and kill any agent from its card. Open a card name for **Replay** (last N steps, step player).
5. **Costs** for tokens and cost per agent and per task. **Audit** to export CSV/JSON and verify the hash chain.
   Use the tenant selector to scope every view to one client.

**Cold start:** hosted on Render's free tier, which sleeps after 15 minutes idle. The first load can
take about a minute; after that it is instant. The fleet also idles when nobody has watched it for
10 minutes, to protect free LLM quotas, and resumes as soon as the page is open.

**Key decisions:**
- Agents hold no credentials; every side effect goes through one tool gateway, so pause, kill and
  quarantine are enforced rather than requested.
- Approvals are futures the agent awaits; undecided approvals expire and fail closed.
- Event sourcing: the audit log *is* the event stream, SHA-256 chained, so replay and compliance
  export come from the same data.
- Free LLM router (Groq gpt-oss-20b → Gemini 2.5 Flash-Lite → OpenRouter Gemma) with a fleet-wide rate
  cap and a scripted fallback, so the demo keeps running on free quotas. Costs are shown at paid-tier
  list prices.
- The rogue drill forces the hijack after the real model answers, because a containment test must be
  reproducible regardless of whether a given model happens to resist. The replay shows both.
- Python only: FastAPI + Jinja + HTMX + SSE, no front-end build step.

**AI tools used:** Claude Code (architecture, implementation, tests, docs); Firecrawl (Navy theme and
DOO context); Groq, Gemini and OpenRouter free tiers at runtime.

**Out of scope:** authentication and roles (open demo by design), persistent storage across restarts,
real integrations (systems are simulated), horizontal scaling.
