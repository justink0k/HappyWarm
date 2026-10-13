"""Zuivere rekenkern: geen netwerk, geen HA. Alle tijden zijn epoch-seconden."""
from __future__ import annotations

import bisect
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- parsen


def _ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def parse_history(entries):
    """HA-history (gecomprimeerd of volledig) -> [(t, state, attrs)] met attributen doorgetrokken."""
    out, attrs = [], {}
    for e in entries or []:
        if e.get("a") is not None:
            attrs = e["a"]
        elif e.get("attributes") is not None:
            attrs = e["attributes"]
        t = _ts(e.get("lu") or e.get("lc") or e.get("last_updated") or e.get("last_changed"))
        if t is None:
            continue
        out.append((t, e.get("s", e.get("state")), attrs))
    return out


def num_series(parsed):
    r = []
    for t, s, _ in parsed:
        try:
            r.append((t, float(s)))
        except (TypeError, ValueError):
            pass
    return r


def attr_num_series(parsed, key):
    r = []
    for t, _, a in parsed:
        try:
            r.append((t, float(a[key])))
        except (KeyError, TypeError, ValueError):
            pass
    return r


def attr_str_series(parsed, key):
    return [(t, str(a[key]).lower()) for t, _, a in parsed if a and a.get(key) is not None]


def bool_series(parsed, true_values=("on",)):
    r = []
    for t, s, _ in parsed:
        if s in (None, "unavailable", "unknown"):
            continue
        r.append((t, 1 if str(s).lower() in true_values else 0))
    return r


# ---------------------------------------------------------------- tijdreeks-hulp


def segments(series, a, b):
    """Stapfunctie -> [(start, einde, waarde)] binnen [a, b)."""
    if not series:
        return []
    ts = [t for t, _ in series]
    i = bisect.bisect_right(ts, a) - 1
    pts = []
    start = 0
    if i >= 0:
        pts.append((a, series[i][1]))
        start = i + 1
    for t, v in series[start:]:
        if t >= b:
            break
        pts.append((max(t, a), v))
    segs = []
    for k, (t, v) in enumerate(pts):
        e = pts[k + 1][0] if k + 1 < len(pts) else b
        if e > t:
            segs.append((t, e, v))
    return segs


def twmean(series, a, b):
    segs = segments(series, a, b)
    tot = sum(e - s for s, e, _ in segs)
    return sum(v * (e - s) for s, e, v in segs) / tot if tot else None


def dur_where(series, a, b, pred):
    return sum(e - s for s, e, v in segments(series, a, b) if pred(v))


def intervals_where(series, a, b, pred):
    out = []
    for s, e, v in segments(series, a, b):
        if not pred(v):
            continue
        if out and abs(out[-1][1] - s) < 1e-6:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def counter_delta(series, a, b):
    segs = segments(series, a, b)
    if len(segs) < 1:
        return None
    d = segs[-1][2] - segs[0][2]
    return d if d >= 0 else None  # teller-reset: onbekend


def join(series_list, a, b):
    """Voeg stapfuncties samen: [(start, einde, [waarden])] waar alle reeksen een waarde hebben."""
    times = {a}
    for s in series_list:
        for t, _ in s:
            if a < t < b:
                times.add(t)
    times = sorted(times)
    idx = [[t for t, _ in s] for s in series_list]
    out = []
    for k, t in enumerate(times):
        e = times[k + 1] if k + 1 < len(times) else b
        vals, ok = [], True
        for s, ix in zip(series_list, idx):
            i = bisect.bisect_right(ix, t) - 1
            if i < 0:
                ok = False
                break
            vals.append(s[i][1])
        if ok and e > t:
            out.append((t, e, vals))
    return out


def weighted_quantile(pairs, q):
    """pairs = [(waarde, gewicht)]"""
    pairs = sorted(p for p in pairs if p[1] > 0)
    tot = sum(w for _, w in pairs)
    if not tot:
        return None
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * tot:
            return v
    return pairs[-1][0]


# ---------------------------------------------------------------- dagen


def day_bounds(d: date, tz: ZoneInfo):
    a = datetime(d.year, d.month, d.day, tzinfo=tz)
    b = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return a.timestamp(), b.timestamp()


