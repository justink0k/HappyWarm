"""Dashboard (Ingress): kerncijfers, aanbevelingen, grafieken met hover, logboek.
Inline SVG + een klein stukje JS voor tooltips; geen externe libs. Licht/donker volgt het systeem."""
from __future__ import annotations

import html
import json
from datetime import datetime

esc = html.escape


def q(s):
    return html.escape(str(s), quote=True)


def _f(v, fmt="{:.1f}", dash="–"):
    return dash if v is None else fmt.format(v)


def _nl(v, d=1):
    """Nederlandse decimale komma."""
    return "–" if v is None else f"{v:.{d}f}".replace(".", ",")


# ------------------------------------------------------------------ grafieken
W, H, PL, PR, PT, PB = 640, 210, 40, 12, 14, 30


def _ticks(mx, n=4):
    if mx <= 0:
        return [0, 1]
    import math
    raw = mx / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    out, v = [], 0.0
    while v <= mx + step * 0.01:
        out.append(round(v, 6))
        v += step
    return out


def _fmt_tick(v):
    return f"{v:g}".replace(".", ",")


def _frame(ticks, ymax, ymin=0.0, unit=""):
    out = []
    for t in ticks:
        y = PT + (H - PT - PB) * (1 - (t - ymin) / (ymax - ymin))
        out.append(f'<line class=grid x1="{PL}" x2="{W-PR}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class=ax x="{PL-6}" y="{y+4:.1f}" text-anchor="end">{_fmt_tick(t)}</text>')
    return "".join(out)


def _xlabels(labels, xs):
    n = len(labels)
    skip = 1 if n <= 10 else 2 if n <= 20 else 4
    return "".join(f'<text class=ax x="{x:.1f}" y="{H-8}" text-anchor="middle">{esc(lb)}</text>'
                   for i, (lb, x) in enumerate(zip(labels, xs)) if i % skip == 0)


def _empty(msg):
    return f'<p class="mut empty">{esc(msg)}</p>'


def bar_chart(vals, labels, tips, color, ref=None, ref_label=None, ymax=None):
    n = len(vals)
    known = [v for v in vals if v is not None]
    if not known:
        return _empty("Geen data in deze periode.")
    top = max(known + [ref or 0]) * 1.08 or 1
    ticks = _ticks(top)
    top = max(ticks[-1], top)
    bw = (W - PL - PR) / n
    out = [f'<svg viewBox="0 0 {W} {H}" class=chart role=img>', _frame(ticks, top)]
    xs = []
    for i, v in enumerate(vals):
        x0 = PL + i * bw
        xs.append(x0 + bw / 2)
        gap = 2
        w = max(bw - gap * 2, 2)
        if v is not None:
            h = (H - PT - PB) * v / top
            y = H - PB - h
            r = min(4, w / 2, h)
            out.append(f'<path class=mark style="fill:var({color})" d="M{x0+gap:.1f},{H-PB:.1f} V{y+r:.1f} Q{x0+gap:.1f},{y:.1f} {x0+gap+r:.1f},{y:.1f} '
                       f'H{x0+gap+w-r:.1f} Q{x0+gap+w:.1f},{y:.1f} {x0+gap+w:.1f},{y+r:.1f} V{H-PB:.1f} Z"/>')
        out.append(f'<rect class=hit x="{x0:.1f}" y="{PT}" width="{bw:.1f}" height="{H-PT-PB}" data-tip="{q(tips[i])}"/>')
    if ref:
        y = H - PB - (H - PT - PB) * ref / top
        out.append(f'<line class=ref x1="{PL}" x2="{W-PR}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ax refl" x="{W-PR}" y="{y-5:.1f}" text-anchor="end">{esc(ref_label or "")}</text>')
    out.append(_xlabels(labels, xs) + "</svg>")
    return "".join(out)


def line_chart(vals, labels, tips, color, ref=None, ref_label=None, marks=None, ymin=None, ymax=None):
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if not pts:
        return _empty("Nog geen data.")
    lo = min([v for _, v in pts] + ([ref] if ref else []))
    hi = max([v for _, v in pts] + ([ref] if ref else []))
    if ymin is not None:
        lo = ymin
    if ymax is not None:
        hi = ymax
    span = (hi - lo) or max(abs(hi), 1) * 0.2
    if ymin is None:
        lo -= span * 0.15
    hi += span * 0.15
    ticks = [t for t in _ticks(hi - lo)]
    ticks = [round(lo + t, 6) for t in ticks if lo + t <= hi]
    n = len(vals)
    step = (W - PL - PR) / max(n - 1, 1) if n > 1 else 0
    xs = [PL + (i * step if n > 1 else (W - PL - PR) / 2) for i in range(n)]

    def Y(v):
        return PT + (H - PT - PB) * (1 - (v - lo) / (hi - lo))

    out = [f'<svg viewBox="0 0 {W} {H}" class=chart role=img>', _frame(ticks, hi, lo)]
    if ref is not None:
        out.append(f'<line class=ref x1="{PL}" x2="{W-PR}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}"/>'
                   f'<text class="ax refl" x="{W-PR}" y="{Y(ref)-5:.1f}" text-anchor="end">{esc(ref_label or "")}</text>')
    for i, lb in (marks or {}).items():
        out.append(f'<line class=mk x1="{xs[i]:.1f}" x2="{xs[i]:.1f}" y1="{PT}" y2="{H-PB}"/>'
                   f'<text class="ax mkl" x="{xs[i]+4:.1f}" y="{PT+10}">{esc(lb)}</text>')
    d = " ".join(f"{xs[i]:.1f},{Y(v):.1f}" for i, v in pts)
    if len(pts) > 1:
        out.append(f'<polyline class=ln style="stroke:var({color})" points="{d}"/>')
    for i, v in pts:
        out.append(f'<circle class=dot style="fill:var({color})" cx="{xs[i]:.1f}" cy="{Y(v):.1f}" r="4"/>')
    cw = max(step, 28)
    for i in range(n):
        out.append(f'<rect class=hit x="{xs[i]-cw/2:.1f}" y="{PT}" width="{cw:.1f}" height="{H-PT-PB}" data-tip="{q(tips[i])}"/>')
    out.append(_xlabels(labels, xs) + "</svg>")
    return "".join(out)


