import json
from fastapi import Depends, HTTPException, Query
from ..auth import can_access
from .models import Acknowledge, Rule, RuleInput, RuleUpdate


def register(app, current_user, admin):
    def scope(user):
        if "admin" in user["roles"] or user["device_ids"] is None:
            return "1=1", []
        ids = user["device_ids"]
        return (
            ("device_id IN (" + ",".join("?" for _ in ids) + ")", list(ids)) if ids else ("0=1", [])
        )

    async def visible(alert_id, user):
        rows = await app.state.storage.rows("SELECT data FROM alerts WHERE id=?", (alert_id,))
        if not rows:
            raise HTTPException(404, "Alert not found")
        data = json.loads(rows[0]["data"])
        if not can_access(user, data["device_id"]):
            raise HTTPException(404, "Alert not found")
        return data

    @app.get("/api/alerts/summary")
    async def summary(user=Depends(current_user)):
        where, args = scope(user)
        rows = await app.state.storage.rows(
            "SELECT status,COUNT(*) AS count FROM alerts WHERE " + where + " GROUP BY status", args
        )
        counts = {s: 0 for s in ("ACTIVE", "ACKNOWLEDGED", "RESOLVED")}
        counts.update({r["status"]: r["count"] for r in rows})
        return {"counts": counts, "engine_status": "error" if app.state.alerts.last_error else "ok"}

    @app.get("/api/alerts")
    async def listing(
        status: str = "open",
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(current_user),
    ):
        where, args = scope(user)
        if status == "open":
            where += " AND status!='RESOLVED'"
        elif status in {"ACTIVE", "ACKNOWLEDGED", "RESOLVED"}:
            where += " AND status=?"
            args.append(status)
        elif status != "all":
            raise HTTPException(422, "Invalid alert status")
        rows = await app.state.storage.rows(
            "SELECT data FROM alerts WHERE "
            + where
            + " ORDER BY opened_at DESC,id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return [json.loads(r["data"]) for r in rows]

    @app.get("/api/alerts/{alert_id}")
    async def detail(alert_id: str, user=Depends(current_user)):
        return await visible(alert_id, user)

    @app.get("/api/alerts/{alert_id}/history")
    async def history(alert_id: str, user=Depends(current_user)):
        await visible(alert_id, user)
        rows = await app.state.storage.rows(
            "SELECT occurred_at,data FROM alert_history WHERE alert_id=? ORDER BY id", (alert_id,)
        )
        return [{"occurred_at": r["occurred_at"], **json.loads(r["data"])} for r in rows]

    @app.post("/api/alerts/{alert_id}/ack")
    async def acknowledge(alert_id: str, data: Acknowledge, user=Depends(current_user)):
        await visible(alert_id, user)
        if not set(user["roles"]) & {"admin", "operator", "audio", "video", "network"}:
            raise HTTPException(403, "Operator role required")
        return await app.state.alerts.acknowledge(
            alert_id, data.revision, user["id"], data.comment, user["username"]
        )

    @app.get("/api/alert-rule-catalog")
    async def catalog(user=Depends(admin)):
        keys = {("status", "status", None), ("status", "connection_status", None)}
        for state in app.state.manager.states.values():
            keys.update(("metric", m.key, m.unit) for m in state.metrics)
            keys.update(("parameter", key, None) for key in state.parameters)
        return [
            {"source": source, "key": key, "unit": unit}
            for source, key, unit in sorted(
                keys, key=lambda item: (item[0], item[1], item[2] or "")
            )
        ]

    @app.get("/api/alert-rules")
    async def rules(user=Depends(admin)):
        return list(app.state.alerts.rules.values())

    @app.post("/api/alert-rules", status_code=201)
    async def create(data: RuleInput, user=Depends(admin)):
        return await app.state.alerts.configure(Rule(**data.model_dump()), user["id"])

    @app.put("/api/alert-rules/{rule_id}")
    async def edit(rule_id: str, data: RuleUpdate, user=Depends(admin)):
        if rule_id not in app.state.alerts.rules:
            raise HTTPException(404, "Rule not found")
        return await app.state.alerts.configure(Rule(id=rule_id, **data.model_dump()), user["id"])