def gas_per_day(hourly, tz):
    """hourly = [(start_ts, m3)] -> {date: m3} (alleen dagen met data)."""
    out = {}
    for t, v in hourly:
        if v is None:
            continue
        d = datetime.fromtimestamp(t, tz).date()
        out[d] = out.get(d, 0.0) + v
    return out


def estimate_base_load(daily_gas, daily_degree_days, max_gd=0.5):
    """Basislast (warm water + koken) in m3/dag: mediaan van het gas op dagen zonder stookbehoefte.

    Douchen en koken zitten in de totale gasmeter; zonder deze correctie lijkt het
    verbruik per graaddag hoger dan het werkelijk voor verwarming is. Dagen met
    stookbehoefte (graaddagen > max_gd) tellen niet mee in deze schatting.
    """
    vals = [g for d, g in daily_gas.items() if daily_degree_days.get(d, 99) <= max_gd and g > 0]
    return statistics.median(vals) if len(vals) >= 5 else None


def estimate_base_load_fallback(daily_gas, min_days=30):
    """Grove basislast als er geen buitentemperatuur-historie is: 15e percentiel van het dagverbruik."""
    vals = sorted(g for g in daily_gas.values() if g > 0)
    if len(vals) < min_days:
        return None
    return vals[int(0.15 * (len(vals) - 1))]


def analyze_day(data, cfg, d: date, tz: ZoneInfo, base_load):
    a, b = day_bounds(d, tz)
    row = {"date": d.isoformat()}
    out_t = data.get("outside")
    avg_out = twmean(out_t, a, b) if out_t else None
    row["avg_out"] = avg_out
    row["degree_days"] = max(0.0, cfg["degree_day_base"] - avg_out) if avg_out is not None else None
    gas = data.get("gas_daily", {}).get(d)
    row["gas_m3"] = gas
    row["gas_heating_m3"] = None if gas is None else max(0.0, gas - (base_load or 0.0))

    flame = data.get("flame") or []
    burns = intervals_where(flame, a, b, lambda v: v == 1)
    burn_s = sum(e - s for s, e in burns)
    row["burn_h"] = burn_s / 3600
    row["burns"] = len(burns)
    row["mean_burn_min"] = (burn_s / len(burns) / 60) if burns else None
    st = counter_delta(data.get("starts") or [], a, b)
    row["starts"] = st

    # aanvoer tijdens branden
    fl = data.get("flow") or []
    jf = join([fl, flame], a, b) if fl and flame else []
    on = [(e - s, v[0]) for s, e, v in jf if v[1] == 1]
    w = sum(x for x, _ in on)
    row["flow_mean"] = sum(x * v for x, v in on) / w if w else None
    row["flow_below55_frac"] = sum(x for x, v in on if v < 55) / w if w else None
    row["flow_p95"] = weighted_quantile([(v, x) for x, v in on], 0.95)

    # gasverdeling: ketelverbruik volgens SAT (m3/h) tijdens branden; warm water via de OTGW-sensor
    cons, dhw = data.get("consumption") or [], data.get("dhw")
    boiler = dhw_m3 = 0.0
    if cons and flame:
        series = [cons, flame] + ([dhw] if dhw else [])
        for s_, e_, v in join(series, a, b):
            if v[1] == 1:
                part = v[0] * (e_ - s_) / 3600
                boiler += part
                if dhw and v[2] == 1:
                    dhw_m3 += part
        row["boiler_m3"] = boiler
        row["dhw_m3"] = dhw_m3 if dhw else None
        row["ch_m3"] = (boiler - dhw_m3) if dhw else None
        row["other_m3"] = (gas - boiler) if gas is not None else None
    else:
        row["boiler_m3"] = row["dhw_m3"] = row["ch_m3"] = row["other_m3"] = None

    # PWM en continu branden
    pwm = data.get("pwm")
    if pwm:
        known = sum(e - s for s, e, _ in segments(pwm, a, b))
        act = dur_where(pwm, a, b, lambda v: v in ("on", "off"))
        row["pwm_frac"] = act / known if known else None
    else:
        row["pwm_frac"] = None
    long_burn = sum(e - s for s, e in burns if e - s >= 30 * 60)
    row["continuous_min"] = long_burn / 60
    row["burn_min"] = burn_s / 60

    # afwezigheid / handmatig
    # Een dag telt als 'afwezig' bij preset Away (> max_away_fraction) of als je vrijwel de hele
    # dag weg bent (> max_person_away_fraction). Gewoon een werkdag buiten de deur telt niet.
    person = data.get("person")
    person_away = 0.0
    if person:
        kn = sum(e - s for s, e, _ in segments(person, a, b))
        person_away = dur_where(person, a, b, lambda v: v == 0) / kn if kn else 0.0
    preset_away = 0.0
    preset = data.get("preset")
    if preset:
        kn = sum(e - s for s, e, _ in segments(preset, a, b))
        preset_away = dur_where(preset, a, b, lambda v: v == "away") / kn if kn else 0.0
    away = max(person_away, preset_away)
    row["away_frac"] = away
    row["person_away_frac"] = person_away
    is_away_day = preset_away > cfg["max_away_fraction"] or person_away > cfg.get("max_person_away_fraction", 0.75)
    man = data.get("manual")
    row["manual_frac"] = (dur_where(man, a, b, lambda v: v == 1) / (b - a)) if man else 0.0

    # comfort per kamer t.o.v. setpoint van SAT
    sp = data.get("setpoint") or []
    lim = cfg["comfort_limit_c"]
    rooms = {}
    for name, rs in (data.get("rooms") or {}).items():
        if not rs or not sp:
            continue
        series = [rs, sp] + ([person] if person else [])
        tot = dev_w = cold = warm = home = cold_home = 0.0
        for s, e, v in join(series, a, b):
            dt = e - s
            dev = v[0] - v[1]
            is_home = (v[2] == 1) if person else True
            tot += dt
            dev_w += dev * dt
            if dev < -lim:
                cold += dt
                if is_home:
                    cold_home += dt
            if dev > lim:
                warm += dt
            if is_home:
                home += dt
        if tot:
            rooms[name] = {
                "mean_dev": dev_w / tot,
                "cold_h": cold / 3600,
                "warm_h": warm / 3600,
                "home_h": home / 3600,
                "cold_home_h": cold_home / 3600,
            }
    row["rooms"] = rooms

    # geldigheid
    reasons = []
    if row["degree_days"] is None:
        reasons.append("geen buitentemperatuur")
    elif row["degree_days"] < cfg["min_degree_days_per_day"]:
        reasons.append("te weinig graaddagen")
    if gas is None:
        reasons.append("geen gasdata")
    if is_away_day:
        reasons.append("veel afwezig")
    if row["manual_frac"] > cfg["max_away_fraction"]:
        reasons.append("handmatige stand")
    row["valid"] = not reasons
    row["invalid_reasons"] = reasons
    return row


