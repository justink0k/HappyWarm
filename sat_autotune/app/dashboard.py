"""Dashboard (Ingress): kerncijfers, grafieken als inline SVG, inzichten en logboek. Geen externe libs."""
from __future__ import annotations

import html
from datetime import datetime

e = html.escape
C_GAS, C_GD, C_OK, C_WARN, C_BAD = "#e8743b", "#4a90d9", "#2e9e5b", "#d9a400", "#d64545"


def _f(v, fmt="{:.1f}", dash="–"):
    return dash if v is None else fmt.format(v)


def bars(vals, labels, color, unit="", h=130, marks=None, ref=None):
    """Staafdiagram. vals mag None bevatten. marks = {index: tekst} voor wijzigingen."""
    n = len(vals)
    if not n:
        return "<p class=mut>Geen data</p>"
    w, pad = 560, 26
    mx = max([v for v in vals if v is not None] + [ref or 0, 1e-9]) * 1.1
    bw = (w - pad) / n
    out = [f'<svg viewBox="0 0 {w} {h+34}" class=chart role=img>']
    for i, (v, lb) in enumerate(zip(vals, labels)):
        x = pad + i * bw
        if v is not None:
            bh = v / mx * h
            out.append(f'<rect x="{x+bw*0.15:.1f}" y="{h-bh:.1f}" width="{bw*0.7:.1f}" height="{bh:.1f}" fill="{color}" rx="2"><title>{e(lb)}: {v:.2f} {e(unit)}</title></rect>')
        if n <= 16 or i % 2 == 0:
            out.append(f'<text x="{x+bw/2:.1f}" y="{h+14}" text-anchor="middle" class=ax>{e(lb)}</text>')
        if marks and i in marks:
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h}" stroke="{C_BAD}" stroke-dasharray="3 3"/><text x="{x+3:.1f}" y="10" class=ax fill="{C_BAD}">{e(marks[i])}</text>')
    if ref:
        y = h - ref / mx * h
        out.append(f'<line x1="{pad}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" stroke="{C_WARN}" stroke-dasharray="4 3"/>')
    out.append(f'<text x="0" y="10" class=ax>{mx/1.1:.1f}</text><text x="0" y="{h}" class=ax>0</text></svg>')
    return "".join(out)


def line(vals, labels, color, unit="", h=130, marks=None):
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 1:
        return "<p class=mut>Nog te weinig weken voor een trend.</p>"
    w, pad = 560, 34
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or max(abs(hi), 1) * 0.1
    lo, hi = lo - span * 0.2, hi + span * 0.2
    n = max(len(vals) - 1, 1)
    xy = [(pad + i / n * (w - pad - 8), h - (v - lo) / (hi - lo) * h) for i, v in pts]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    out = [f'<svg viewBox="0 0 {w} {h+34}" class=chart role=img><polyline points="{d}" fill="none" stroke="{color}" stroke-width="2.5"/>']
    for (x, y), (i, v) in zip(xy, pts):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"><title>{e(labels[i])}: {v:.3f} {e(unit)}</title></circle>')
    for i, lb in enumerate(labels):
        x = pad + i / n * (w - pad - 8)
        out.append(f'<text x="{x:.1f}" y="{h+14}" text-anchor="middle" class=ax>{e(lb)}</text>')
        if marks and i in marks:
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h}" stroke="{C_BAD}" stroke-dasharray="3 3"/><text x="{x+3:.1f}" y="10" class=ax fill="{C_BAD}">{e(marks[i])}</text>')
    out.append(f'<text x="0" y="10" class=ax>{hi:.2f}</text><text x="0" y="{h}" class=ax>{lo:.2f}</text></svg>')
    return "".join(out)


