"""HTTP surface: operator pages, HTMX partials, intervention endpoints, SSE stream, audit export."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import APP_DIR
from .tower import Tower

tower: Tower | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tower
    tower = Tower()
    await tower.start()
    yield
    await tower.stop()


app = FastAPI(title="Agent Control Tower", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

STATUS_LABEL = {"running": "Running", "idle": "Idle", "paused": "Paused", "waiting_approval": "Needs approval",
                "quarantined": "Quarantined", "killed": "Killed", "starting": "Starting"}


def _ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    return f"{s}s ago" if s < 60 else f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


templates.env.filters["ago"] = _ago
templates.env.filters["clock"] = _clock
templates.env.filters["usd"] = lambda v: f"${v:,.4f}"
templates.env.filters["num"] = lambda v: f"{v:,}"
templates.env.filters["pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2, default=str)
templates.env.globals["STATUS_LABEL"] = STATUS_LABEL


def operator(request: Request) -> str:
    return request.cookies.get("op") or "operator"


def tenant(request: Request) -> str | None:
    qp = request.query_params
    t = qp.get("t", "") if "t" in qp else request.cookies.get("t", "")
    return t if t in tower.policy.tenants else None


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    t = tenant(request)
    ctx.update(request=request, tower=tower, op=operator(request), tenant_id=t, tq=f"?t={t or ''}",
               tenants=tower.policy.tenants, task=request.query_params.get("task", ""))
    return templates.TemplateResponse(request, name, ctx)


def done() -> Response:
    """Interventions answer 204 and fire an HTMX 'refresh' so every panel redraws at once."""
    return Response(status_code=204, headers={"HX-Trigger": "refresh"})


# ---- pages ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def fleet_page(request: Request):
    return render(request, "fleet.html", page="fleet")


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_page(request: Request, agent_id: str):
    agent = tower.agents.get(agent_id) or _404()
    return render(request, "agent.html", page="fleet", agent=agent)


@app.get("/incidents", response_class=HTMLResponse)
async def incidents_page(request: Request):
    return render(request, "incidents.html", page="incidents")


@app.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request):
    return render(request, "costs.html", page="costs")


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    return render(request, "audit.html", page="audit")


@app.get("/architecture", response_class=HTMLResponse)
async def architecture_page(request: Request):
    return render(request, "architecture.html", page="architecture")


def _404():
    raise HTTPException(404)


# ---- partials (HTMX) ------------------------------------------------------------------
@app.get("/p/kpis", response_class=HTMLResponse)
async def p_kpis(request: Request):
    return render(request, "partials/kpis.html", k=tower.kpis(tenant(request)))


@app.get("/p/fleet", response_class=HTMLResponse)
async def p_fleet(request: Request):
    return render(request, "partials/fleet.html", agents=tower.fleet(tenant(request)))


@app.get("/p/approvals", response_class=HTMLResponse)
async def p_approvals(request: Request):
    t = tenant(request)
    return render(request, "partials/approvals.html", pending=tower.approvals.pending(t),
                  recent=tower.approvals.recent(t, 5))


@app.get("/p/incidents", response_class=HTMLResponse)
async def p_incidents(request: Request):
    t = tenant(request)
    rows = sorted((i for i in tower.incidents.values() if not t or i.tenant_id == t), key=lambda i: -i.opened)
    full = request.query_params.get("full") == "1"
    evidence = {i.id: tower.store.query(agent_id=i.agent_id, min_severity="warn", limit=12) for i in rows} if full else {}
    return render(request, "partials/incidents.html", incidents=rows, full=full, evidence=evidence)


@app.get("/p/agent/{agent_id}", response_class=HTMLResponse)
async def p_agent(request: Request, agent_id: str):
    agent = tower.agents.get(agent_id) or _404()
    return render(request, "partials/agent_header.html", a=agent)


@app.get("/p/replay/{agent_id}", response_class=HTMLResponse)
async def p_replay(request: Request, agent_id: str, n: int = 25, task: str = ""):
    tower.agents.get(agent_id) or _404()
    n = max(5, min(n, 200))
    steps = tower.store.query(agent_id=agent_id, task_id=task or None, limit=n)
    steps.reverse()
    return render(request, "partials/replay.html", steps=steps, n=n, task=task, agent_id=agent_id)


@app.get("/p/tasks/{agent_id}", response_class=HTMLResponse)
async def p_tasks(request: Request, agent_id: str):
    agent = tower.agents.get(agent_id) or _404()
    return render(request, "partials/tasks.html", a=agent)


@app.get("/p/costs", response_class=HTMLResponse)
async def p_costs(request: Request):
    t = tenant(request)
    agents = tower.fleet(t)
    tasks = sorted((tk for a in agents for tk in a.tasks), key=lambda tk: -tk.started)[:40]
    owner = {tk.id: a for a in agents for tk in a.tasks}
    by_tenant = {}
    for a in agents:
        row = by_tenant.setdefault(a.tenant_id, {"cost": 0.0, "tokens": 0, "tasks": 0})
        row["cost"] += a.cost_usd; row["tokens"] += a.tokens_in + a.tokens_out; row["tasks"] += a.tasks_done
    return render(request, "partials/costs.html", agents=agents, tasks=tasks, owner=owner, by_tenant=by_tenant)


@app.get("/p/audit", response_class=HTMLResponse)
async def p_audit(request: Request, sev: str = ""):
    rows = tower.store.query(tenant_id=tenant(request), min_severity=sev or None, limit=150)
    return render(request, "partials/audit_rows.html", rows=rows)


@app.get("/p/verify", response_class=HTMLResponse)
async def p_verify(request: Request):
    return render(request, "partials/verify.html", v=tower.store.verify_chain())


# ---- interventions -------------------------------------------------------------------
@app.post("/agents/{agent_id}/{action}")
async def agent_action(request: Request, agent_id: str, action: str):
    if agent_id not in tower.agents:
        raise HTTPException(404)
    by = operator(request)
    {"pause": lambda: tower.pause(agent_id, by, "operator"), "resume": lambda: tower.resume(agent_id, by),
     "kill": lambda: tower.kill(agent_id, by), "restart": lambda: tower.restart(agent_id, by)}.get(
        action, lambda: _404())()
    return done()


@app.post("/approvals/{approval_id}/{verdict}")
async def approval_action(request: Request, approval_id: str, verdict: str, note: str = Form("")):
    if verdict not in {"approve", "deny"}:
        raise HTTPException(404)
    tower.decide(approval_id, verdict == "approve", operator(request), note[:200])
    return done()


@app.post("/incidents/{incident_id}/release")
async def incident_release(request: Request, incident_id: str):
    tower.release(incident_id, operator(request))
    return done()


@app.post("/incidents/{incident_id}/kill")
async def incident_kill(request: Request, incident_id: str):
    inc = tower.incidents.get(incident_id) or _404()
    tower.kill(inc.agent_id, operator(request), f"killed from {incident_id}")
    return done()


@app.post("/fleet/{action}")
async def fleet_action(request: Request, action: str):
    by, t = operator(request), tenant(request)
    if action == "pause-all":
        tower.pause_all(by, t)
    elif action == "resume-all":
        tower.resume_all(by, t)
    else:
        raise HTTPException(404)
    return done()


@app.post("/drills/{name}")
async def run_drill(request: Request, name: str):
    try:
        tower.drill(name, operator(request))
    except KeyError:
        raise HTTPException(404)
    return done()


@app.post("/settings")
async def settings(request: Request, op: str = Form(""), mode: str = Form("")):
    resp = done()
    if op:
        clean = re.sub(r"[^\w .-]", "", op)[:32].strip() or "operator"
        resp.set_cookie("op", clean, max_age=86400 * 30, samesite="lax")
    if mode:
        tower.set_mode(mode, operator(request))
    return resp


# ---- live stream -----------------------------------------------------------------------
def _sse(event: str, html: str) -> str:
    lines = html.replace("\r", "").split("\n")
    return f"event: {event}\n" + "".join(f"data: {ln}\n" for ln in lines) + "\n"


@app.get("/stream")
async def stream(request: Request):
    t = tenant(request)
    q = tower.bus.subscribe()
    row_tpl = templates.env.get_template("partials/event_row.html")

    async def gen():
        try:
            yield "retry: 3000\n\n"
            dirty, last_signal, last_beat = True, 0.0, time.monotonic()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    ev = None
                if ev and (not t or ev["tenant_id"] in (t, None)):
                    yield _sse("feed", row_tpl.render(e=ev, tenants=tower.policy.tenants))
                    dirty = True
                now = time.monotonic()
                if dirty and now - last_signal >= 1.0:   # coalesce: at most one panel refresh per second
                    yield _sse("fleet", str(int(now)))
                    dirty, last_signal = False, now
                if now - last_beat > 15:
                    yield ": keep-alive\n\n"
                    last_beat = now
        finally:
            tower.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- audit export (compliance) -----------------------------------------------------------
EXPORT_COLS = ["id", "ts", "iso_time", "tenant_id", "agent_id", "task_id", "type", "actor", "severity",
               "summary", "data", "prev_hash", "hash"]


def _export_rows(request: Request) -> list[dict]:
    rows = tower.store.query(tenant_id=tenant(request), limit=100_000, desc=False)
    for r in rows:
        r["iso_time"] = datetime.fromtimestamp(r["ts"], tz=timezone.utc).isoformat()
    return rows


@app.get("/audit/export.csv")
async def export_csv(request: Request):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLS, extrasaction="ignore")
    w.writeheader()
    for r in _export_rows(request):
        w.writerow({**r, "data": json.dumps(r["data"], ensure_ascii=False, sort_keys=True)})
    name = f"audit-{tenant(request) or 'all-tenants'}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/audit/export.json")
async def export_json(request: Request):
    rows = _export_rows(request)
    doc = {"exported_at": datetime.now(timezone.utc).isoformat(), "tenant": tenant(request) or "all",
           "chain": tower.store.verify_chain(), "hash_algorithm": "sha256(prev_hash + canonical_json(row))",
           "events": rows}
    name = f"audit-{tenant(request) or 'all-tenants'}.json"
    return Response(json.dumps(doc, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/state")
async def api_state(request: Request):
    t = tenant(request)
    return JSONResponse({
        "kpis": tower.kpis(t),
        "llm": {"mode": tower.llm.mode, "providers": tower.llm.available(), **tower.llm.stats},
        "agents": [{"id": a.id, "tenant": a.tenant_id, "kind": a.kind, "status": a.status,
                    "task": a.task.title if a.task else None, "blocker": a.blocker, "cost_usd": round(a.cost_usd, 6),
                    "tokens": a.tokens_in + a.tokens_out, "drift": a.drift} for a in tower.fleet(t)],
        "pending_approvals": [{"id": p.id, "agent": p.agent_id, "tool": p.tool, "risk": p.risk,
                               "reasons": p.reasons} for p in tower.approvals.pending(t)],
    })


@app.get("/healthz")
async def healthz():
    return {"ok": True, "events": tower.store.count(), "uptime_s": int(time.time() - tower.started)}