def analyze(data, cfg, start: date, end: date, tz: ZoneInfo, base_load=None):
    """Analyseer de dagen start..end (inclusief)."""
    base = base_load if base_load else (cfg.get("base_gas_m3_per_day") or None)
    days = []
    d = start
    while d <= end:
        days.append(analyze_day(data, cfg, d, tz, base))
        d += timedelta(days=1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "base_load_m3_per_day": base,
        "days": days,
        "agg": aggregate(days, cfg),
        "warnings": warnings_for(days, cfg, base),
    }


def aggregate(days, cfg):
    valid = [r for r in days if r["valid"]]
    allgd = sum(r["degree_days"] or 0 for r in days)
    agg = {
        "days_total": len(days),
        "valid_days": len(valid),
        "degree_days_total": allgd,
        "gas_total_m3": sum(r["gas_m3"] or 0 for r in days),
    }
    split = [r for r in days if r.get("boiler_m3") is not None and r.get("gas_m3") is not None]
    if split:
        g = sum(r["gas_m3"] for r in split)
        bo = sum(r["boiler_m3"] for r in split)
        agg["split_gas_m3"] = g
        agg["split_boiler_m3"] = bo
        agg["split_dhw_m3"] = sum(r["dhw_m3"] or 0 for r in split) if any(r["dhw_m3"] is not None for r in split) else None
        agg["split_other_m3"] = g - bo
    if not valid:
        return agg
    gd = sum(r["degree_days"] for r in valid)
    agg["degree_days"] = gd
    agg["gas_heating_m3"] = sum(r["gas_heating_m3"] for r in valid)
    agg["gas_per_gd"] = agg["gas_heating_m3"] / gd if gd else None
    agg["gas_per_gd_raw"] = sum(r["gas_m3"] for r in valid) / gd if gd else None
    sts = [r["starts"] for r in valid if r["starts"] is not None]
    agg["starts_per_day"] = sum(sts) / len(sts) if sts else None
    burns = sum(r["burns"] for r in valid)
    burn_min = sum(r["burn_min"] for r in valid)
    agg["mean_burn_min"] = burn_min / burns if burns else None
    bh = [(r["burn_h"], r) for r in valid if r["flow_mean"] is not None]
    wsum = sum(h for h, _ in bh)
    agg["flow_mean"] = sum(h * r["flow_mean"] for h, r in bh) / wsum if wsum else None
    agg["pct_flow_below55"] = (
        100 * sum(h * r["flow_below55_frac"] for h, r in bh) / wsum if wsum else None
    )
    p95 = [r["flow_p95"] for r in valid if r["flow_p95"] is not None]
    agg["flow_p95"] = max(p95) if p95 else None
    pw = [r["pwm_frac"] for r in valid if r["pwm_frac"] is not None]
    agg["pwm_frac"] = sum(pw) / len(pw) if pw else None
    agg["continuous_frac"] = sum(r["continuous_min"] for r in valid) / burn_min if burn_min else None

    rooms = {}
    for r in valid:
        for n, v in r["rooms"].items():
            x = rooms.setdefault(n, {"dev": [], "cold_h": 0, "warm_h": 0, "home_h": 0, "cold_home_h": 0})
            x["dev"].append(v["mean_dev"])
            for k in ("cold_h", "warm_h", "home_h", "cold_home_h"):
                x[k] += v[k]
    agg["rooms"] = {
        n: {
            "mean_dev": sum(x["dev"]) / len(x["dev"]),
            "cold_h": x["cold_h"],
            "warm_h": x["warm_h"],
            "cold_home_frac": x["cold_home_h"] / x["home_h"] if x["home_h"] else 0.0,
        }
        for n, x in rooms.items()
    }
    return agg


