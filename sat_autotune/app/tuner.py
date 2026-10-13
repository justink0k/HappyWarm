"""Beslislogica: kandidaat kiezen, grenzen bewaken, evalueren. Zuiver, geen I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .const import LABELS
from .metrics import monitored_cold_frac


@dataclass
class Proposal:
    key: str          # coef | max_setpoint | cycles | opv
    old: float
    new: float
    gain_pct: float
    reason: str


def limits(cfg):
    return {
        "coef": (cfg["coef_min"], cfg["coef_max"], cfg["coef_step"]),
        "max_setpoint": (cfg["max_setpoint_min"], cfg["max_setpoint_max"], cfg["max_setpoint_step"]),
        "cycles": (cfg["cycles_min"], cfg["cycles_max"], 1),
        "opv": (cfg["opv_min"], cfg["opv_max"], cfg["opv_step"]),
    }


def within_limits(cfg, key, value):
    lo, hi, _ = limits(cfg)[key]
    return lo - 1e-9 <= value <= hi + 1e-9


def _parse_hm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def in_quiet_hours(now: datetime, cfg):
    s, e = _parse_hm(cfg["quiet_start"]), _parse_hm(cfg["quiet_end"])
    t = now.time()
    return (t >= s or t < e) if s > e else (s <= t < e)


def phase(now_ts, install_ts, cfg):
    """'dry_run' tijdens de eerste weken na installatie, daarna 'live'."""
    if now_ts < install_ts + cfg["dry_run_weeks"] * 7 * 86400:
        return "dry_run"
    return "live"


def cooldown_over(now_ts, last_change_ts, cfg):
    return last_change_ts is None or now_ts >= last_change_ts + cfg["wait_days"] * 86400


def propose(agg, opts, cfg, rejected, now_ts):
    """Kies de wijziging met de grootste verwachte winst, of None.

    agg: aggregaat over de basisperiode; opts: huidige waarden {key: waarde};
    rejected: {(key, richting): ts} van teruggedraaide wijzigingen.
    De winstschatting is een vuistregel; de echte beslissing valt bij de evaluatie.
    """
    if agg.get("valid_days", 0) < cfg["min_valid_days"] or agg.get("gas_per_gd") is None:
        return None
    cold = monitored_cold_frac(agg, cfg["comfort_monitor_rooms"])
    if cold is None or cold > 0.02:
        return None  # geen ruimte om koeler te gaan stoken / onvoldoende comfortdata
    lim = limits(cfg)
    below55 = (agg.get("pct_flow_below55") or 0) / 100
    flow_p95 = agg.get("flow_p95")
    starts = agg.get("starts_per_day")
    burn = agg.get("mean_burn_min")
    cands = []

    def ok(key, new):
        cur = opts.get(key)
        if cur is None or not within_limits(cfg, key, new) or abs(new - cur) < 1e-9:
            return False
        direction = "down" if new < cur else "up"
        t = rejected.get((key, direction))
        return not (t and now_ts - t < 60 * 86400)

    if "coef" in opts:
        step = lim["coef"][2]
        new = round(opts["coef"] - step, 2)
        if ok("coef", new):
            gain = min(6.0, step / 0.1 * 3.0 * (1 - below55))
            cands.append(Proposal("coef", opts["coef"], new, gain,
                                  f"Lagere aanvoer: nu {below55*100:.0f}% van de brandtijd onder 55 °C; meer condensatie mogelijk"))
    if "max_setpoint" in opts and flow_p95 is not None:
        new = opts["max_setpoint"] - lim["max_setpoint"][2]
        if ok("max_setpoint", new) and new >= flow_p95 + 3:
            gain = 1.0 if below55 < 0.8 else 0.0
            cands.append(Proposal("max_setpoint", opts["max_setpoint"], new, gain,
                                  f"Aanvoer komt zelden boven {flow_p95:.0f} °C; lager maximum begrenst pieken"))
    if "cycles" in opts and starts is not None and burn is not None:
        new = opts["cycles"] - 1
        if ok("cycles", new) and starts > 40 and burn < 8:
            cands.append(Proposal("cycles", opts["cycles"], new, 1.5,
                                  f"{starts:.0f} starts/dag met gemiddeld {burn:.1f} min branden: langere cycli"))
    if "opv" in opts and starts is not None and burn is not None:
        new = opts["opv"] - lim["opv"][2]
        if ok("opv", new) and starts > 50 and burn < 6:
            cands.append(Proposal("opv", opts["opv"], new, 1.2,
                                  f"{starts:.0f} starts/dag met korte branden ({burn:.1f} min)"))
    cands = [c for c in cands if c.gain_pct >= 1.0]
    return max(cands, key=lambda c: c.gain_pct) if cands else None


def evaluate(baseline, after, change, cfg):
    """-> ('keep'|'revert'|'wait', reden). 'wait' = nog te weinig vergelijkbare dagen."""
    if after.get("valid_days", 0) < cfg["min_valid_days"]:
        return "wait", f"Nog maar {after.get('valid_days', 0)} vergelijkbare dagen na de wijziging"
    b, a = baseline.get("gas_per_gd"), after.get("gas_per_gd")
    if not b or a is None:
        return "revert", "Geen gas-per-graaddag om mee te vergelijken (bij twijfel terugdraaien)"
    impr = 100 * (b - a) / b
    bs, as_ = baseline.get("starts_per_day"), after.get("starts_per_day")
    if bs and as_ and as_ > bs * (1 + cfg["max_starts_increase_pct"] / 100):
        return "revert", f"Starts per dag gestegen van {bs:.0f} naar {as_:.0f}"
    cb = monitored_cold_frac(baseline, cfg["comfort_monitor_rooms"]) or 0
    ca = monitored_cold_frac(after, cfg["comfort_monitor_rooms"]) or 0
    if ca > cb + 0.03:
        return "revert", f"Comfort verslechterd: {ca*100:.0f}% van de thuistijd te koud (was {cb*100:.0f}%)"
    if impr < cfg["min_improvement_pct"]:
        return "revert", f"Gas per graaddag {impr:+.1f}% (minimaal {cfg['min_improvement_pct']}% nodig)"
    return "keep", f"Gas per graaddag {impr:+.1f}% beter"


def pendel_check(agg_after, change, cfg):
    """Na een OPV-wijziging: gemiddelde brandduur < 5 min betekent pendelen."""
    if change["key"] != "opv":
        return False
    m = agg_after.get("mean_burn_min")
    return m is not None and m < 5


def describe(p: Proposal):
    return f"{LABELS[p.key]}: {p.old:g} → {p.new:g}"
