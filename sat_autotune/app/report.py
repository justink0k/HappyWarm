"""Rapport in tekst (push/log) en HTML (Ingress)."""
from __future__ import annotations

import html


def _f(v, fmt="{:.1f}", dash="–"):
    return dash if v is None else fmt.format(v)


def text_report(an, cmp_pct=None, proposal=None, phase=None, advice=None):
    a = an["agg"]
    L = [f"Periode {an['start']} t/m {an['end']} — {a.get('valid_days', 0)}/{a.get('days_total', 0)} vergelijkbare dagen"]
    L.append(f"Gas totaal: {_f(a.get('gas_total_m3'))} m³, graaddagen: {_f(a.get('degree_days_total'))}")
    if an.get("base_load_m3_per_day"):
        L.append(f"Basislast warm water/koken: {an['base_load_m3_per_day']:.2f} m³/dag (afgetrokken)")
    L.append(f"Gas voor verwarming per graaddag: {_f(a.get('gas_per_gd'), '{:.3f}')} m³"
             + (f"  ({cmp_pct:+.1f}% t.o.v. vorige periode)" if cmp_pct is not None else ""))
    L.append(f"Ketelstarts/dag: {_f(a.get('starts_per_day'))}, gemiddelde brandduur: {_f(a.get('mean_burn_min'))} min")
    L.append(f"Aanvoer tijdens branden: {_f(a.get('flow_mean'))} °C, {_f(a.get('pct_flow_below55'), '{:.0f}')}% van de brandtijd onder 55 °C")
    pw, ct = a.get("pwm_frac"), a.get("continuous_frac")
    L.append(f"PWM actief: {_f(None if pw is None else pw*100, '{:.0f}')}% van de tijd; continu branden (≥30 min): {_f(None if ct is None else ct*100, '{:.0f}')}% van de brandtijd")
    for n, r in sorted((a.get("rooms") or {}).items()):
        L.append(f"  {n}: gem. afwijking {r['mean_dev']:+.1f} °C, {r['cold_h']:.0f} u te koud, {r['warm_h']:.0f} u te warm")
    for w in an.get("warnings", []):
        L.append(f"⚠ {w}")
    if proposal:
        label = "Voorstel (dry-run, niet uitgevoerd)" if phase == "dry_run" else "Voorstel"
        L.append(f"{label}: {proposal}")
    if advice:
        L.append("")
        L.append(advice)
    return "\n".join(L)


def push_summary(an, cmp_pct, proposal, phase):
    a = an["agg"]
    parts = [
        f"Gas/graaddag {_f(a.get('gas_per_gd'), '{:.3f}')} m³" + (f" ({cmp_pct:+.0f}%)" if cmp_pct is not None else ""),
        f"{_f(a.get('starts_per_day'), '{:.0f}')} starts/dag, {_f(a.get('mean_burn_min'), '{:.0f}')} min/brand",
        f"{_f(a.get('pct_flow_below55'), '{:.0f}')}% brandtijd <55 °C",
    ]
    msg = "; ".join(parts)
    msg += "\nVoorstel: " + (proposal if proposal else "geen wijziging")
    if proposal and phase == "dry_run":
        msg += " (dry-run)"
    return msg
