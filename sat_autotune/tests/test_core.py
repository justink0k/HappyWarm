import asyncio
import pathlib
import re
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import config, dashboard, metrics as m, sat_options as so, tuner  # noqa: E402

TZ = ZoneInfo("Europe/Amsterdam")
CFG = config.load("/nonexistent")


def t(d, h=0):
    return datetime(d.year, d.month, d.day, h, tzinfo=TZ).timestamp()


def synth_data(days=14, start=date(2026, 10, 1), starts_per_day=30, flow=50.0, room=20.0):
    """Synthetische data: 8 °C buiten, ketel brandt 4 x 15 min per dag."""
    from datetime import timedelta
    d0 = t(start)
    end = d0 + days * 86400
    flame, flowv, starts, gas = [(d0, 0)], [(d0, flow)], [(d0, 1000.0)], []
    n = 0
    for i in range(days):
        for k in range(4):
            s = d0 + i * 86400 + (6 + k * 4) * 3600
            flame += [(s, 1), (s + 900, 0)]
            n += starts_per_day / 4
            starts.append((s, 1000.0 + n))
        for h in range(24):
            gas.append((d0 + i * 86400 + h * 3600, 0.5 + (0.2 if h in (7, 19) else 0)))  # 12+ m3 / dag
    return {
        "outside": [(d0, 8.0)], "setpoint": [(d0, 20.0)], "preset": [(d0, "home")], "pwm": [(d0, "idle")],
        "flame": flame, "flow": flowv, "starts": starts, "manual": [(d0, 0)], "person": [(d0, 1)],
        "rooms": {"woonkamer": [(d0, room)], "kantoor": [(d0, room)], "slaapkamer": [(d0, 17.0)], "hal": [(d0, 19.0)]},
        "gas_daily": m.gas_per_day(gas, TZ),
    }, start, start + timedelta(days=days - 1)


def test_analyze_basics():
    data, a, b = synth_data()
    an = m.analyze(data, CFG, a, b, TZ, base_load=2.0)
    agg = an["agg"]
    assert agg["valid_days"] == 14
    assert agg["degree_days"] == pytest.approx(14 * 10)
    # 0.5*24 + 0.4 = 12.4 m3/dag; basislast 2.0 afgetrokken
    assert agg["gas_per_gd"] == pytest.approx(10.4 / 10, rel=1e-3)
    assert agg["gas_per_gd_raw"] == pytest.approx(1.24, rel=1e-3)
    assert agg["mean_burn_min"] == pytest.approx(15)
    assert agg["flow_mean"] == pytest.approx(50)
    assert agg["pct_flow_below55"] == pytest.approx(100)
    assert agg["starts_per_day"] == pytest.approx(30, abs=1)


def test_normal_workday_away_does_not_invalidate():
    data, a, b = synth_data(days=2)
    d0 = t(a)
    data["person"] = [(d0, 1), (d0 + 8 * 3600, 0), (d0 + 17 * 3600, 1)]  # 9 uur weg = 37%
    an = m.analyze(data, CFG, a, b, TZ)
    assert an["days"][0]["valid"]


def test_base_load_fallback():
    from datetime import timedelta
    gas = {date(2026, 7, 1) + timedelta(days=i): 1.0 + i * 0.1 for i in range(40)}
    assert m.estimate_base_load_fallback(gas) == pytest.approx(1.5, abs=0.2)
    assert m.estimate_base_load_fallback({date(2026, 7, 1): 1.0}) is None


def test_away_and_few_degree_days_invalid():
    data, a, b = synth_data(days=3)
    data["person"] = [(t(a), 0)]  # hele tijd weg
    an = m.analyze(data, CFG, a, b, TZ)
    assert an["agg"]["valid_days"] == 0
    assert all("veel afwezig" in d["invalid_reasons"] for d in an["days"])
    data, a, b = synth_data(days=3)
    data["outside"] = [(t(a), 16.0)]  # 2 graaddagen < 5
    an = m.analyze(data, CFG, a, b, TZ)
    assert an["agg"]["valid_days"] == 0


def test_base_load_excludes_heating_days():
    from datetime import timedelta
    gas = {date(2026, 7, 1) + timedelta(days=i): 1.5 for i in range(10)}
    gas[date(2026, 12, 1)] = 15.0
    gd = {d: 0.0 for d in gas if d.month == 7}
    gd[date(2026, 12, 1)] = 12.0
    assert m.estimate_base_load(gas, gd) == pytest.approx(1.5)
    assert m.estimate_base_load({date(2026, 7, 1): 1.0}, {date(2026, 7, 1): 0}) is None