def scatter_chart(points, color):
    """points = [(graaddagen, m3, tip)]; met trendlijn (kleinste kwadraten)."""
    if len(points) < 2:
        return _empty("Minimaal 2 vergelijkbare dagen nodig.")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    xh, yh = max(xs) * 1.1 or 1, max(ys) * 1.1 or 1
    tx, ty = _ticks(xh), _ticks(yh)
    xh, yh = max(tx[-1], xh), max(ty[-1], yh)

    def X(v):
        return PL + (W - PL - PR) * v / xh

    def Y(v):
        return PT + (H - PT - PB) * (1 - v / yh)

    out = [f'<svg viewBox="0 0 {W} {H}" class=chart role=img>', _frame(ty, yh)]
    out += [f'<text class=ax x="{X(t):.1f}" y="{H-8}" text-anchor="middle">{_fmt_tick(t)}</text>' for t in tx[:-1]]
    mx_, my_ = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx_) ** 2 for x in xs)
    if sxx > 1e-9:
        b = sum((x - mx_) * (y - my_) for x, y in zip(xs, ys)) / sxx
        a = my_ - b * mx_
        x1, x2 = min(xs), max(xs)
        out.append(f'<line class=ref x1="{X(x1):.1f}" x2="{X(x2):.1f}" y1="{Y(a+b*x1):.1f}" y2="{Y(a+b*x2):.1f}"/>')
    for x, y, tip in points:
        out.append(f'<circle class=dot style="fill:var({color})" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="5"/>'
                   f'<circle class=hit cx="{X(x):.1f}" cy="{Y(y):.1f}" r="13" data-tip="{q(tip)}"/>')
    out.append(f'<text class=ax x="{W-PR}" y="{H-8}" text-anchor="end">graaddagen →</text></svg>')
    return "".join(out)


def deviation_chart(rooms, monitored):
    """Horizontaal: gemiddelde afwijking t.o.v. setpoint per kamer. Blauw = koud, oranje = warm."""
    if not rooms:
        return _empty("Geen kamerdata.")
    names = sorted(rooms)
    rowh = 34
    h = PT + rowh * len(names) + 26
    lim = max(2.0, max(abs(r["mean_dev"]) for r in rooms.values()) * 1.2)
    left = 96
    cx = left + (W - left - PR) / 2
    sc = (W - left - PR) / 2 / lim
    out = [f'<svg viewBox="0 0 {W} {h}" class=chart role=img>']
    for t in (-2, -1, 0, 1, 2):
        if abs(t) <= lim:
            x = cx + t * sc
            out.append(f'<line class="{"axis0" if t == 0 else "grid"}" x1="{x:.1f}" x2="{x:.1f}" y1="{PT}" y2="{h-24}"/>'
                       f'<text class=ax x="{x:.1f}" y="{h-8}" text-anchor="middle">{t:+d}</text>')
    out.append(f'<rect class=band x="{cx-sc:.1f}" y="{PT}" width="{2*sc:.1f}" height="{h-24-PT}"/>')
    for i, n in enumerate(names):
        r = rooms[n]
        y = PT + i * rowh + 6
        v = r["mean_dev"]
        w = abs(v) * sc
        x = cx if v >= 0 else cx - w
        col = "--warm" if v >= 0 else "--cold"
        tip = (f"{n}: gemiddeld {v:+.1f} °C t.o.v. setpoint · {r['cold_h']:.0f} u te koud · "
               f"{r['warm_h']:.0f} u te warm · {r['cold_home_frac']*100:.0f}% te koud als je thuis bent").replace(".", ",")
        out.append(f'<text class=lab x="{left-8}" y="{y+13}" text-anchor="end">{esc(n.capitalize())}{" ●" if n in monitored else ""}</text>'
                   f'<path class=mark style="fill:var({col})" d="M{x:.1f},{y} h{max(w,2):.1f} v18 h-{max(w,2):.1f} Z"/>'
                   f'<text class=val x="{(cx+w+6) if v>=0 else (cx-w-6):.1f}" y="{y+13}" text-anchor="{"start" if v>=0 else "end"}">{_nl(v)}</text>'
                   f'<rect class=hit x="{PL}" y="{y-6}" width="{W-PL-PR}" height="{rowh}" data-tip="{q(tip)}"/>')
    out.append("</svg>")
    return "".join(out)


def stacked_chart(days, labels):
    """Gasverdeling per dag: verwarming / warm water / koken en overig. Legenda erbij (>= 2 reeksen)."""
    rows = []
    for d, lb in zip(days, labels):
        if d.get("boiler_m3") is None or d.get("gas_m3") is None:
            rows.append(None)
            continue
        other = max(0.0, d["other_m3"])
        if d.get("dhw_m3") is not None:
            segs = [("Verwarming", d["ch_m3"], "--s1"), ("Warm water", d["dhw_m3"], "--s2"), ("Koken en overig", other, "--s3")]
        else:
            segs = [("Ketel (verwarming + warm water)", d["boiler_m3"], "--s1"), ("Koken en overig", other, "--s3")]
        rows.append((lb, segs, d))
    if not any(rows):
        return _empty("Geen ketelverbruik beschikbaar om het gas te verdelen.")
    top = max(sum(v for _, v, _ in r[1]) for r in rows if r) * 1.08 or 1
    ticks = _ticks(top)
    top = max(ticks[-1], top)
    n = len(rows)
    bw = (W - PL - PR) / n
    out = [f'<svg viewBox="0 0 {W} {H}" class=chart role=img>', _frame(ticks, top)]
    xs = []
    for i, r in enumerate(rows):
        x0 = PL + i * bw
        xs.append(x0 + bw / 2)
        if not r:
            continue
        lb, segs, d = r
        y = H - PB
        w = max(bw - 4, 2)
        tip = f"{lb}: " + ", ".join(f"{nm.lower()} {_nl(v, 2)} m³" for nm, v, _ in segs)
        for k, (nm, v, col) in enumerate(segs):
            h = (H - PT - PB) * v / top
            if h < 0.5:
                continue
            gap = 2 if k else 0
            out.append(f'<rect class=mark style="fill:var({col})" x="{x0+2:.1f}" y="{y-h+gap/2:.1f}" width="{w:.1f}" height="{max(h-gap,0.5):.1f}" '
                       f'rx="{3 if k == len(segs)-1 else 0}"/>')
            y -= h
        out.append(f'<rect class=hit x="{x0:.1f}" y="{PT}" width="{bw:.1f}" height="{H-PT-PB}" data-tip="{q(tip)}"/>')
    out.append(_xlabels(labels, xs) + "</svg>")
    names = {}
    for r in rows:
        if r:
            for nm, _, col in r[1]:
                names[nm] = col
    leg = "".join(f'<span class=lg><i style="background:var({c})"></i>{esc(nm)}</span>' for nm, c in names.items())
    return f'<div class=legend>{leg}</div>' + "".join(out)