def insights(agg, cmp_pct, cfg, an):
    """Korte, bruikbare conclusies uit de cijfers (rule-based, geen AI)."""
    out = []
    below = agg.get("pct_flow_below55")
    if below is not None:
        if below < 30:
            out.append(("warn", f"Maar {below:.0f}% van de brandtijd onder 55 °C: de ketel condenseert weinig. Een lagere stooklijn kan gas besparen."))
        elif below > 70:
            out.append(("ok", f"{below:.0f}% van de brandtijd onder 55 °C: de ketel condenseert goed."))
    s, b = agg.get("starts_per_day"), agg.get("mean_burn_min")
    if s is not None and b is not None:
        if s > 40 and b < 8:
            out.append(("warn", f"{s:.0f} starts per dag met gemiddeld {b:.0f} minuten branden: veel korte cycli, dat kost rendement en slijtage."))
        elif s < 25 and b > 15:
            out.append(("ok", f"Rustige regeling: {s:.0f} starts/dag met {b:.0f} min per brand."))
    ct = agg.get("continuous_frac")
    if ct is not None and ct > 0.5:
        out.append(("ok", f"{ct*100:.0f}% van de brandtijd is een lange, rustige brand (≥30 min)."))
    for n, r in (agg.get("rooms") or {}).items():
        if r["mean_dev"] < -1:
            out.append(("warn", f"{n.capitalize()} zit gemiddeld {abs(r['mean_dev']):.1f} °C onder het setpoint van SAT (let op: slaapkamer/hal zijn vaak bewust koeler)."))
        elif r["mean_dev"] > 1:
            out.append(("warn", f"{n.capitalize()} zit gemiddeld {r['mean_dev']:.1f} °C boven het setpoint."))
    if cmp_pct is not None:
        out.append(("ok" if cmp_pct < 0 else "warn", f"Gas per graaddag is {abs(cmp_pct):.1f}% {'lager' if cmp_pct < 0 else 'hoger'} dan de vorige periode (weer gecorrigeerd)."))
    if an.get("base_load_m3_per_day"):
        out.append(("info", f"Douchen en koken: ruim {an['base_load_m3_per_day']:.1f} m³/dag is als basislast afgetrokken, zodat de cijfers over verwarming gaan."))
    return out


def tile(label, value, sub="", cls=""):
    return f'<div class="tile {cls}"><div class=tl>{e(label)}</div><div class=tv>{value}</div><div class=ts>{sub}</div></div>'


