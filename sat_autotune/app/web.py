"""Ingress-webserver (poort 8099), alleen bereikbaar via de HA-proxy."""
from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web

from . import dashboard, metrics, tuner
from .const import ENT, LABELS, ROOMS
from .ha_client import HAError

log = logging.getLogger("web")
INGRESS_IP = "172.30.32.2"


def _fmt_changes(eng):
    out = []
    for c in eng.db.changes(50):
        out.append({
            "when": datetime.fromtimestamp(c["ts"], eng.tz).strftime("%d-%m-%Y %H:%M"),
            "label": LABELS.get(c["key"], c["field"]), "old": c["old"], "new": c["new"],
            "source": c["source"], "status": c["status"], "reason": c["reason"], "ts": c["ts"],
        })
    return out


async def _live(eng):
    live = {}
    try:
        for label, eid, unit in [("vlam", ENT["flame"], ""), ("aanvoer", ENT["flow"], " °C"),
                                 ("modulatie", ENT["modulation"], "%"), ("setpoint ketel", ENT["control_setpoint"], " °C")]:
            st = await eng.ha.get_state(eid)
            if st:
                live[label] = st["state"] + unit
        cl = await eng.ha.get_state(ENT["climate"])
        a = (cl or {}).get("attributes", {})
        if a.get("outside_temperature") is not None:
            live["buiten"] = f"{float(a['outside_temperature']):.1f}".replace(".", ",") + " °C"
        if a.get("temperature") is not None:
            live["setpoint SAT"] = f"{float(a['temperature']):.1f}".replace(".", ",") + " °C"
        for n, eid in ROOMS.items():
            st = await eng.ha.get_state(eid)
            if st:
                live[n] = st["state"] + " °C"
    except HAError:
        pass
    return live


async def index(request):
    eng = request.app["eng"]
    if request.remote not in (INGRESS_IP, "127.0.0.1", "::1"):
        return web.Response(status=403, text="Alleen via Home Assistant (Ingress)")
    newest = eng.db.reports(None, 1)
    reps = eng.db.reports("weekly", 12)
    latest = newest[0]["data"] if newest else None
    report_ts = datetime.fromtimestamp(newest[0]["ts"], eng.tz).strftime("%d-%m %H:%M") if newest else None
    hist = [r["data"] for r in reps]
    status = list(eng.status)
    ph = tuner.phase(__import__("time").time(), eng.db.get("install_ts"), eng.cfg)
    status.insert(0, f"Fase: {'dry-run (er wordt niets aangepast)' if ph == 'dry_run' else 'live'}; "
                     f"automatisch aanpassen: {'aan' if await eng.active() else 'UIT (alleen analyse)'}")
    ac = eng.db.active_change()
    if ac:
        days = max(0, (ac["eval_ts"] - __import__("time").time()) / 86400)
        status.append(f"Lopend experiment: {LABELS.get(ac['key'])} {ac['old']:g} → {ac['new']:g}, evaluatie over {days:.0f} dagen")
    import time as _t
    dry_end = None
    if ph == "dry_run":
        dry_end = datetime.fromtimestamp(eng.db.get("install_ts") + eng.cfg["dry_run_weeks"] * 7 * 86400, eng.tz).strftime("%d-%m-%Y")
    return web.Response(content_type="text/html", text=dashboard.page({
        "latest": latest, "history": hist, "changes": _fmt_changes(eng), "status": status,
        "live": await _live(eng), "cfg": eng.cfg, "opts": eng.current_opts(eng.db.last_snapshot() or {}),
        "active": await eng.active(), "dry_run_end": dry_end,
        "opv": eng.db.get("opv_seen"), "dhw_found": bool(eng.dhw), "report_ts": report_ts,
        "weeks": metrics.weekly_summary(eng.db.days(), eng.cfg), "price": eng.cfg.get("gas_price_eur_per_m3") or 0}))


async def refresh(request):
    eng = request.app["eng"]
    if request.remote not in (INGRESS_IP, "127.0.0.1", "::1"):
        return web.Response(status=403, text="Alleen via Home Assistant (Ingress)")
    try:
        await eng.refresh_report()
    except Exception:
        log.exception("verversen mislukt")
    raise web.HTTPFound("./")


async def export_csv(request):
    eng = request.app["eng"]
    if request.remote not in (INGRESS_IP, "127.0.0.1", "::1"):
        return web.Response(status=403, text="Alleen via Home Assistant (Ingress)")
    cols = ["date", "avg_out", "degree_days", "gas_m3", "gas_heating_m3", "boiler_m3", "dhw_m3", "ch_m3", "other_m3",
            "starts", "burns", "mean_burn_min", "flow_mean", "flow_below55_frac", "pwm_frac", "away_frac", "valid"]
    lines = [";".join(cols)]
    for r in eng.db.days():
        lines.append(";".join("" if r.get(c) is None else (f"{r[c]:.4f}".replace(".", ",") if isinstance(r[c], float) else str(r[c])) for c in cols))
    return web.Response(text="\n".join(lines), content_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=sat_autotune_dagen.csv"})


async def start_web(eng):
    app = web.Application()
    app["eng"] = eng
    app.router.add_get("/", index)
    app.router.add_get("/refresh", refresh)
    app.router.add_get("/export.csv", export_csv)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()
    log.info("Dashboard op poort 8099 (Ingress)")