def spark(vals, color):
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or 1
    n = len(vals) - 1 or 1
    d = " ".join(f"{i/n*100:.1f},{22-(v-lo)/span*20:.1f}" for i, v in pts)
    return f'<svg viewBox="0 0 100 24" class=spark preserveAspectRatio=none><polyline points="{d}" style="stroke:var({color})"/></svg>'


# ------------------------------------------------------------------ aanbevelingen
SEV = {"kans": ("▲", "Kans", 0), "let_op": ("!", "Let op", 1), "goed": ("✓", "Goed", 2), "info": ("i", "Info", 3)}


def setback_html(ctx):
    from . import setback
    events = ctx.get("events") or []
    tz = ctx.get("tz")
    price = ctx.get("price") or 0
    if not events:
        return ('<h2>Terugzetten bij afwezigheid</h2><p class=sub>Meet of het slim is de temperatuur te laten zakken als je weg bent.</p>'
                '<div class=card><p class=mut>Nog geen afwezigheden van 3 uur of langer gevonden. Zodra je weg bent geweest, verschijnen ze hier.</p></div>')
    sm = setback.summarize(events, price)
    sev, text = sm.get("verdict", ("info", ""))
    icon, label, _ = SEV[sev]
    tiles = "".join([
        tile("Afwezigheden", f"{sm['n']}", f"gem. {_nl(sm['avg_hours'], 1)} uur weg"),
        tile("Kamer zakt", f"{_nl(sm['avg_drop'])} <small>°C</small>", "gemiddeld tijdens afwezigheid"),
        tile("Opwarmen", f"{_nl(sm['avg_recovery_min'], 0)} <small>min</small>", f"aanvoer {_nl(sm['avg_flow'], 0)} °C, {_nl(sm['avg_pct55'], 0)}% onder 55 °C"),
        tile("Besparing", f"{_nl(sm['avg_saving'], 2)} <small>m³ per keer</small>", (f"totaal {_nl(sm['total_saving'], 1)} m³" if sm.get("total_saving") is not None else "")),
    ])
    def fmt_t(ts):
        return datetime.fromtimestamp(ts, tz).strftime("%d-%m %H:%M") if tz else datetime.fromtimestamp(ts).strftime("%d-%m %H:%M")
    rows = "".join(
        f"<tr><td>{fmt_t(e['start'])}</td><td class=num>{_nl(e['hours'], 1)}</td><td class=num>{_nl(e['drop'])}</td>"
        f"<td class=num>{_nl(e['recovery_min'], 0) if e.get('reached') else '> 6 u'}</td><td class=num>{_nl(e['flow_recovery'], 0)}</td>"
        f"<td class=num>{_nl(e['actual_m3'], 2)}</td><td class=num>{_nl(e['expected_m3'], 2)}</td><td class=num>{_nl(e['saving_m3'], 2)}</td></tr>"
        for e in reversed(events[-30:]))
    return (f'<h2>Terugzetten bij afwezigheid</h2><p class=sub>Het gas voor verwarming tijdens je afwezigheid plus het opwarmen, naast wat de ketel '
            f'bij thuisblijven zou hebben verstookt (model uit je thuisuren; een schatting).</p>'
            f'<ul class=recos><li class="reco {sev}"><span class=badge><span aria-hidden=true>{icon}</span> {label}</span><div><b>{esc(text)}</b></div></li></ul>'
            f'<div class=tiles>{tiles}</div>'
            f'<div class="tbl scroll"><table><tr><th>Vertrek</th><th>Uren weg</th><th>Daling °C</th><th>Opwarmen min</th><th>Aanvoer °C</th>'
            f'<th>Werkelijk m³</th><th>Verwacht m³</th><th>Besparing m³</th></tr>{rows}</table></div>')


def eur(m3, price):
    return f" (ongeveer € {m3 * price:.0f})" if price else ""


