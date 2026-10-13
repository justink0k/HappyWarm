"""SAT-opties lezen en wijzigen via de officiële options flow van de config-entry.

Er wordt altijd maar één veld gewijzigd; alle andere velden worden met hun huidige waarde
teruggestuurd en achteraf wordt gecontroleerd dat alleen dat ene veld is veranderd.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("sat_options")


class OptionsError(Exception):
    pass


class RestFlowAPI:
    """Options flow via de REST API van HA (alleen het options-flow pad)."""

    def __init__(self, ha):
        self.ha = ha

    async def start(self, entry_id):
        return await self.ha.rest("POST", "config/config_entries/options/flow", {"handler": entry_id})

    async def step(self, flow_id, user_input):
        return await self.ha.rest("POST", f"config/config_entries/options/flow/{flow_id}", user_input)

    async def abort(self, flow_id):
        try:
            await self.ha.rest("DELETE", f"config/config_entries/options/flow/{flow_id}")
        except Exception as e:  # opruimen mag falen
            log.debug("flow abort: %s", e)


def num(v):
    """'3' -> 3.0, 3 -> 3.0; alles wat geen getal is blijft ongewijzigd."""
    if isinstance(v, bool) or v is None:
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def form_fields(result):
    """Formulier -> {naam: {'value', 'options', 'has_value'}}."""
    out = {}
    for f in result.get("data_schema") or []:
        name = f.get("name")
        if not name:
            continue
        sugg = (f.get("description") or {}).get("suggested_value")
        has_default = "default" in f
        value = sugg if sugg is not None else (f.get("default") if has_default else None)
        opts = None
        if isinstance(f.get("options"), list):
            opts = [o[0] if isinstance(o, (list, tuple)) else o for o in f["options"]]
        elif isinstance(f.get("selector"), dict):
            sel = (f["selector"].get("select") or {}).get("options")
            if sel:
                opts = [o["value"] if isinstance(o, dict) else o for o in sel]
        out[name] = {"value": value, "options": opts, "required": bool(f.get("required"))}
    return out


async def _open_step(api, entry_id, step_id=None):
    """Start de flow en navigeer (indien menu) naar de gevraagde stap. Retourneert (flow_id, form)."""
    res = await api.start(entry_id)
    fid = res["flow_id"]
    if res.get("type") == "menu":
        if step_id is None:
            return fid, res
        res = await api.step(fid, {"next_step_id": step_id})
    return fid, res


async def read_all(api, entry_id):
    """Lees alle opties. Retourneert (snapshot {veld: waarde}, locaties {veld: stapnaam}, formulieren)."""
    snapshot, loc, forms = {}, {}, {}
    fid, res = await _open_step(api, entry_id)
    try:
        if res.get("type") == "menu":
            steps = list(res.get("menu_options") or [])
        else:
            steps = [None]
            _collect(res, snapshot, loc, forms)
    finally:
        await api.abort(fid)
    if steps != [None]:
        for s in steps:
            fid, res = await _open_step(api, entry_id, s)
            try:
                if res.get("type") == "form":
                    _collect(res, snapshot, loc, forms, step=s)
            finally:
                await api.abort(fid)
    return snapshot, loc, forms


def _collect(res, snapshot, loc, forms, step=None):
    ff = form_fields(res)
    forms[step or res.get("step_id")] = ff
    for n, f in ff.items():
        snapshot[n] = f["value"]
        loc[n] = step


def diff(old, new):
    return {k: (old.get(k), new.get(k)) for k in set(old) | set(new) if old.get(k) != new.get(k)}


async def set_field(api, entry_id, field, value):
    """Wijzig precies één veld. Retourneert (voor, na) snapshots. Gooit OptionsError bij elke afwijking."""
    before, loc, forms = await read_all(api, entry_id)
    if field not in before:
        raise OptionsError(f"Veld '{field}' bestaat niet in de SAT-opties")
    step = loc[field]
    form = forms[step or next(iter(forms))]
    cur = before[field]
    if isinstance(cur, bool) or cur is None:
        raise OptionsError(f"Veld '{field}' heeft een onverwacht type ({type(cur).__name__})")
    value = num(value)
    opts = form[field]["options"]
    if isinstance(cur, str):
        # SAT bewaart sommige getallen als tekst (bijv. cycles_per_hour = '3'); zelfde vorm teruggeven
        if not isinstance(value, float):
            raise OptionsError(f"Waarde {value!r} is geen getal voor '{field}'")
        value = str(int(value)) if value.is_integer() else str(value)
    elif isinstance(cur, int):
        value = int(value) if float(value).is_integer() else float(value)
    elif isinstance(cur, float):
        value = float(value)
    if opts and value not in opts and str(value) not in [str(o) for o in opts]:
        raise OptionsError(f"Waarde {value} niet toegestaan voor '{field}' (opties: {opts})")

    user_input = {}
    for n, f in form.items():
        if f["value"] is None:
            if f["required"]:
                raise OptionsError(f"Verplicht veld '{n}' heeft geen huidige waarde; niets gewijzigd")
            continue
        user_input[n] = f["value"]
    user_input[field] = value

    fid, res = await _open_step(api, entry_id, step)
    try:
        if res.get("type") != "form":
            raise OptionsError(f"Onverwacht flow-resultaat: {res.get('type')}")
        res = await api.step(fid, user_input)
    except Exception:
        await api.abort(fid)
        raise
    if res.get("type") == "form" and res.get("errors"):
        await api.abort(fid)
        raise OptionsError(f"SAT weigert de waarde: {res['errors']}")
    if res.get("type") not in ("create_entry",):
        await api.abort(fid)
        raise OptionsError(f"Flow niet afgerond (type {res.get('type')}); niets opgeslagen")

    after, _, _ = await read_all(api, entry_id)
    changed = diff(before, after)
    if set(changed) != {field}:
        raise OptionsError(f"Controle mislukt: gewijzigd {changed}, verwacht alleen '{field}'")
    return before, after


def read_sat_version(config_dir="/homeassistant"):
    p = Path(config_dir) / "custom_components" / "sat" / "manifest.json"
    try:
        return json.loads(p.read_text()).get("version")
    except Exception as e:
        log.warning("SAT-versie niet leesbaar uit %s: %s", p, e)
        return None


def check_compat(cfg, snapshot, sat_version):
    """-> lijst problemen; leeg = ok. Elk probleem betekent alleen-lezen-modus."""
    problems = []
    if sat_version is None:
        problems.append("SAT-versie kon niet worden vastgesteld")
    elif sat_version != cfg["expected_sat_version"]:
        problems.append(f"SAT-versie {sat_version} (verwacht {cfg['expected_sat_version']})")
    for key in ("field_coef", "field_max_setpoint", "field_cycles", "field_opv"):
        if not cfg[key]:
            continue  # bewust uitgeschakelde instelling (leeg veld)
        if cfg[key] not in snapshot:
            problems.append(f"SAT-optie '{cfg[key]}' niet gevonden")
    return problems