def test_comfort_cold_detection():
    data, a, b = synth_data(days=2, room=18.5)  # 1.5 °C onder setpoint 20
    an = m.analyze(data, CFG, a, b, TZ)
    r = an["days"][0]["rooms"]["woonkamer"]
    assert r["mean_dev"] == pytest.approx(-1.5)
    assert r["cold_h"] == pytest.approx(24)
    st = m.longest_cold_streak(data["rooms"]["woonkamer"], data["setpoint"], data["person"], t(a), t(a) + 3 * 3600, 1.0)
    assert st == pytest.approx(3 * 3600)
    data["person"] = [(t(a), 0)]
    assert m.longest_cold_streak(data["rooms"]["woonkamer"], data["setpoint"], data["person"], t(a), t(a) + 3 * 3600, 1.0) == 0


def test_counter_reset_ignored():
    assert m.counter_delta([(0, 100.0), (50, 5.0)], 0, 100) is None


# ---------------------------------------------------------------- tuner


def agg(**kw):
    base = {"valid_days": 10, "gas_per_gd": 1.0, "starts_per_day": 30, "mean_burn_min": 15,
            "pct_flow_below55": 10, "flow_p95": 58, "rooms": {"woonkamer": {"cold_home_frac": 0.0, "mean_dev": 0}, "kantoor": {"cold_home_frac": 0.0, "mean_dev": 0}}}
    base.update(kw)
    return base


def test_propose_lowers_curve_within_limits():
    p = tuner.propose(agg(), {"coef": 1.8, "max_setpoint": 60.0, "cycles": 3.0, "opv": 45.0}, CFG, {}, time.time())
    assert p and p.key == "coef" and p.new == pytest.approx(1.7)
    assert tuner.within_limits(CFG, p.key, p.new)


def test_propose_respects_lower_limit_and_comfort():
    assert tuner.propose(agg(), {"coef": 1.0}, CFG, {}, time.time()) is None
    bad = agg(rooms={"woonkamer": {"cold_home_frac": 0.1, "mean_dev": -1}})
    assert tuner.propose(bad, {"coef": 1.8}, CFG, {}, time.time()) is None
    assert tuner.propose(agg(valid_days=3), {"coef": 1.8}, CFG, {}, time.time()) is None


def test_rejected_not_retried():
    now = time.time()
    assert tuner.propose(agg(), {"coef": 1.8}, CFG, {("coef", "down"): now - 86400}, now) is None


def test_cycles_only_down_to_min():
    a = agg(starts_per_day=55, mean_burn_min=5, pct_flow_below55=95)  # curve heeft nauwelijks winst
    p = tuner.propose(a, {"coef": 1.0, "cycles": 4.0, "opv": 45.0}, CFG, {}, time.time())
    assert p and p.key in ("cycles", "opv") and tuner.within_limits(CFG, p.key, p.new)
    assert tuner.propose(a, {"coef": 1.0, "cycles": 3.0, "opv": 42.0}, CFG, {}, time.time()) is None


def test_evaluate():
    b = agg()
    assert tuner.evaluate(b, agg(gas_per_gd=0.95), {}, CFG)[0] == "keep"
    assert tuner.evaluate(b, agg(gas_per_gd=0.995), {}, CFG)[0] == "revert"
    assert tuner.evaluate(b, agg(gas_per_gd=0.9, starts_per_day=45), {}, CFG)[0] == "revert"
    assert tuner.evaluate(b, agg(valid_days=3), {}, CFG)[0] == "wait"
    worse = agg(gas_per_gd=0.9, rooms={"woonkamer": {"cold_home_frac": 0.2, "mean_dev": -1}})
    assert tuner.evaluate(b, worse, {}, CFG)[0] == "revert"


def test_pendel_and_phase_and_quiet():
    assert tuner.pendel_check({"mean_burn_min": 4}, {"key": "opv"}, CFG)
    assert not tuner.pendel_check({"mean_burn_min": 4}, {"key": "coef"}, CFG)
    now = time.time()
    assert tuner.phase(now, now - 10 * 86400, CFG) == "dry_run"
    assert tuner.phase(now, now - 29 * 86400, CFG) == "live"
    assert tuner.in_quiet_hours(datetime(2026, 10, 14, 23, 0), CFG)
    assert tuner.in_quiet_hours(datetime(2026, 10, 14, 6, 59), CFG)
    assert not tuner.in_quiet_hours(datetime(2026, 10, 14, 7, 0), CFG)
    assert not tuner.cooldown_over(now, now - 5 * 86400, CFG)
    assert tuner.cooldown_over(now, now - 15 * 86400, CFG)


