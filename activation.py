"""Persistent, dependency-aware activation events. No telecom API is fabricated."""
from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from pydantic import BaseModel, Field

from planner import _dependencies


class TaskEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=100)
    task_id: str = Field(min_length=1, max_length=100)
    action: Literal["complete", "delay", "fail", "retry"]
    expected_version: int = Field(ge=0)
    evidence: str = Field(min_length=5, max_length=500)
    delay_days: int = Field(default=0, ge=0, le=365)


def init_tables(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS decision_plans (
            order_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, access_hash TEXT NOT NULL,
            accepted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activation_tasks (
            order_id TEXT NOT NULL, id TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(order_id,id)
        );
        CREATE TABLE IF NOT EXISTS activation_events (
            order_id TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(order_id,event_id)
        );
        CREATE TABLE IF NOT EXISTS plan_revisions (
            order_id TEXT NOT NULL, fingerprint TEXT NOT NULL, plan_json TEXT NOT NULL,
            tasks_json TEXT NOT NULL, archived_at TEXT NOT NULL,
            PRIMARY KEY(order_id,fingerprint)
        );
    """)


def seed(c, order_id: str, items: list[dict]):
    def task(tid, label, deps, days, site="business", product=None, kind="provision"):
        data = dict(id=tid, label=label, dependencies=deps, days=days, site_id=site, product_id=product, kind=kind, status="pending", version=0, delay_days=0, evidence=None, source=None)
        c.execute("INSERT INTO activation_tasks VALUES (?,?,?)", (order_id, tid, json.dumps(data)))
    task("verification", "Business and order identity verified", [], 1, kind="verification")
    scoped = []
    for i in items:
        for n in range(i["qty"] if "site_id" not in i and i["unit_type"] == "per site" else 1):
            site = i.get("site_id", f"site-{n+1}")
            scoped.append((site, i))
    for site, i in scoped:
        base = f"{site}:{i['id']}"
        deps = ["verification"]
        required = _dependencies(i["id"])
        partner = next((x for s, x in scoped if s == site and x["id"] in required), None)
        if partner:
            deps.append(f"{site}:{partner['id']}:check")
        if required and not partner:
            # Legacy quotes can have per-account products lacking a site label.
            partners = [f"{s}:{x['id']}:check" for s, x in scoped if x["id"] in required]
            deps.extend(partners)
        if i["id"] in ("P01", "P02", "P03", "P04", "P05", "P06"):
            task(base+":feasibility", f"{i['name']}: site feasibility", deps, 1, site, i["id"], "feasibility")
            deps = [base+":feasibility"]
        task(base+":provision", f"{i['name']}: install / configure", deps, max(1, i["days"]), site, i["id"])
        task(base+":check", f"{i['name']}: useful-service check", [base+":provision"], 1, site, i["id"], "check")


def snapshot(c, order_id: str, start: str) -> dict:
    tasks = [json.loads(r[0]) for r in c.execute("SELECT data_json FROM activation_tasks WHERE order_id=? ORDER BY id", (order_id,))]
    by = {t["id"]: t for t in tasks}
    today = dt.date.today()
    anchor = max(today, dt.date.fromisoformat(start[:10]))
    memo = {}

    def forecast(tid):
        if tid in memo:
            return memo[tid]
        task = by[tid]
        if task["status"] == "complete":
            out = dt.date.fromisoformat(task["completed_at"][:10])
        elif task["status"] == "failed":
            out = None
        else:
            dates = [forecast(d) for d in task["dependencies"]]
            if any(d is None for d in dates):
                out = None
            else:
                out = max([anchor] + dates) + dt.timedelta(days=task["days"] + task["delay_days"])
        memo[tid] = out
        return out

    for task in tasks:
        blocked = [d for d in task["dependencies"] if by[d]["status"] != "complete"]
        task["blocked_by"] = blocked
        task["can_complete"] = not blocked and task["status"] in ("pending", "delayed")
        date = forecast(task["id"])
        task["forecast"] = str(date) if date else None
    failed = [t for t in tasks if t["status"] == "failed"]
    checks = [t for t in tasks if t["kind"] == "check"]
    events = [dict(event_id=r[0], **json.loads(r[1]), recorded_at=r[2]) for r in c.execute("SELECT event_id,payload_json,created_at FROM activation_events WHERE order_id=? ORDER BY created_at DESC,event_id DESC LIMIT 30", (order_id,))]
    return dict(tasks=tasks, events=events, complete=bool(checks) and all(t["status"] == "complete" for t in checks), completed_services=sum(t["status"] == "complete" for t in checks), total_services=len(checks), forecast=max((t["forecast"] for t in checks), default=None) if all(t["forecast"] for t in checks) else None, forecast_type="estimate; not a confirmed appointment", provisioning="simulation / operator-recorded evidence; Airtel API not connected", alternatives=[dict(site_id=t["site_id"], failed_product=t["product_id"], suggestion="Review AirFiber feasibility and catalogue dependencies. A new quote and customer acceptance are required before changing product, cost or contract.", changes_applied=False) for t in failed if t["product_id"] in ("P01", "P02", "P03", "P05", "P06")])


def record(c, order_id: str, event: TaskEvent, source: str = "simulation", actor: str | None = None) -> bool:
    """Transactional caller, optimistic concurrency and replay protection."""
    body = event.model_dump()
    body.pop("event_id")
    body["source"] = source
    if actor:
        body["actor"] = actor
    encoded = json.dumps(body, sort_keys=True)
    duplicate = c.execute("SELECT payload_json FROM activation_events WHERE order_id=? AND event_id=?", (order_id, event.event_id)).fetchone()
    if duplicate:
        if duplicate[0] != encoded:
            raise ValueError("Event ID was already used with different content")
        return False
    row = c.select_for_update("SELECT data_json FROM activation_tasks WHERE order_id=? AND id=?", (order_id, event.task_id)).fetchone()
    if not row:
        raise ValueError("Unknown activation task")
    task = json.loads(row[0])
    if task["version"] != event.expected_version:
        raise ValueError("Task changed; refresh before submitting another event")
    if task["status"] == "complete":
        raise ValueError("Completed tasks are immutable; open a new service incident")
    if task["kind"] == "verification" and source not in ("mock_registry_bound", "operator_verified", "demo_only"):
        raise ValueError("Business verification must come from the bound KYC workflow")
    if event.action == "complete":
        for dep in task["dependencies"]:
            other = c.execute("SELECT data_json FROM activation_tasks WHERE order_id=? AND id=?", (order_id, dep)).fetchone()
            if not other or json.loads(other[0])["status"] != "complete":
                raise ValueError("Prerequisite tasks must complete first")
        if task["status"] == "failed":
            raise ValueError("Retry the failed task before completing it")
        task["status"] = "complete"
        task["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    elif event.action == "delay":
        if event.delay_days < 1 or task["status"] == "failed":
            raise ValueError("A delay needs at least one day and a non-failed task")
        task.update(status="delayed", delay_days=event.delay_days)
    elif event.action == "fail":
        task["status"] = "failed"
    else:
        if task["status"] not in ("failed", "delayed"):
            raise ValueError("Only failed or delayed tasks can be retried")
        task.update(status="pending", delay_days=0)
    task.update(version=task["version"]+1, evidence=event.evidence, source=source)
    if actor:
        task["actor"] = actor
    c.execute("UPDATE activation_tasks SET data_json=? WHERE order_id=? AND id=?", (json.dumps(task), order_id, event.task_id))
    c.execute("INSERT INTO activation_events VALUES (?,?,?,?)", (order_id, event.event_id, encoded, dt.datetime.now(dt.timezone.utc).isoformat()))
    return True


def replace_tasks(c, order_id: str, items: list[dict]):
    """Preserve unchanged tasks and evidence after explicit revised acceptance."""
    old = {r[0]: json.loads(r[1]) for r in c.execute("SELECT id,data_json FROM activation_tasks WHERE order_id=?", (order_id,))}
    c.execute("DELETE FROM activation_tasks WHERE order_id=?", (order_id,))
    seed(c, order_id, items)
    for row in c.execute("SELECT id,data_json FROM activation_tasks WHERE order_id=?", (order_id,)).fetchall():
        tid, new = row[0], json.loads(row[1])
        if tid in old and old[tid]["dependencies"] == new["dependencies"]:
            c.execute("UPDATE activation_tasks SET data_json=? WHERE order_id=? AND id=?", (json.dumps(old[tid]), order_id, tid))
        elif tid in old:
            new["version"] = old[tid]["version"] + 1
            c.execute("UPDATE activation_tasks SET data_json=? WHERE order_id=? AND id=?", (json.dumps(new), order_id, tid))
