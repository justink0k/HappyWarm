"""Meet of terugzetten bij afwezigheid loont. Zuivere rekenkern.

Per afwezigheid (>= 3 uur weg) vergelijken we het gas voor verwarming tijdens de afwezigheid plus het
opwarmen daarna met wat de ketel in dezelfde uren had verstookt als je thuis was gebleven. Dat tweede
volgt uit een eenvoudig model uit de uren dat je wel thuis was op de normale stand:
    verwarmingsgas per uur = a + b * (setpoint - buitentemperatuur)
"""
from __future__ import annotations

from . import metrics as m

HOME_PRESETS = ("home", "comfort", "activity")
RECOVERY_TOL = 0.5      # kamer weer binnen 0,5 °C van het setpoint
RECOVERY_MAX_H = 6


def ch_m3(data, a, b, k=1.0):
    """Gas voor verwarming (m3) tussen a en b: ketelverbruik tijdens branden, zonder warm water."""
    cons, flame, dhw = data.get("consumption") or [], data.get("flame") or [], data.get("dhw")
    if not cons or not flame:
        return None
    series = [cons, flame] + ([dhw] if dhw else [])
    tot = 0.0
    for s, e, v in m.join(series, a, b):
        if v[1] == 1 and not (dhw and v[2] == 1):
            tot += v[0] * (e - s) / 3600
    return tot * k


def reference_fit(data, a, b, k=1.0):
    """Model van het uurverbruik op uren dat je thuis was en de normale stand stond. None bij te weinig data."""
    person, preset, sp, outside = data.get("person"), data.get("preset"), data.get("setpoint"), data.get("outside")
    manual = data.get("manual")
    if not (person and sp and outside):
        return None
    pts = []
    t = a
    while t + 3600 <= b:
        e = t + 3600
        home = m.twmean(person, t, e)
        ph = (m.dur_where(preset, t, e, lambda v: v in HOME_PRESETS) / 3600) if preset else 1.0
        man = (m.dur_where(manual, t, e, lambda v: v == 1) / 3600) if manual else 0.0
        if home is not None and home > 0.95 and ph > 0.9 and man < 0.1:
            spv, out, g = m.twmean(sp, t, e), m.twmean(outside, t, e), ch_m3(data, t, e, k)
            if spv is not None and out is not None and g is not None:
                pts.append((max(0.0, spv - out), g))
        t = e
    if len(pts) < 24:
        return {"ok": False, "n": len(pts)}
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    n = len(pts)
    xb, yb = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xb) ** 2 for x in xs)
    if sxx < 1e-6:
        return {"ok": True, "a0": yb, "b1": 0.0, "n": n}
    b1 = max(0.0, sum((x - xb) * (y - yb) for x, y in pts) / sxx)
    a0 = yb - b1 * xb
    if a0 < 0:
        b1, a0 = sum(x * y for x, y in pts) / sum(x * x for x in xs), 0.0
    return {"ok": True, "a0": a0, "b1": b1, "n": n}


def _val_at(series, t):
    segs = m.segments(series, t, t + 1)
    return segs[0][2] if segs else None


def away_events(data, a, b, min_hours=3):
    """Afwezigheden die volledig binnen [a, b] vallen."""
    person = data.get("person")
    if not person:
        return []
    return [(s, e) for s, e in m.intervals_where(person, a, b, lambda v: v == 0)
            if e - s >= min_hours * 3600 and s > a and e < b]


