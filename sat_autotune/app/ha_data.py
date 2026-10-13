"""Gegevens ophalen uit HA en omzetten naar de reeksen die metrics.py gebruikt."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import metrics as m
from .const import ENT, ROOMS

log = logging.getLogger("data")

CHUNK_DAYS = 10
CONS_PARAMS = None  # (min, max) m3/h uit de SAT-opties; gezet door de engine


async def _history(ha, ids, a, b, attrs):
    res = await ha.ws_call({
        "type": "history/history_during_period",
        "start_time": a.isoformat(), "end_time": b.isoformat(),
        "entity_ids": ids, "include_start_time_state": True,
        "significant_changes_only": False,
        "minimal_response": not attrs, "no_attributes": not attrs,
    })
    return res or {}


async def fetch(ha, tz, start_date, end_date, dhw=None):
    """Alle benodigde reeksen voor start_date..end_date (inclusief), lokale dagen."""
    a = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    b = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz) + timedelta(days=1)

    plain_ids = [ENT[k] for k in ("flame", "flow", "starts", "manual", "person", "consumption_est", "modulation")] + list(ROOMS.values())
    if dhw:
        plain_ids.append(dhw)
    plain = await _history(ha, plain_ids, a, b, attrs=False)

    clim = []
    t = a
    while t < b:
        e = min(t + timedelta(days=CHUNK_DAYS), b)
        r = await _history(ha, [ENT["climate"]], t, e, attrs=True)
        clim += r.get(ENT["climate"], [])
        t = e
    climp = m.parse_history(clim)

    def p(eid):
        return m.parse_history(plain.get(eid))

    stats = await ha.ws_call({
        "type": "recorder/statistics_during_period",
        "start_time": a.isoformat(), "end_time": b.isoformat(),
        "statistic_ids": [ENT["gas"]], "period": "hour", "types": ["change"],
    })
    hourly = []
    for row in (stats or {}).get(ENT["gas"], []):
        st = row["start"]
        st = st / 1000 if st > 1e11 else st
        hourly.append((float(st), row.get("change")))

    full = m.gas_per_day(hourly, tz)
    dropped = len({datetime.fromtimestamp(t, tz).date() for t, v in hourly if v is not None}) - len(full)
    if dropped > 0:
        log.info("Gas: %d dag(en) met onvolledige uurstatistieken genegeerd", dropped)
    data = {
        "outside": m.attr_num_series(climp, "outside_temperature"),
        "setpoint": m.attr_num_series(climp, "temperature"),
        "preset": m.attr_str_series(climp, "preset_mode"),
        "pwm": m.attr_str_series(climp, "pulse_width_modulation_state"),
        "flame": m.bool_series(p(ENT["flame"])),
        "flow": m.num_series(p(ENT["flow"])),
        "starts": m.num_series(p(ENT["starts"])),
        "manual": m.bool_series(p(ENT["manual"])),
        "person": m.bool_series(p(ENT["person"]), true_values=("home",)),
        "rooms": {n: m.num_series(p(eid)) for n, eid in ROOMS.items()},
        "consumption": m.num_series(p(ENT["consumption_est"])),
        "dhw": m.bool_series(p(dhw)) if dhw else None,
        "minimum_setpoint": m.attr_num_series(climp, "minimum_setpoint"),
        "gas_daily": full,
    }
    if not data["consumption"] and CONS_PARAMS:
        mod = m.num_series(p(ENT["modulation"]))
        if mod:
            data["consumption"] = m.derive_consumption(mod, *CONS_PARAMS)
            log.debug("Verbruikssensor leeg; verbruik afgeleid uit modulatie")
    return data


async def estimate_base_load(ha, tz, cfg, today, db, dhw=None):
    """Basislast voor warm water en koken (douchen/koken zit in dezelfde gasmeter).
    Gecachet voor 7 dagen; handmatige waarde in de configuratie wint."""
    if cfg.get("base_gas_m3_per_day"):
        return cfg["base_gas_m3_per_day"]
    cached = db.get("base_load")
    if cached and today.toordinal() - cached["day"] < 7:
        return cached["value"]
    start = today - timedelta(days=90)
    try:
        data = await fetch(ha, tz, start, today - timedelta(days=1), dhw)
    except Exception as e:
        log.warning("Basislast schatten mislukt: %s", e)
        return cached["value"] if cached else None
    gd = {}
    d = start
    while d < today:
        a, b = m.day_bounds(d, tz)
        avg = m.twmean(data["outside"], a, b)
        if avg is not None:
            gd[d] = max(0.0, cfg["degree_day_base"] - avg)
        d += timedelta(days=1)
    val = m.estimate_base_load(data["gas_daily"], gd)
    log.info("Basislast-schatting: %d dagen met gasdata, %d met buitentemperatuur, %d zonder stookbehoefte",
             len(data["gas_daily"]), len(gd), sum(1 for d in gd if gd[d] <= 0.5))
    if val is None:
        val = m.estimate_base_load_fallback(data["gas_daily"])
        if val is not None:
            log.warning("Basislast grof geschat op %.2f m3/dag (15e percentiel van het dagverbruik)", val)
    if val is not None:
        db.set("base_load", {"day": today.toordinal(), "value": val})
        log.info("Basislast warm water/koken geschat op %.2f m3/dag", val)
    else:
        log.warning("Geen basislast te schatten (te weinig dagen zonder stookbehoefte)")
    return val


async def check_entities(ha):
    """Controleer of alle entiteiten bestaan; retourneert lijst ontbrekende."""
    missing = []
    for name, eid in list(ENT.items()) + [(f"kamer {n}", e) for n, e in ROOMS.items()]:
        if name == "killswitch":
            continue
        st = await ha.get_state(eid)
        if st is None:
            log.error("Entiteit ontbreekt: %s (%s)", eid, name)
            missing.append(eid)
        elif st.get("state") in ("unavailable", "unknown"):
            log.warning("Entiteit niet beschikbaar: %s (%s)", eid, st.get("state"))
    return missing


DHW_TOKENS = ("dhw", "hot_water", "domestic", "tap_water", "warm_water")


async def discover_dhw(ha, cfg, tz):
    """Zoek de OTGW-sensor die aangeeft dat de ketel warm water maakt, op basis van gedrag.
    Retourneert (gekozen, tabel met kandidaten)."""
    if cfg.get("entity_dhw"):
        return cfg["entity_dhw"], []
    states = await ha.ws_call({"type": "get_states"})
    cands = sorted(s["entity_id"] for s in states
                   if s["entity_id"].startswith("binary_sensor.") and "otgw" in s["entity_id"]
                   and any(t in s["entity_id"] for t in DHW_TOKENS))
    if not cands:
        return None, []
    now = datetime.now(tz)
    a, b = (now - timedelta(days=3)), now
    res = await _history(ha, cands + [ENT["flame"]], a, b, attrs=False)
    flame = m.bool_series(m.parse_history(res.get(ENT["flame"])))
    table = []
    for c in cands:
        sc = m.score_dhw_candidate(m.bool_series(m.parse_history(res.get(c))), flame, a.timestamp(), b.timestamp())
        table.append((c, sc))
    good = [(c, sc) for c, sc in table if sc["ok"]]
    pick = max(good, key=lambda x: x[1]["p_flame"])[0] if good else None
    return pick, table


async def history_depth(ha, tz, max_days=45):
    """Hoeveel dagen geschiedenis bewaart HA (recorder)? Vroegste vlam-wisseling binnen max_days."""
    now = datetime.now(tz)
    res = await _history(ha, [ENT["flame"]], now - timedelta(days=max_days), now, attrs=False)
    ts = [t for t, _, _ in m.parse_history(res.get(ENT["flame"]))]
    start = (now - timedelta(days=max_days)).timestamp()
    real = [t for t in ts if t > start + 3600]
    return (now.timestamp() - min(real)) / 86400 if real else None