def monitored_cold_frac(agg, rooms):
    vals = [v["cold_home_frac"] for n, v in (agg.get("rooms") or {}).items() if n in rooms]
    return max(vals) if vals else None


def warnings_for(days, cfg, base):
    w = []
    valid = sum(1 for r in days if r["valid"])
    if valid < cfg["min_valid_days"]:
        w.append(
            f"Slechts {valid} vergelijkbare dagen (minimaal {cfg['min_valid_days']} nodig); "
            "vergelijking en aanpassingen zijn minder betrouwbaar."
        )
    away = sum(1 for r in days if "veel afwezig" in r["invalid_reasons"])
    if away >= 2:
        w.append(f"{away} dagen met veel afwezigheid zijn buiten beschouwing gelaten.")
    if base is None:
        w.append(
            "Geen basislast voor warm water/koken bekend (geen dagen zonder stookbehoefte in de data). "
            "Gas per graaddag bevat daardoor ook douchen en koken. Vul eventueel "
            "'base_gas_m3_per_day' in."
        )
    if any(r["gas_m3"] is None for r in days):
        w.append("Voor sommige dagen ontbreekt gasdata.")
    return w


def compare(cur, prev):
    """Procentueel verschil van gas per graaddag t.o.v. een eerdere periode."""
    try:
        c, p = cur["gas_per_gd"], prev["gas_per_gd"]
        return 100 * (c - p) / p if p else None
    except (KeyError, TypeError):
        return None


def longest_cold_streak(room_series, setpoint, person, a, b, limit):
    """Langste aaneengesloten tijd (s) dat de kamer > limit onder het setpoint zat terwijl je thuis was."""
    series = [room_series, setpoint] + ([person] if person else [])
    best = cur = 0.0
    last_end = None
    for s, e, v in join(series, a, b):
        cold = (v[0] - v[1]) < -limit and (v[2] == 1 if person else True)
        if cold:
            cur = cur + (e - s) if last_end is not None and abs(last_end - s) < 1e-6 else (e - s)
            last_end = e
            best = max(best, cur)
        else:
            cur, last_end = 0.0, None
    return best