def recommendations(ctx):
    """Regelgebaseerde adviezen: (severity, titel, uitleg, wat_te_doen)."""
    r = []
    latest = ctx.get("latest")
    an = (latest or {}).get("an") or {}
    agg = an.get("agg") or {}
    cfg = ctx["cfg"]
    opts = ctx.get("opts") or {}
    cmp_pct = (latest or {}).get("cmp_pct")

    if not ctx.get("price"):
        r.append(("info", "Gasprijs nog niet ingevuld",
                  "Daarom staan er nergens euro's op het dashboard.",
                  "Vul 'gas_price_eur_per_m3' in de configuratie van de add-on in (je all-in prijs per m³, inclusief belasting en btw), bijvoorbeeld 1.40."))
    if not ctx.get("active"):
        r.append(("info", "Automatisch aanpassen staat uit",
                  "De schakelaar 'SAT Autotune actief' staat uit, dus de add-on meet en adviseert alleen.",
                  "Zet de schakelaar pas aan als de dry-run klaar is en je de voorstellen vertrouwt."))
    if ctx.get("dry_run_end"):
        r.append(("info", f"Dry-run loopt tot {ctx['dry_run_end']}",
                  "Tot die datum past de add-on niets aan en laat hij alleen zien wat hij zou doen.", ""))
    valid, need = agg.get("valid_days", 0), cfg["min_valid_days"]
    if agg and valid < need:
        r.append(("info", f"Nog te weinig koude dagen: {valid} van de {need} vergelijkbare dagen",
                  f"Een dag telt pas mee bij minstens {_nl(cfg['min_degree_days_per_day'], 0)} graaddagen (gemiddeld onder "
                  f"{_nl(cfg['degree_day_base'] - cfg['min_degree_days_per_day'], 0)} °C buiten). In warme weken stelt de add-on dus niets bij.",
                  "Geen actie nodig; zodra het kouder wordt vult dit zich vanzelf."))
    if not agg or agg.get("gas_per_gd") is None and not agg.get("starts_per_day"):
        return sorted(r, key=lambda x: SEV[x[0]][2])

    below = agg.get("pct_flow_below55")
    coef = opts.get("coef")
    if below is not None:
        if below < 30:
            tip = (f"Stooklijn (nu {_nl(coef)}) 0,1 tot 0,2 lager zetten." if coef else "Stooklijn iets lager zetten.")
            r.append(("kans", "Je ketel condenseert weinig",
                      f"Slechts {below:.0f}% van de brandtijd zit de aanvoer onder 55 °C. Een lagere aanvoer geeft een beter rendement.", tip))
        elif below < 60:
            r.append(("kans", "Condensatie kan beter",
                      f"{below:.0f}% van de brandtijd onder 55 °C. Een iets lagere stooklijn zou dit verder omhoog brengen.",
                      "Pas de stooklijn alleen aan als de kamers warm genoeg blijven."))
        else:
            r.append(("goed", "Ketel condenseert uitstekend",
                      f"{below:.0f}% van de brandtijd onder 55 °C (gemiddeld {_nl(agg.get('flow_mean'), 0)} °C aanvoer). Hier valt weinig meer te winnen.", ""))

    s, b = agg.get("starts_per_day"), agg.get("mean_burn_min")
    if s is not None and b is not None:
        if s > 40 and b < 8:
            r.append(("let_op", "Veel korte cycli",
                      f"{s:.0f} starts per dag met gemiddeld {_nl(b, 0)} minuten branden kost rendement en geeft slijtage.",
                      "Cycli per uur omlaag (nu " + (str(int(opts["cycles"])) if opts.get("cycles") else "?") + "); de add-on kan dit zelf voorstellen."))
        elif s < 25 and b > 12:
            r.append(("goed", "Rustige regeling", f"{s:.0f} starts per dag met {_nl(b, 0)} min per brand.", ""))
    ct = agg.get("continuous_frac")
    if ct is not None and ct > 0.6 and (agg.get("pwm_frac") or 0) > 0.7:
        r.append(("info", "SAT werkt vooral met pulsbreedte (PWM) en lange branden",
                  f"{(agg['pwm_frac'])*100:.0f}% van de tijd PWM actief en {ct*100:.0f}% van de brandtijd in branden van 30 minuten of langer. "
                  "Dat past bij een goed geïsoleerd huis met lage warmtevraag.", ""))

    rooms = agg.get("rooms") or {}
    for n in cfg["comfort_monitor_rooms"]:
        rm = rooms.get(n)
        if not rm:
            continue
        if rm["mean_dev"] > 0.8:
            per_day = (agg.get("gas_heating_m3") / agg["valid_days"]) if agg.get("gas_heating_m3") and agg.get("valid_days") else None
            money = ""
            if per_day:
                m3 = 0.06 * rm["mean_dev"] * per_day * 30
                money = f" Bij het weer van deze periode is dat ongeveer {_nl(m3, 1)} m³ per maand{eur(m3, ctx.get('price') or 0)}."
            r.append(("kans", f"{n.capitalize()} zit {_nl(rm['mean_dev'])} °C boven het setpoint",
                      "Elke graad die je niet te warm stookt scheelt ongeveer 5 tot 7% gas (vuistregel)." + money,
                      "Controleer of het setpoint of de kamersensor klopt; een iets lager setpoint kan direct besparen."))
        elif rm["mean_dev"] < -0.8 or rm["cold_home_frac"] > 0.05:
            r.append(("let_op", f"{n.capitalize()} zit vaak te koud",
                      f"Gemiddeld {_nl(abs(rm['mean_dev']))} °C onder het setpoint; {rm['cold_home_frac']*100:.0f}% van de tijd dat je thuis bent meer dan 1 °C te koud.",
                      "Pas de stooklijn niet verder omlaag; de add-on doet dat bij zo'n kamer ook niet."))

    mxs = opts.get("max_setpoint")
    p95 = agg.get("flow_p95")
    if mxs and p95 and mxs - p95 >= 10:
        r.append(("info", f"Maximaal setpoint ({_nl(mxs, 0)} °C) wordt nooit gehaald",
                  f"De aanvoer komt hooguit tot {_nl(p95, 0)} °C. Een lager maximum geeft extra bescherming tegen pieken zonder comfortverlies.",
                  "Kan veilig 5 °C omlaag; de add-on stelt dit voor als het nuttig is."))

    gt = agg.get("split_gas_m3")
    cal = an.get("calibration") or {}
    if gt and agg.get("split_boiler_m3") is not None:
        bo, ot, dh = agg["split_boiler_m3"], agg["split_other_m3"], agg.get("split_dhw_m3")
        if cal.get("ok"):
            how = (f"Gekalibreerd op je gasmeter over {cal['n']} dagen (correctie ×{_nl(cal['k'], 2)}, R² {_nl(cal['r2'], 2)}); "
                   f"koken en overig is ongeveer {_nl(cal['c'], 2)} m³ per dag.")
        else:
            how = f"Nog niet gekalibreerd ({cal.get('reason', 'te weinig data')}); schatting uit OTGW-modulatie en de min/max van SAT."
        if dh is not None:
            r.append(("info", f"Gas: {(bo-dh)/gt*100:.0f}% verwarming, {dh/gt*100:.0f}% warm water, {max(0,ot)/gt*100:.0f}% koken en overig",
                      f"Over deze periode ({_nl(gt)} m³). Warm water volgt uit de OTGW; {how}",
                      "Warm water is vaak 20 tot 40% van het gas: kortere douches of een iets lagere warmwatertemperatuur is een besparing die losstaat van de stooklijn." if dh/gt > 0.2 else ""))
        else:
            r.append(("info", f"Gas: {bo/gt*100:.0f}% via de ketel, {max(0,ot)/gt*100:.0f}% koken en overig",
                      "Zonder warm-water-sensor kan ik verwarming en warm water niet uit elkaar halen. " + how,
                      "Stel 'entity_dhw' in op de OTGW-sensor voor warm water (zie het log voor kandidaten)."))
    opv = ctx.get("opv")
    if opv:
        r.append(("info", f"OPV staat op {_nl(opv, 0)} °C",
                  "Dit is de door SAT gekalibreerde ondergrens: het laagste setpoint waarop de ketel zonder te pendelen brandt. "
                  "De add-on kijkt mee en logt elke wijziging, maar past hem niet zelf aan.",
                  "Handmatig aanpassen kan via Herconfigureren van SAT; een lagere waarde kan condensatie verbeteren, maar herkalibreer wel."))

    base = an.get("base_load_m3_per_day")
    tot = agg.get("gas_total_m3")
    ndays = agg.get("days_total") or 0
    if base and tot and ndays:
        share = min(100, base * ndays / tot * 100)
        if share > 25:
            r.append(("info", f"Zo'n {share:.0f}% van je gas gaat naar douchen en koken",
                      f"Basislast ongeveer {_nl(base)} m³ per dag. Daar heeft de stooklijn geen invloed op; het is wel een apart besparingspunt.",
                      "Hoe warmer het buiten is, hoe groter dit aandeel lijkt."))
    elif not base:
        r.append(("info", "Basislast voor douchen en koken onbekend",
                  "Zonder basislast bevat 'gas per graaddag' ook warm water en koken, en lijkt het verbruik per graad hoger.",
                  "Vul eventueel 'base_gas_m3_per_day' in de configuratie in."))

    if cmp_pct is not None:
        r.append(("goed" if cmp_pct < -1 else "let_op" if cmp_pct > 3 else "info",
                  f"Gas per graaddag {abs(cmp_pct):.1f}% {'lager' if cmp_pct < 0 else 'hoger'} dan vorige periode".replace(".", ","),
                  "Weer gecorrigeerd, dus echt verschil in rendement of gedrag.", ""))
    return sorted(r, key=lambda x: SEV[x[0]][2])