def page(ctx):
    """ctx: latest, history(list), changes(list), status(list), live(dict), cfg."""
    latest = ctx.get("latest")
    an = (latest or {}).get("an") or {}
    agg = an.get("agg") or {}
    cmp_pct = (latest or {}).get("cmp_pct")
    days = an.get("days") or []
    cfg = ctx["cfg"]

    tiles = ""
    if agg:
        tr = "" if cmp_pct is None else f"<span class={'good' if cmp_pct<0 else 'bad'}>{cmp_pct:+.1f}% vs vorige</span>"
        tiles = "".join([
            tile("Gas per graaddag", _f(agg.get("gas_per_gd"), "{:.3f}") + " m³", tr or "alleen verwarming"),
            tile("Gas totaal", _f(agg.get("gas_total_m3")) + " m³", f"{_f(agg.get('degree_days_total'))} graaddagen"),
            tile("Starts per dag", _f(agg.get("starts_per_day"), "{:.0f}"), f"gem. brand {_f(agg.get('mean_burn_min'), '{:.0f}')} min"),
            tile("Aanvoer", _f(agg.get("flow_mean"), "{:.0f}") + " °C", f"{_f(agg.get('pct_flow_below55'), '{:.0f}')}% onder 55 °C"),
            tile("Vergelijkbare dagen", f"{agg.get('valid_days', 0)}/{agg.get('days_total', 0)}", "min. " + str(cfg["min_valid_days"]) + " nodig"),
        ])
    live = ctx.get("live") or {}
    livehtml = "".join(f"<span class=chip>{e(k)}: <b>{e(str(v))}</b></span>" for k, v in live.items())

    st = "".join(f"<li class=warnl>{e(s)}</li>" for s in ctx["status"])
    ins = "".join(f'<li class="in-{k}">{e(t)}</li>' for k, t in insights(agg, cmp_pct, cfg, an)) if agg else ""

    labels = [d["date"][8:10] + "/" + d["date"][5:7] for d in days]
    gas_chart = bars([d["gas_m3"] for d in days], labels, C_GAS, "m³", ref=an.get("base_load_m3_per_day"))
    gd_chart = bars([d["degree_days"] for d in days], labels, C_GD, "graaddagen")
    starts_chart = bars([d["starts"] for d in days], labels, C_BAD, "starts")
    burn_chart = bars([d["mean_burn_min"] for d in days], labels, C_OK, "min")

    hist = list(reversed(ctx.get("history") or []))
    hlabels = [h["an"]["start"][8:10] + "/" + h["an"]["start"][5:7] for h in hist]
    marks = {}
    for c in ctx.get("changes") or []:
        if c["source"] == "auto" and hist:
            t = datetime.fromtimestamp(c["ts"]).date().isoformat()
            idx = max([i for i, h in enumerate(hist) if h["an"]["start"] <= t] or [0])
            marks[idx] = c["label"].split(" ")[0]
    trend = line([h["an"]["agg"].get("gas_per_gd") for h in hist], hlabels, C_GAS, "m³/graaddag", marks=marks)
    trend_starts = line([h["an"]["agg"].get("starts_per_day") for h in hist], hlabels, C_BAD, "starts/dag")
    trend_below = line([h["an"]["agg"].get("pct_flow_below55") for h in hist], hlabels, C_OK, "%")

    rooms = "".join(
        f"<tr><td>{e(n)}</td><td>{r['mean_dev']:+.1f} °C</td><td>{r['cold_h']:.0f} u</td><td>{r['warm_h']:.0f} u</td>"
        f"<td>{r['cold_home_frac']*100:.0f}%</td></tr>" for n, r in sorted((agg.get("rooms") or {}).items()))
    drows = "".join(
        f"<tr><td>{e(d['date'])}</td><td>{_f(d['avg_out'])}</td><td>{_f(d['degree_days'])}</td><td>{_f(d['gas_m3'], '{:.2f}')}</td>"
        f"<td>{_f(d['starts'], '{:.0f}')}</td><td>{_f(d['mean_burn_min'])}</td><td>{_f(d['flow_mean'], '{:.0f}')}</td>"
        f"<td>{'✓' if d['valid'] else e(', '.join(d['invalid_reasons']))}</td></tr>" for d in days)
    chg = "".join(
        f"<tr><td>{e(c['when'])}</td><td>{e(c['label'])}</td><td>{e(str(c['old']))} → {e(str(c['new']))}</td>"
        f"<td>{e(c['source'] or '')}</td><td>{e(c['status'] or '')}</td><td>{e(c['reason'] or '')}</td></tr>"
        for c in ctx.get("changes") or []) or "<tr><td colspan=6 class=mut>Nog geen wijzigingen.</td></tr>"
    warns = "".join(f"<li class=warnl>{e(w)}</li>" for w in an.get("warnings", []))
    proposal = (latest or {}).get("proposal")
    prop = f"<p><b>Voorstel:</b> {e(proposal)} {'<span class=mut>(dry-run, wordt niet uitgevoerd)</span>' if (latest or {}).get('phase')=='dry_run' else ''}</p>" if proposal else "<p class=mut>Geen wijziging voorgesteld.</p>"
    advice = ""
    if latest and "\n\n" in latest.get("text", ""):
        advice = f"<h2>Uitleg</h2><p>{e(latest['text'].split(chr(10)+chr(10))[-1])}</p>"

    return f"""<!doctype html><html lang=nl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SAT Autotune</title>
<style>
:root{{--bg:#fff;--fg:#1c1c1e;--mut:#6b7280;--card:#f4f5f7;--line:#0001}}
@media(prefers-color-scheme:dark){{:root{{--bg:#161618;--fg:#ececec;--mut:#9aa0a6;--card:#232326;--line:#fff1}}}}
body{{font:15px system-ui,sans-serif;margin:0;padding:16px;background:var(--bg);color:var(--fg);max-width:1000px;margin:auto}}
h1{{margin:0 0 4px}}h2{{margin:28px 0 8px;font-size:17px}}.mut{{color:var(--mut)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}}
.tile{{background:var(--card);border-radius:12px;padding:12px}}.tl{{font-size:12px;color:var(--mut)}}.tv{{font-size:24px;font-weight:600}}.ts{{font-size:12px;color:var(--mut)}}
.good{{color:{C_OK}}}.bad{{color:{C_BAD}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}}
.card{{background:var(--card);border-radius:12px;padding:12px}}.card h3{{margin:0 0 6px;font-size:14px}}
.chart{{width:100%;height:auto}}.ax{{font-size:10px;fill:var(--mut)}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}}
.chip{{display:inline-block;background:var(--card);border-radius:20px;padding:3px 10px;margin:2px 4px 2px 0;font-size:13px}}
li{{margin:4px 0}}.in-warn::marker{{color:{C_WARN}}}.in-ok::marker{{color:{C_OK}}}.warnl{{color:{C_BAD}}}
.scroll{{overflow-x:auto}}
</style></head><body>
<h1>SAT Autotune</h1>
<div>{livehtml}</div>
<ul>{st}</ul>
<div class=tiles>{tiles or '<p class=mut>Nog geen rapport. Zet run_mode op test_7d of wacht tot het weekrapport.</p>'}</div>
{'<h2>Wat valt op</h2><ul>'+ins+warns+'</ul>' if agg else ''}
<h2>Voorstel</h2>{prop}
<h2>Deze periode ({e(an.get('start',''))} t/m {e(an.get('end',''))})</h2>
<div class=grid>
<div class=card><h3>Gasverbruik per dag (m³) <span class=mut>— oranje stippellijn = basislast koken/douchen</span></h3>{gas_chart}</div>
<div class=card><h3>Graaddagen per dag</h3>{gd_chart}</div>
<div class=card><h3>Ketelstarts per dag</h3>{starts_chart}</div>
<div class=card><h3>Gemiddelde brandduur (min)</h3>{burn_chart}</div>
</div>
<h2>Trend over weken <span class="mut" style="font-weight:400">(rode lijn = wijziging)</span></h2>
<div class=grid>
<div class=card><h3>Gas per graaddag (m³) — lager is beter</h3>{trend}</div>
<div class=card><h3>Starts per dag</h3>{trend_starts}</div>
<div class=card><h3>% brandtijd onder 55 °C (condensatie)</h3>{trend_below}</div>
</div>
<h2>Comfort per kamer (t.o.v. setpoint van SAT)</h2>
<div class=scroll><table><tr><th>Kamer</th><th>Gem. afwijking</th><th>Te koud</th><th>Te warm</th><th>Te koud als thuis</th></tr>{rooms}</table></div>
<h2>Experimentlogboek</h2>
<div class=scroll><table><tr><th>Wanneer</th><th>Instelling</th><th>Wijziging</th><th>Door</th><th>Status</th><th>Reden</th></tr>{chg}</table></div>
{advice}
<h2>Dagdetails</h2>
<div class=scroll><table><tr><th>Dag</th><th>Buiten °C</th><th>Graaddagen</th><th>Gas m³</th><th>Starts</th><th>Brand min</th><th>Aanvoer</th><th>Vergelijkbaar</th></tr>{drows}</table></div>
<p class=mut>Bijgewerkt {datetime.now().strftime('%d-%m %H:%M')}. Fase: {e(str((latest or {}).get('phase') or '–'))}.</p>
</body></html>"""
