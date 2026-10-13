"""Ingress-webserver (poort 8099), alleen bereikbaar via de HA-proxy."""
from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web

from . import dashboard, tuner
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
    reps = eng.db.reports("weekly", 12) or eng.db.reports("test", 1)
    latest = reps[0]["data"] if reps else None
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
        "opv": eng.db.get("opv_seen"), "dhw_found": bool(eng.dhw)}))


async def start_web(eng):
    app = web.Application()
    app["eng"] = eng
    app.router.add_get("/", index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()
    log.info("Dashboard op poort 8099 (Ingress)")