def reco_html(recos):
    if not recos:
        return '<p class=mut>Geen aanbevelingen.</p>'
    out = []
    for sev, title, why, act in recos:
        icon, label, _ = SEV[sev]
        out.append(f'<li class="reco {sev}"><span class=badge><span aria-hidden=true>{icon}</span> {label}</span>'
                   f'<div><b>{esc(title)}</b><p>{esc(why)}</p>' + (f'<p class=act>→ {esc(act)}</p>' if act else "") + '</div></li>')
    return '<ul class=recos>' + "".join(out) + '</ul>'


# ------------------------------------------------------------------ pagina
def tile(label, value, sub="", delta=None, good_when_negative=True, spark_html=""):
    chip = ""
    if delta is not None:
        good = (delta < 0) == good_when_negative
        arrow = "▼" if delta < 0 else "▲"
        chip = f'<span class="chip {"good" if good else "bad"}">{arrow} {abs(delta):.1f}%'.replace(".", ",") + '</span>'
    return (f'<div class=tile><div class=tl>{esc(label)}</div><div class=tv>{value}</div>'
            f'<div class=ts>{chip}{esc(sub)}</div>{spark_html}</div>')


CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#0b0b0b;--ink2:#52514e;--mut:#7a7873;--line:#e7e6e1;--grid:#ecebe6;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s7:#4a3aa7;--cold:#2a78d6;--warm:#eb6834;--ok:#0b7a3b;--warn:#a86800;--bad:#c23030;--ref:#7a7873;--band:#2a78d614}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121211;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--mut:#908f87;--line:#2c2c2a;--grid:#262624;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s7:#9085e9;--cold:#3987e5;--warm:#d95926;--ok:#4cc27d;--warn:#e0a53a;--bad:#ee7d7d;--ref:#908f87;--band:#3987e51c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif}
a{color:var(--s1)}main{max-width:1100px;margin:auto;padding:20px 16px 60px}
h1{font-size:26px;margin:0}h2{font-size:18px;margin:34px 0 4px}.sub{color:var(--ink2);margin:0 0 12px;font-size:14px}.mut{color:var(--mut)}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}.pill{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 11px;font-size:13px;color:var(--ink2)}
.pill b{color:var(--ink);font-weight:600}.pill.phase{border-color:var(--warn);color:var(--warn)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 14px 10px;position:relative;overflow:hidden}
.tl{font-size:12.5px;color:var(--ink2)}.tv{font-size:30px;font-weight:650;letter-spacing:-.02em;margin:2px 0}.tv small{font-size:14px;font-weight:500;color:var(--ink2)}
.ts{font-size:12.5px;color:var(--mut);min-height:18px}.spark{width:100%;height:26px;margin-top:6px}.spark polyline{fill:none;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.chip{display:inline-block;border-radius:6px;padding:0 6px;margin-right:6px;font-weight:600}.chip.good{color:var(--ok)}.chip.bad{color:var(--bad)}
.recos{list-style:none;padding:0;margin:10px 0;display:grid;gap:10px}
.reco{display:flex;gap:12px;background:var(--card);border:1px solid var(--line);border-left-width:4px;border-radius:12px;padding:12px 14px}
.reco p{margin:3px 0 0;color:var(--ink2);font-size:14px}.reco .act{color:var(--ink);font-weight:500}
.badge{flex:none;align-self:flex-start;font-size:12px;font-weight:700;border-radius:6px;padding:2px 8px;white-space:nowrap}
.kans{border-left-color:var(--s1)}.kans .badge{color:var(--s1);background:#2a78d618}
.let_op{border-left-color:var(--warn)}.let_op .badge{color:var(--warn);background:#e0a53a22}
.goed{border-left-color:var(--ok)}.goed .badge{color:var(--ok);background:#4cc27d1f}
.info{border-left-color:var(--line)}.info .badge{color:var(--ink2);background:var(--grid)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 14px 8px;min-width:0}
.card h3{margin:0;font-size:15px}.card .cap{margin:2px 0 8px;font-size:12.5px;color:var(--mut)}
.chart{width:100%;height:auto;display:block;overflow:visible}.ax{font-size:12px;fill:var(--mut)}.lab{font-size:13px;fill:var(--ink)}.val{font-size:12.5px;fill:var(--ink2)}
.grid{stroke:var(--grid);stroke-width:1}.axis0{stroke:var(--mut);stroke-width:1.2}.ref{stroke:var(--ref);stroke-width:1.5;stroke-dasharray:5 4}.refl{fill:var(--ref)}
.mk{stroke:var(--bad);stroke-dasharray:3 3;opacity:.7}.mkl{fill:var(--bad)}.band{fill:var(--band)}
.ln{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}.dot{stroke:var(--card);stroke-width:2}
.mark{transition:opacity .1s}.hit{fill:transparent;cursor:crosshair}svg:has(.hit:hover) .mark{opacity:.55}.hit:hover{fill:#8882}
.empty{padding:30px 0;text-align:center}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th{color:var(--ink2);font-weight:600;font-size:12px}
td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums}
.tbl{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:6px 6px}
details{margin-top:14px}summary{cursor:pointer;color:var(--ink2);font-size:14px}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:var(--ink2);margin:0 0 6px}.lg i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--bg);padding:6px 10px;border-radius:8px;font-size:13px;max-width:280px;z-index:9;display:none;box-shadow:0 4px 14px #0003}
@media(max-width:560px){.tv{font-size:26px}h1{font-size:22px}.reco{flex-direction:column;gap:6px}}
"""

JS = """
const t=document.getElementById('tip');
function show(e){const el=e.target.closest('[data-tip]');if(!el){t.style.display='none';return}
t.textContent=el.dataset.tip;t.style.display='block';const r=t.getBoundingClientRect();
let x=e.clientX+14,y=e.clientY+14;if(x+r.width>innerWidth-8)x=e.clientX-r.width-14;if(y+r.height>innerHeight-8)y=e.clientY-r.height-14;
t.style.left=x+'px';t.style.top=y+'px'}
document.addEventListener('pointermove',show);document.addEventListener('pointerdown',show);
document.addEventListener('pointerleave',()=>t.style.display='none');
"""


def page(ctx):
    """ctx: latest, history, changes, status, live, cfg; optioneel: opts, active, dry_run_end."""
    latest = ctx.get("latest")
    an = (latest or {}).get("an") or {}
    agg = an.get("agg") or {}
    cmp_pct = (latest or {}).get("cmp_pct")
    days = an.get("days") or []
    cfg = ctx["cfg"]
    monitored = cfg["comfort_monitor_rooms"]

    pills = "".join(f'<span class=pill>{esc(k)} <b>{esc(str(v))}</b></span>' for k, v in (ctx.get("live") or {}).items())
    status = "".join(f'<span class="pill phase">{esc(s)}</span>' for s in ctx["status"])

    labels = [d["date"][8:10] + "/" + d["date"][5:7] for d in days]
    nl = lambda v, d=1: _nl(v, d)  # noqa: E731
    tiles = ""
    outs = [d["avg_out"] for d in days if d.get("avg_out") is not None]
    avg_out = sum(outs) / len(outs) if outs else None
    if agg:
        tiles = "".join([
            tile("Gas per graaddag", f'{_nl(agg.get("gas_per_gd"), 3)} <small>m³</small>', "alleen verwarming", cmp_pct,
                 spark_html=spark([(d["gas_heating_m3"] / d["degree_days"]) if d.get("gas_heating_m3") is not None and d["degree_days"] and d["valid"] else None for d in days], "--s1")),
            tile("Gas totaal", f'{nl(agg.get("gas_total_m3"))} <small>m³</small>', f"{nl(agg.get('degree_days_total'))} graaddagen",
                 spark_html=spark([d["gas_m3"] for d in days], "--s1")),
            tile("Ketelstarts", f'{nl(agg.get("starts_per_day"), 0)} <small>per dag</small>', f"gem. brand {nl(agg.get('mean_burn_min'), 0)} min",
                 spark_html=spark([d["starts"] for d in days], "--s3")),
            tile("Aanvoer", f'{nl(agg.get("flow_mean"), 0)} <small>°C</small>', f"{nl(agg.get('pct_flow_below55'), 0)}% onder 55 °C",
                 spark_html=spark([d["flow_mean"] for d in days], "--s2")),
            *([tile("Gaskosten", f'€ {_nl(agg.get("gas_total_m3", 0) * ctx["price"], 2)}', f"deze periode, € {_nl(ctx['price'], 2)} per m³")] if ctx.get("price") else []),
            tile("Buitentemperatuur", f'{nl(avg_out)} <small>°C</small>', f"laagste dag {nl(min(outs))} · hoogste {nl(max(outs))}" if outs else "",
                 spark_html=spark([d["avg_out"] for d in days], "--s2")),
            tile("Vergelijkbare dagen", f'{agg.get("valid_days", 0)} <small>van {agg.get("days_total", 0)}</small>', f"minimaal {cfg['min_valid_days']} nodig"),
        ])

    # --- dagcharts
    def tip_day(d, extra):
        return f"{d['date'][8:10]}/{d['date'][5:7]}: {extra}"

    gas_chart = bar_chart([d["gas_m3"] for d in days], labels,
                          [tip_day(d, f"{_nl(d['gas_m3'], 2)} m³ gas, {_nl(d['degree_days'])} graaddagen") for d in days], "--s1",
                          ref=an.get("base_load_m3_per_day"), ref_label="basislast douchen/koken")
    thr = cfg["degree_day_base"] - cfg["min_degree_days_per_day"]
    out_chart = line_chart([d["avg_out"] for d in days], labels,
                           [tip_day(d, f"gemiddeld {_nl(d['avg_out'])} °C buiten") for d in days], "--s2",
                           ref=thr, ref_label=f"onder {_nl(thr, 0)} °C telt een dag mee")
    gd_chart = bar_chart([d["degree_days"] for d in days], labels,
                         [tip_day(d, f"{_nl(d['degree_days'])} graaddagen, gemiddeld {_nl(d['avg_out'])} °C buiten") for d in days], "--s2")
    starts_chart = bar_chart([d["starts"] for d in days], labels,
                             [tip_day(d, f"{_nl(d['starts'], 0)} starts, gem. {_nl(d['mean_burn_min'])} min per brand") for d in days], "--s3")
    flow_chart = line_chart([d["flow_mean"] for d in days], labels,
                            [tip_day(d, f"aanvoer gem. {_nl(d['flow_mean'], 0)} °C, p95 {_nl(d['flow_p95'], 0)} °C, {_nl((d['flow_below55_frac'] or 0)*100, 0)}% onder 55") for d in days],
                            "--s2", ref=55, ref_label="55 °C: grens condensatie", ymax=65)
    pts = [(d["degree_days"], d["gas_heating_m3"],
            tip_day(d, f"{_nl(d['gas_heating_m3'], 2)} m³ bij {_nl(d['degree_days'])} graaddagen"))
           for d in days if d["valid"] and d["gas_heating_m3"] is not None]
    scatter = scatter_chart(pts, "--s1")

    # --- trends
    hist = list(reversed(ctx.get("history") or []))
    hl = [h["an"]["start"][8:10] + "/" + h["an"]["start"][5:7] for h in hist]
    marks = {}
    for c in ctx.get("changes") or []:
        if c["source"] == "auto" and hist:
            tday = datetime.fromtimestamp(c["ts"]).date().isoformat()
            marks[max([i for i, h in enumerate(hist) if h["an"]["start"] <= tday] or [0])] = c["label"].split(" ")[0]
    ht = lambda key, f: [f"week {hl[i]}: {f(h['an']['agg'].get(key))}" for i, h in enumerate(hist)]  # noqa: E731
    trend_gas = line_chart([h["an"]["agg"].get("gas_per_gd") for h in hist], hl, ht("gas_per_gd", lambda v: _nl(v, 3) + " m³/graaddag"), "--s1", marks=marks)
    trend_cond = line_chart([h["an"]["agg"].get("pct_flow_below55") for h in hist], hl, ht("pct_flow_below55", lambda v: _nl(v, 0) + "% onder 55 °C"), "--s2", marks=marks, ymin=0, ymax=100)
    trend_note = "" if len(hist) > 1 else '<p class="mut cap">Een trend verschijnt na een paar weekrapporten.</p>'

    split_chart = stacked_chart(days, labels)
    weeks = ctx.get("weeks") or []
    price = ctx.get("price") or 0
    wl = [w["start"][8:10] + "/" + w["start"][5:7] for w in weeks]
    wtip = lambda w: (f"week van {w['start'][8:10]}/{w['start'][5:7]}: {_nl(w['gas_total'], 1)} m³ gas"  # noqa: E731
                      + (f" (€ {_nl(w['gas_total'] * price, 2)})" if price and w["gas_total"] is not None else "")
                      + f", {_nl(w['avg_out'])} °C buiten, {w['valid_days']} vergelijkbare dagen")
    long_gas = bar_chart([w["gas_total"] for w in weeks], wl, [wtip(w) for w in weeks], "--s1")
    long_gd = line_chart([w["gas_per_gd"] for w in weeks], wl,
                         [f"week van {w['start'][8:10]}/{w['start'][5:7]}: " + (_nl(w["gas_per_gd"], 3) + " m³/graaddag" if w["gas_per_gd"] is not None else "geen vergelijkbare dagen") for w in weeks], "--s1")
    long_out = line_chart([w["avg_out"] for w in weeks], wl, [f"week van {w['start'][8:10]}/{w['start'][5:7]}: {_nl(w['avg_out'])} °C" for w in weeks], "--s2")
    long_html = (f'<h2>Langere termijn</h2><p class=sub>Uit het eigen archief van de add-on: {len(ctx.get("weeks") or [])} week(en), '
                 f'dus ook als HA de oude geschiedenis al heeft verwijderd. <a href="export.csv">Download als CSV</a></p>'
                 f'<div class=grid2><div class=card><h3>Gas per week</h3><p class=cap>m³, hele huis.</p>{long_gas}</div>'
                 f'<div class=card><h3>Gas per graaddag per week</h3><p class=cap>m³, alleen verwarming. Lager is zuiniger; alleen weken met vergelijkbare dagen.</p>{long_gd}</div>'
                 f'<div class=card><h3>Buitentemperatuur per week</h3><p class=cap>Gemiddeld in °C.</p>{long_out}</div></div>') if weeks else ""
    dev = deviation_chart(agg.get("rooms") or {}, monitored)
    recos = reco_html(recommendations(ctx))

    drows = "".join(
        f"<tr><td>{esc(d['date'])}</td><td class=num>{_nl(d['avg_out'])}</td><td class=num>{_nl(d['degree_days'])}</td><td class=num>{_nl(d['gas_m3'], 2)}</td>"
        f"<td class=num>{_nl(d['starts'], 0)}</td><td class=num>{_nl(d['mean_burn_min'])}</td><td class=num>{_nl(d['flow_mean'], 0)}</td>"
        f"<td>{'✓' if d['valid'] else esc(', '.join(d['invalid_reasons']))}</td></tr>" for d in days)
    chg = "".join(
        f"<tr><td>{esc(c['when'])}</td><td>{esc(c['label'])}</td><td>{esc(str(c['old']))} → {esc(str(c['new']))}</td>"
        f"<td>{esc(c['source'] or '')}</td><td>{esc(c['status'] or '')}</td><td>{esc(c['reason'] or '')}</td></tr>"
        for c in ctx.get("changes") or []) or '<tr><td colspan=6 class=mut>Nog geen wijzigingen.</td></tr>'

    proposal = (latest or {}).get("proposal")
    prop = ""
    if proposal:
        dry = (latest or {}).get("phase") == "dry_run"
        prop = (f'<li class="reco kans"><span class=badge><span aria-hidden=true>▲</span> Voorstel</span><div><b>{esc(proposal)}</b>'
                f'<p>{"Dry-run: wordt nog niet uitgevoerd." if dry else "Wordt automatisch doorgevoerd en na de wachttijd geëvalueerd."}</p></div></li>')
        recos = recos.replace('<ul class=recos>', '<ul class=recos>' + prop, 1) if "<ul" in recos else recos
    warns = "".join(f"<li>{esc(w)}</li>" for w in an.get("warnings", []))
    advice = ""
    if latest and "\n\n" in latest.get("text", ""):
        advice = f'<h2>Uitleg</h2><div class=card><p>{esc(latest["text"].split(chr(10)+chr(10))[-1])}</p></div>'

    empty = '<p class=mut>Nog geen rapport. Zet <code>run_mode</code> op <code>test_7d</code> of wacht tot het weekrapport.</p>'
    period = f"{an.get('start', '')} t/m {an.get('end', '')}" if an else ""
    return f"""<!doctype html><html lang=nl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SAT Autotune</title><style>{CSS}</style></head><body><main>
<header><h1>SAT Autotune</h1><span class=mut>Analyse van {esc(ctx.get('report_ts') or '–')} · <a href="refresh">Vernieuwen</a></span></header>
<div class=pills>{status}</div><div class=pills>{pills}</div>
<div class=tiles>{tiles or empty}</div>
<h2>Aanbevelingen</h2><p class=sub>Op basis van de cijfers hieronder. De add-on past alleen aan binnen de ingestelde regels.</p>{recos}
{'<ul class=mut>'+warns+'</ul>' if warns else ''}
<h2>Deze periode</h2><p class=sub>{esc(period)}</p>
<div class=grid2>
<div class=card><h3>Gasverbruik per dag</h3><p class=cap>m³, hele huis. De stippellijn is de schatting voor douchen en koken.</p>{gas_chart}</div>
<div class=card><h3>Gas tegenover kou</h3><p class=cap>Verwarmingsgas (m³) per graaddag. Dichter bij de lijn = voorspelbaarder; lager = zuiniger.</p>{scatter}</div>
<div class=card><h3>Waar blijft je gas?</h3><p class=cap>Verdeling per dag (m³): verwarming, warm water en koken. Uit de OTGW (modulatie en warm-water-status), gekalibreerd op je gasmeter.</p>{split_chart}</div>
<div class=card><h3>Buitentemperatuur</h3><p class=cap>Daggemiddelde in °C (de waarde die SAT gebruikt). Alleen dagen onder de stippellijn zijn koud genoeg om te vergelijken.</p>{out_chart}</div>
<div class=card><h3>Graaddagen per dag</h3><p class=cap>Hoe koud het was (stookgrens {_nl(cfg['degree_day_base'], 0)} °C).</p>{gd_chart}</div>
<div class=card><h3>Aanvoertemperatuur</h3><p class=cap>Gemiddeld tijdens branden. Onder de stippellijn condenseert de ketel.</p>{flow_chart}</div>
<div class=card><h3>Ketelstarts per dag</h3><p class=cap>Minder starts met langere branden is rustiger.</p>{starts_chart}</div>
<div class=card><h3>Kamers t.o.v. setpoint</h3><p class=cap>Gemiddelde afwijking in °C. ● = kamer die de add-on bewaakt.</p>{dev}</div>
</div>
{long_html}
{setback_html(ctx)}
<h2>Trend per week</h2><p class=sub>Rode lijnen markeren een wijziging van een instelling.</p>{trend_note}
<div class=grid2>
<div class=card><h3>Gas per graaddag</h3><p class=cap>m³, alleen verwarming. Lager is beter.</p>{trend_gas}</div>
<div class=card><h3>Condenserend</h3><p class=cap>% van de brandtijd met aanvoer onder 55 °C. Hoger is beter.</p>{trend_cond}</div>
</div>
<h2>Experimentlogboek</h2><p class=sub>Alle wijzigingen aan de SAT-instellingen, ook die jij zelf doet.</p>
<div class="tbl scroll"><table><tr><th>Wanneer</th><th>Instelling</th><th>Wijziging</th><th>Door</th><th>Status</th><th>Reden</th></tr>{chg}</table></div>
{advice}
<details><summary>Tabel met alle dagen</summary><div class="tbl scroll" style="margin-top:8px"><table><tr><th>Dag</th><th>Buiten °C</th><th>Graaddagen</th><th>Gas m³</th><th>Starts</th><th>Brand min</th><th>Aanvoer °C</th><th>Vergelijkbaar</th></tr>{drows}</table></div></details>
</main><div id=tip></div><script>{JS}</script></body></html>"""