def analyze_event(data, s, e, b, fit, k=1.0, room="woonkamer"):
    room_s = (data.get("rooms") or {}).get(room)
    sp = data.get("setpoint")
    if not room_s or not sp:
        return None
    # opwarmen: tot de kamer weer binnen de marge van het setpoint zit
    t_rec, reached = e + RECOVERY_MAX_H * 3600, False
    hi = min(b, e + RECOVERY_MAX_H * 3600)
    for s2, e2, v in m.join([room_s, sp], e + 600, hi):
        if v[0] >= v[1] - RECOVERY_TOL and v[1] >= 18:   # setpoint na vertrek-stand (Away = 16) negeren
            t_rec, reached = s2, True
            break
    t_rec = min(t_rec, hi)
    window_end = t_rec
    drop = None
    r0 = _val_at(room_s, s)
    segs = m.segments(room_s, s, e)
    if r0 is not None and segs:
        drop = r0 - min(v for _, _, v in segs)
    # aanvoer tijdens opwarmen
    flow = data.get("flow") or []
    flame = data.get("flame") or []
    fl_mean = pct55 = None
    if flow and flame and window_end > e:
        jf = [(en - st, v[0]) for st, en, v in m.join([flow, flame], e, window_end) if v[1] == 1]
        w = sum(x for x, _ in jf)
        if w:
            fl_mean = sum(x * v for x, v in jf) / w
            pct55 = 100 * sum(x for x, v in jf if v < 55) / w
    actual = ch_m3(data, s, window_end, k)
    expected = None
    if fit and fit.get("ok") and actual is not None:
        sp_home = _val_at(sp, window_end) if reached else None
        sp_home = sp_home if sp_home and sp_home >= 18 else (_home_setpoint(sp, e, hi) or 20.0)
        outside = data.get("outside") or []
        expected, t = 0.0, s
        while t < window_end:
            nxt = min(t + 3600, window_end)
            out = m.twmean(outside, t, nxt)
            if out is None:
                expected = None
                break
            expected += (fit["a0"] + fit["b1"] * max(0.0, sp_home - out)) * (nxt - t) / 3600
            t = nxt
    return {
        "start": s, "end": e, "hours": (e - s) / 3600, "drop": drop,
        "recovery_min": (t_rec - e) / 60 if reached else None, "reached": reached,
        "flow_recovery": fl_mean, "pct55_recovery": pct55,
        "actual_m3": actual, "expected_m3": expected,
        "saving_m3": (expected - actual) if (expected is not None and actual is not None) else None,
    }


def _home_setpoint(sp, a, b):
    vals = [v for _, _, v in m.segments(sp, a, b) if v >= 18]
    return max(vals) if vals else None


def compute_events(data, a, b, k=1.0):
    fit = reference_fit(data, a, b, k)
    out = []
    for s, e in away_events(data, a, b):
        ev = analyze_event(data, s, e, b, fit, k)
        if ev:
            out.append(ev)
    return out, fit


def summarize(events, price=0.0):
    ev = [x for x in events if x.get("saving_m3") is not None]
    if not events:
        return {"n": 0}
    def avg(key, rows=None):
        vals = [x[key] for x in (rows or events) if x.get(key) is not None]
        return sum(vals) / len(vals) if vals else None
    out = {
        "n": len(events), "n_scored": len(ev),
        "avg_hours": avg("hours"), "avg_drop": avg("drop"), "avg_recovery_min": avg("recovery_min"),
        "avg_flow": avg("flow_recovery"), "avg_pct55": avg("pct55_recovery"),
        "avg_saving": avg("saving_m3", ev), "total_saving": sum(x["saving_m3"] for x in ev) if ev else None,
        "reached_frac": sum(1 for x in events if x.get("reached")) / len(events),
    }
    out["verdict"] = verdict(out, price)
    return out


def verdict(s, price=0.0):
    if s["n_scored"] < 5:
        return ("info", f"Nog te weinig afwezigheden om iets te zeggen ({s['n_scored']} van minimaal 5 met een schatting).")
    sv = s["avg_saving"]
    eur = f" (ongeveer € {sv * price:.2f})" if price else ""
    rec = f"Opwarmen duurt gemiddeld {s['avg_recovery_min']:.0f} minuten" if s.get("avg_recovery_min") is not None else "Opwarmen duurde vaak langer dan 6 uur"
    flow = (f" met {s['avg_flow']:.0f} °C aanvoer ({s['avg_pct55']:.0f}% onder 55 °C)" if s.get("avg_flow") is not None else "")
    if sv > 0.1:
        return ("goed", f"Terugzetten loont: gemiddeld {sv:.2f} m³{eur} per afwezigheid. {rec}{flow}.")
    if sv > 0:
        return ("info", f"Terugzetten levert weinig op: gemiddeld {sv:.2f} m³ per afwezigheid. {rec}{flow}.")
    return ("let_op", f"Terugzetten lijkt niets op te leveren (gemiddeld {sv:.2f} m³ per afwezigheid). {rec}{flow}.")