# ---------------------------------------------------------------- options flow (nep-HA)


class FakeSAT:
    """Menu-gebaseerde options flow zoals SAT: elke stap beheert een aantal velden."""

    def __init__(self):
        self.opts = {
            "heating_curve_coefficient": 1.8, "maximum_setpoint": 60, "cycles_per_hour": 3,
            "overshoot_protection_value": 45, "target_temperature_step": 0.5, "cycles_text": "3",
            "mode": "pid", "window_sensors": ["a", "b"], "name": "Living",
        }
        self.steps = {
            "general": ["name", "target_temperature_step", "mode"],
            "advanced": ["heating_curve_coefficient", "maximum_setpoint", "cycles_per_hour", "cycles_text", "overshoot_protection_value", "window_sensors"],
        }
        self.flows = {}
        self.n = 0
        self.log = []

    def schema(self, step):
        return [{"name": f, "required": True, "description": {"suggested_value": self.opts[f]}} for f in self.steps[step]]

    async def start(self, entry_id):
        self.n += 1
        fid = f"f{self.n}"
        self.flows[fid] = None
        return {"flow_id": fid, "type": "menu", "menu_options": list(self.steps)}

    async def step(self, fid, ui):
        if "next_step_id" in ui:
            self.flows[fid] = ui["next_step_id"]
            return {"flow_id": fid, "type": "form", "step_id": ui["next_step_id"], "data_schema": self.schema(ui["next_step_id"])}
        step = self.flows[fid]
        assert set(ui) == set(self.steps[step]), "formulier moet volledig worden teruggestuurd"
        self.log.append(dict(ui))
        self.opts.update(ui)
        return {"flow_id": fid, "type": "create_entry"}

    async def abort(self, fid):
        self.flows.pop(fid, None)


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_set_field_changes_only_one_field():
    api = FakeSAT()
    before = dict(api.opts)
    b, a = run(so.set_field(api, "e1", "heating_curve_coefficient", 1.7))
    assert so.diff(before, api.opts) == {"heating_curve_coefficient": (1.8, 1.7)}
    assert set(so.diff(b, a)) == {"heating_curve_coefficient"}
    # terugzetten laat alles weer identiek
    run(so.set_field(api, "e1", "heating_curve_coefficient", 1.8))
    assert api.opts == before


def test_set_field_text_number_and_revert_with_string():
    api = FakeSAT()
    api.opts["cycles_per_hour"] = "3"
    run(so.set_field(api, "e1", "cycles_per_hour", 4.0))
    assert api.opts["cycles_per_hour"] == "4"
    run(so.set_field(api, "e1", "cycles_per_hour", "3"))  # terugdraaien met oude (tekst)waarde
    assert api.opts["cycles_per_hour"] == "3"
    assert so.num("3") == 3.0 and so.num("00:01:00") == "00:01:00" and so.num(None) is None


def test_disabled_field_skips_compat():
    cfg = dict(CFG, field_opv="")
    snap = {"heating_curve_coefficient": 1, "maximum_setpoint": 1, "cycles_per_hour": "3"}
    assert so.check_compat(cfg, snap, "4.2.0") == []


def test_set_field_integer_type_preserved():
    api = FakeSAT()
    run(so.set_field(api, "e1", "maximum_setpoint", 55.0))
    assert api.opts["maximum_setpoint"] == 55 and isinstance(api.opts["maximum_setpoint"], int)


def test_set_field_unknown_field_changes_nothing():
    api = FakeSAT()
    with pytest.raises(so.OptionsError):
        run(so.set_field(api, "e1", "bestaat_niet", 1))
    assert api.log == []


def test_compat_checks():
    snap = {"heating_curve_coefficient": 1, "maximum_setpoint": 1, "cycles_per_hour": 1, "overshoot_protection_value": 1}
    assert so.check_compat(CFG, snap, "4.2.0") == []
    assert so.check_compat(CFG, snap, "4.3.0")
    assert so.check_compat(CFG, snap, None)
    snap.pop("cycles_per_hour")
    assert so.check_compat(CFG, snap, "4.2.0")


# ---------------------------------------------------------------- harde eisen


