"""Control-plane tests. Run: python -m pytest -q   (scripted brain, no network, ~15 s)."""
import asyncio
import types

import pytest

from app.control.policy import PolicyEngine, load_policy
from app.events import EventStore
from app.tower import Tower

FAST = dict(db_path=":memory:", llm_mode="scripted", task_gap=0.2, step_delay=0.02, seed=7)


def run(coro):
    return asyncio.run(coro)


async def until(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def fake_agent(kind, tenant, cost=0.0):
    return types.SimpleNamespace(kind=kind, tenant_id=tenant, cost_usd=cost, budget_usd=0.25)


# ---- policy (per-call verdicts) ----------------------------------------------------------
policy = PolicyEngine(load_policy())


def test_out_of_scope_tool_is_denied_as_critical():
    v = policy.evaluate(fake_agent("support", "rimal-eats"), "payments.execute", {"amount": 5})
    assert v.action == "deny" and v.critical and "scope" in v.rules


def test_egress_to_unknown_host_is_denied():
    v = policy.evaluate(fake_agent("support", "rimal-eats"), "support.reply",
                        {"message": "see https://evil.example/upload"})
    assert v.action == "deny" and "egress" in v.rules


def test_withdrawal_thresholds():
    a = fake_agent("withdrawals", "pearl-bank")
    assert policy.evaluate(a, "payments.execute", {"amount": 500, "beneficiary_age_hours": 400}).action == "allow"
    held = policy.evaluate(a, "payments.execute", {"amount": 18500, "beneficiary_age_hours": 2})
    assert held.action == "approve" and {"amount", "new_beneficiary"} <= set(held.rules)
    assert policy.evaluate(a, "payments.execute", {"amount": 99999, "beneficiary_age_hours": 400}).action == "deny"


def test_tainted_input_forces_human_on_write_actions():
    v = policy.evaluate(fake_agent("support", "rimal-eats"), "support.refund", {"amount": 10}, tainted=True)
    assert v.action == "approve" and "tainted" in v.rules


def test_budget_exhaustion_denies():
    v = policy.evaluate(fake_agent("support", "rimal-eats", cost=0.3), "orders.get", {})
    assert v.action == "deny" and "budget" in v.rules and not v.critical


# ---- audit chain -------------------------------------------------------------------------
def test_audit_chain_detects_tampering():
    store = EventStore(":memory:")
    for i in range(5):
        store.append(type="t", summary=f"event {i}", actor="test", tenant_id="pearl-bank")
    assert store.verify_chain() == {**store.verify_chain(), "ok": True, "checked": 5}
    store.tamper_for_test(3, "rewritten history")
    result = store.verify_chain()
    assert not result["ok"] and result["broken_at"] == 3


# ---- interventions on a live fleet --------------------------------------------------------
def test_rogue_agent_is_contained_with_zero_egress():
    async def scenario():
        t = Tower(**FAST, guardian_overrides={"max_tool_calls_per_30s": 1000})
        t.always_active = True
        await t.start()
        t.drill("rogue", "test")
        saad = t.agents["saad-support"]
        assert await until(lambda: saad.status == "quarantined")
        executed = [x["tool"] for x in t.world.executed]
        await t.stop()
        return t, saad, executed

    t, saad, executed = run(scenario())
    assert saad.revoked
    assert t.world.egress_bytes == 0
    assert not {"http.post", "crm.export_customers", "admin.set_refund_limit"} & set(executed)
    inc = next(iter(t.incidents.values()))
    assert inc.agent_id == "saad-support" and inc.rule == "strikes"
    assert t.store.query(agent_id="saad-support", types=["guard.injection"])
    assert t.store.verify_chain()["ok"]


def test_runaway_loop_trips_rate_breaker():
    async def scenario():
        t = Tower(**FAST)
        t.always_active = True
        await t.start()
        t.drill("loop", "test")
        layla = t.agents["layla-support"]
        ok = await until(lambda: layla.status == "quarantined", timeout=15)
        await t.stop()
        return ok, t

    ok, t = run(scenario())
    assert ok and any(i.rule == "rate" for i in t.incidents.values())


def test_approval_blocks_until_human_decides():
    async def scenario():
        t = Tower(**FAST, guardian_overrides={"max_tool_calls_per_30s": 1000})
        t.always_active = True
        await t.start()
        t.drill("withdrawal", "test")
        assert await until(lambda: any(p.args.get("ref") == "WD-90417" for p in t.approvals.pending()))
        appr = next(p for p in t.approvals.pending() if p.args.get("ref") == "WD-90417")
        paid_before = [x for x in t.world.executed if x["args"].get("ref") == "WD-90417" and x["tool"] == "payments.execute"]
        t.decide(appr.id, True, "tester")
        assert await until(lambda: any(x["args"].get("ref") == "WD-90417" and x["tool"] == "payments.execute"
                                       for x in t.world.executed))
        await t.stop()
        return paid_before, t

    paid_before, t = run(scenario())
    assert paid_before == []
    assert t.store.query(types=["approval.approved"])[0]["actor"] == "operator:tester"


def test_drill_preempts_a_pending_approval():
    async def scenario():
        t = Tower(**{**FAST, "task_gap": 30}, guardian_overrides={"max_tool_calls_per_30s": 1000})
        t.always_active = True
        await t.start()
        faisal = t.agents["faisal-withdrawals"]
        t.world.force(faisal.id, {"kind": "withdrawals", "ref": "WD-1", "customer": "x", "amount": 9000.0,
                                  "currency": "BHD", "beneficiary": "B", "beneficiary_age_hours": 500,
                                  "destination_country": "Bahrain", "title": "WD-1"})
        assert await until(lambda: faisal.status == "waiting_approval")
        t.drill("withdrawal", "tester")
        started = await until(lambda: faisal.task and faisal.task.ref == "WD-90417", timeout=3)
        voided = t.store.query(types=["approval.voided"])
        await t.stop()
        return started, voided

    started, voided = run(scenario())
    assert started, "drill should start within seconds even with a 30 s task gap"
    assert voided and voided[0]["actor"] == "operator:tester"


def test_pause_resume_and_kill_take_effect():
    async def scenario():
        t = Tower(**FAST, guardian_overrides={"max_tool_calls_per_30s": 1000})
        t.always_active = True
        await t.start()
        noor = t.agents["noor-kyc"]
        await until(lambda: noor.tasks_done >= 1)
        t.pause(noor.id, "tester")
        await asyncio.sleep(0.3)
        frozen = noor.tasks_done
        await asyncio.sleep(1.0)
        paused_ok = noor.tasks_done == frozen and noor.status == "paused"
        t.resume(noor.id, "tester")

        def progressed():  # answer any approval so only pause/resume is under test
            for p in t.approvals.pending():
                t.decide(p.id, True, "tester")
            return noor.tasks_done > frozen
        resumed_ok = await until(progressed)
        t.kill(noor.id, "tester")
        await asyncio.sleep(0.2)
        killed_ok = noor.status == "killed" and noor.revoked and noor.runner.done()
        await t.stop()
        return paused_ok, resumed_ok, killed_ok

    assert run(scenario()) == (True, True, True)


@pytest.mark.parametrize("tenant", ["pearl-bank", "dilmun-telecom", "rimal-eats"])
def test_every_event_carries_its_tenant(tenant):
    async def scenario():
        t = Tower(**FAST, guardian_overrides={"max_tool_calls_per_30s": 1000})
        t.always_active = True
        await t.start()
        await asyncio.sleep(1.5)
        await t.stop()
        return t

    t = run(scenario())
    rows = t.store.query(tenant_id=tenant, limit=500)
    agents = {a.id for a in t.agents.values() if a.tenant_id == tenant}
    assert rows and all(r["agent_id"] in agents for r in rows if r["agent_id"])