def test_no_mqtt_and_whitelist():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for f in root.glob("*.py"):
        src = f.read_text().lower()
        assert not re.search(r"(import|from)\s+(paho|aiomqtt|asyncio_mqtt)", src), f.name
        assert "mqtt/publish" not in src and "services/mqtt" not in src, f.name
    from app import ha_client
    assert not any("mqtt" in s for s in ha_client.WS_ALLOWED)
    assert not any("mqtt" in s for s in ha_client.REST_ALLOWED_PREFIXES)

    class Dummy(ha_client.HA):
        pass

    h = Dummy(token="x")
    with pytest.raises(ha_client.HAError):
        run(h.rest("POST", "services/mqtt/publish", {}))
    with pytest.raises(ha_client.HAError):
        run(h.rest("POST", "states/climate.living_room", {}))
    with pytest.raises(ha_client.HAError):
        run(h.ws_call({"type": "call_service"}))


def test_no_secrets_logged():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for f in root.glob("*.py"):
        for line in f.read_text().splitlines():
            if "log." in line:
                assert "api_key" not in line and "token" not in line.lower(), (f.name, line)


def test_dashboard_renders():
    data, a, b = synth_data()
    an = m.analyze(data, CFG, a, b, TZ, base_load=2.0)
    rep = {"an": an, "text": "x\n\nUitleg hier", "cmp_pct": -3.2, "proposal": "stooklijn 1,8 → 1,7", "phase": "dry_run"}
    ch = [{"when": "01-10", "label": "stooklijncoëfficiënt", "old": 1.8, "new": 1.7, "source": "auto", "status": "kept", "reason": "r", "ts": time.time()}]
    h = dashboard.page({"latest": rep, "history": [rep, rep], "changes": ch, "status": ["ok"], "live": {"vlam": "off"}, "cfg": CFG})
    assert "Gas per graaddag" in h and "<svg" in h and "Uitleg hier" in h
    assert "Nog geen rapport" in dashboard.page({"latest": None, "history": [], "changes": [], "status": [], "live": {}, "cfg": CFG})


def test_gas_split_and_opv_labels():
    data, a, b = synth_data(days=2)
    d0 = t(a)
    data["consumption"] = [(d0, 1.2)]          # m3/h tijdens brand
    data["dhw"] = [(d0, 0), (d0 + 6 * 3600, 1), (d0 + 6 * 3600 + 900, 0)]  # eerste brand (15 min) is warm water
    an = m.analyze(data, CFG, a, b, TZ)
    d = an["days"][0]
    assert d["boiler_m3"] == pytest.approx(4 * 0.25 * 1.2)      # 4 branden x 15 min x 1,2 m3/h
    assert d["dhw_m3"] == pytest.approx(0.25 * 1.2)
    assert d["ch_m3"] == pytest.approx(3 * 0.25 * 1.2)
    assert an["agg"]["split_dhw_m3"] == pytest.approx(0.3)
    data["dhw"] = None
    assert m.analyze(data, CFG, a, b, TZ)["days"][0]["dhw_m3"] is None


def test_dashboard_with_split_and_opv():
    data, a, b = synth_data(days=3)
    d0 = t(a)
    data["consumption"] = [(d0, 1.2)]
    data["dhw"] = [(d0, 0), (d0 + 6 * 3600, 1), (d0 + 6 * 3600 + 900, 0)]
    an = m.analyze(data, CFG, a, b, TZ, base_load=2.0)
    rep = {"an": an, "text": "x", "cmp_pct": None, "proposal": None, "phase": "live"}
    h = dashboard.page({"latest": rep, "history": [rep], "changes": [], "status": [], "live": {}, "cfg": CFG, "opts": {}, "opv": 45.0, "active": True})
    assert "Waar blijft je gas" in h and "OPV staat op 45" in h and "warm water" in h


def test_dhw_candidate_scoring():
    d0 = 0.0
    flame = [(d0, 0)]
    cand_dhw = [(d0, 0)]
    for k in range(6):  # korte warm-water-brandjes
        st = d0 + k * 10000
        flame += [(st, 1), (st + 600, 0)]
        cand_dhw += [(st, 1), (st + 600, 0)]
    end = d0 + 60000
    always_on = [(d0, 1)]
    never = [(d0, 0)]
    assert m.score_dhw_candidate(cand_dhw, flame, d0, end)["ok"]
    assert not m.score_dhw_candidate(always_on, flame, d0, end)["ok"]   # bijv. *_enable
    assert not m.score_dhw_candidate(never, flame, d0, end)["ok"]
    no_flame = [(d0, 0)]
    assert not m.score_dhw_candidate(cand_dhw, no_flame, d0, end)["ok"]  # schakelt zonder vlam
