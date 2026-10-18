"""SAT Autotune: planning, vangnetten en opstarten."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import advisor, config, ha_data, metrics as m, report, sat_options as so, tuner
from .const import ENT, LABELS, NOTIFY_SERVICE, REVERT_ACTION, ROOMS, SENSOR_PREFIX, TUNABLE
from .db import DB
from .ha_client import HA, HAError

log = logging.getLogger("autotune")
TICK = 300
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Engine:
    def __init__(self, cfg, ha, db):
        self.cfg, self.ha, self.db = cfg, ha, db
        self.tz = ZoneInfo("Europe/Amsterdam")
        self.entry_id = None
        self.api = so.RestFlowAPI(ha)
        self.readonly_reason = None
        self.loc = {}
        self.status = []
        self.dhw = None

    # ------------------------------------------------------------ opstarten
    async def start(self):
        cfgws = await self.ha.ws_call({"type": "get_config"})
        self.tz = ZoneInfo(cfgws.get("time_zone", "Europe/Amsterdam"))
        if self.db.get("install_ts") is None:
            self.db.set("install_ts", time.time())
        try:
            self.dhw, table = await ha_data.discover_dhw(self.ha, self.cfg, self.tz)
        except Exception as e:
            log.warning("Warm-water-sensor zoeken mislukt: %s", e)
            self.dhw, table = None, []
        for c, sc in table:
            log.info("DHW-kandidaat %s: wisselingen=%s, aan=%s, vlam bij aan=%s%s", c, sc["toggles"],
                     "–" if sc["frac_on"] is None else f"{sc['frac_on']*100:.1f}%",
                     "–" if sc["p_flame"] is None else f"{sc['p_flame']*100:.0f}%", "  <== gekozen" if c == self.dhw else "")
        if self.dhw:
            log.info("Warm-water-sensor: %s", self.dhw)
        else:
            log.warning("Geen betrouwbare warm-water-sensor gevonden; verdeling verwarming/warm water is niet mogelijk. "
                        "Zet 'entity_dhw' in de configuratie (kandidaten staan hierboven).")
            self.status.append("Warm-water-sensor niet gevonden (entity_dhw instellen voor de gasverdeling)")
        try:
            depth = await ha_data.history_depth(self.ha, self.tz)
        except Exception as e:
            log.warning("Geschiedenisdiepte bepalen mislukt: %s", e)
            depth = None
        if depth is not None:
            log.info("HA bewaart ongeveer %.0f dagen geschiedenis", depth)
            need = 2 * self.cfg["wait_days"] + 2
            if depth < need:
                msg = (f"HA bewaart maar ~{depth:.0f} dagen geschiedenis; de add-on heeft ~{need} nodig. "
                       "Zet in configuration.yaml: recorder: purge_keep_days: 35")
                log.warning(msg)
                self.status.append(msg)
        missing = await ha_data.check_entities(self.ha)
        if missing:
            self.status.append(f"Ontbrekende entiteiten: {', '.join(missing)}")
        entries = await self.ha.ws_call({"type": "config_entries/get", "domain": "sat"})
        entries = [e for e in entries if e.get("domain") == "sat"]
        if not entries:
            self.readonly_reason = "SAT config-entry niet gevonden"
        else:
            self.entry_id = entries[0]["entry_id"]
            try:
                snap, self.loc, _ = await so.read_all(self.api, self.entry_id)
                self.db.set("opts_known", True)
                if self.cfg["field_opv"] and self.cfg["field_opv"] not in snap:
                    log.warning("SAT-optie '%s' (OPV) bestaat niet in deze SAT-versie; OPV wordt niet aangepast",
                                self.cfg["field_opv"])
                    self.cfg["field_opv"] = ""
                problems = so.check_compat(self.cfg, snap, so.read_sat_version())
                if problems:
                    self.readonly_reason = "; ".join(problems)
                    log.error("Beschikbare SAT-velden per stap: %s",
                              {k: sorted(n for n, st in self.loc.items() if st == k) for k in set(self.loc.values())})
                    log.error("Huidige waarden: %s", {k: v for k, v in sorted(snap.items())})
                await self.sync_snapshot(snap)
            except Exception as e:
                self.readonly_reason = f"SAT-opties niet te lezen: {e}"
        if self.readonly_reason:
            log.error("ALLEEN-LEZEN-MODUS: %s", self.readonly_reason)
            self.status.append(f"Alleen-lezen: {self.readonly_reason}")
            if not self.db.get("readonly_notified") == self.readonly_reason:
                await self.push("SAT Autotune: alleen-lezen", f"Er wordt niets aangepast: {self.readonly_reason}")
                self.db.set("readonly_notified", self.readonly_reason)
        self.db.set("daily_ts", 0)  # direct na (her)start een verse analyse en archivering
        self.set_cons_params(self.db.last_snapshot() or {})
        try:
            cs = await self.ha.get_state(ENT["consumption_est"])
            log.info("Verbruikssensor SAT: staat=%s, eenheid=%s; afgeleid uit modulatie met min/max %s",
                     (cs or {}).get("state"), ((cs or {}).get("attributes") or {}).get("unit_of_measurement"), ha_data.CONS_PARAMS)
        except Exception as e:
            log.debug("Verbruikssensor lezen mislukt: %s", e)
        await self.ensure_killswitch()
        await self.ha.subscribe("mobile_app_notification_action", self.on_action)

    async def ensure_killswitch(self):
        st = await self.ha.get_state(ENT["killswitch"])
        if st is not None:
            return
        try:
            await self.ha.ws_call({"type": "input_boolean/create", "name": "SAT Autotune actief", "icon": "mdi:thermostat-auto"})
            log.info("Schakelaar %s aangemaakt (staat UIT: alleen analyse)", ENT["killswitch"])
            await self.push("SAT Autotune", "Schakelaar 'SAT Autotune actief' is aangemaakt en staat uit. Zet hem aan als je automatisch aanpassen wilt toestaan.")
        except Exception as e:
            log.error("Kon schakelaar niet aanmaken: %s", e)

    async def active(self):
        st = await self.ha.get_state(ENT["killswitch"])
        return bool(st and st.get("state") == "on")

    async def push(self, title, msg, actions=None, tag=None):
        try:
            await self.ha.notify(NOTIFY_SERVICE, title, msg, actions, tag)
        except Exception as e:
            log.error("Melding versturen mislukt: %s", e)

    # ------------------------------------------------------------ opties
    def set_cons_params(self, snap):
        lo, hi = so.num(snap.get("minimum_consumption")), so.num(snap.get("maximum_consumption"))
        if isinstance(lo, float) and isinstance(hi, float) and hi > lo:
            ha_data.CONS_PARAMS = (lo, hi)

    def current_opts(self, snap):
        out = {}
        for key, cfgname in TUNABLE.items():
            if not self.cfg[cfgname]:
                continue  # uitgeschakeld
            v = so.num(snap.get(self.cfg[cfgname]))
            if isinstance(v, float):
                out[key] = v
        return out

    async def sync_snapshot(self, snap=None):
        """Vergelijk SAT-opties met de vorige stand en log wijzigingen (ook die van jezelf)."""
        if snap is None:
            snap, self.loc, _ = await so.read_all(self.api, self.entry_id)
        prev = self.db.last_snapshot()
        if prev is not None:
            for field, (o, n) in so.diff(prev, snap).items():
                if self.db.get("own_write") == field:
                    continue
                log.info("SAT-optie extern gewijzigd: %s %s -> %s", field, o, n)
                key = next((k for k, c in TUNABLE.items() if self.cfg[c] == field), None)
                self.db.add_change(ts=time.time(), key=key or field, field=field, old=o, new=n,
                                   source="extern", reason="Door jou of een andere integratie gewijzigd",
                                   status="extern", eval_ts=None, baseline=None, result=None)
                ac = self.db.active_change()
                if ac and ac["field"] == field:
                    self.db.update_change(ac["id"], status="overruled", closed_ts=time.time())
                    await self.push("SAT Autotune", f"Je hebt {LABELS.get(ac['key'], field)} zelf gewijzigd; lopend experiment is gestopt.")
                if key:
                    self.db.set("last_change_ts", time.time())
        await self.watch_opv()
        if prev != snap:
            self.db.add_snapshot(snap)
        self.db.set("own_write", None)
        return snap

    async def watch_opv(self):
        """OPV (minimum_setpoint) staat niet in de opties maar wel als attribuut van de SAT-thermostaat.
        Alleen meekijken: wijzigingen (ook handmatig via Herconfigureren) komen in het logboek."""
        try:
            st = await self.ha.get_state(ENT["climate"])
            v = so.num((st or {}).get("attributes", {}).get("minimum_setpoint"))
        except Exception as e:
            log.debug("OPV lezen mislukt: %s", e)
            return
        if not isinstance(v, float):
            return
        prev = self.db.get("opv_seen")
        if prev is not None and abs(prev - v) > 1e-6:
            log.info("OPV (minimum_setpoint) gewijzigd: %s -> %s", prev, v)
            self.db.add_change(ts=time.time(), key="opv", field="minimum_setpoint", old=prev, new=v, source="extern",
                               reason="OPV gewijzigd (herconfiguratie of herkalibratie); alleen gelogd",
                               status="extern", eval_ts=None, baseline=None, result=None)
        self.db.set("opv_seen", v)

    async def apply(self, key, new, reason, source="auto"):
        field = self.cfg[TUNABLE[key]]
        snap = await self.sync_snapshot()
        old = so.num(snap.get(field))
        self.db.set("own_write", field)
        await so.set_field(self.api, self.entry_id, field, new)
        snap2 = await self.sync_snapshot(None)
        return old, so.num(snap2.get(field))

    # ------------------------------------------------------------ analyse
    async def analysis(self, start, end):
        today = datetime.now(self.tz).date()
        base = await ha_data.estimate_base_load(self.ha, self.tz, self.cfg, today, self.db, self.dhw)
        cal_start = min(start, end - timedelta(days=27))  # gasverdeling kalibreren op max. 4 weken
        data = await ha_data.fetch(self.ha, self.tz, cal_start, end, self.dhw)
        an = m.analyze(data, self.cfg, start, end, self.tz, base, calib_start=cal_start)
        self.archive_days(an.pop("all_days", []))
        return m.merge_archive(an, self.db.days(), self.cfg), data

    def archive_days(self, rows):
        """Bewaar volledige dagen in de eigen database, zodat ze het verlopen van de HA-geschiedenis overleven."""
        today = datetime.now(self.tz).date().isoformat()
        n = 0
        for r in rows:
            if r["date"] < today and m.usable(r):
                self.db.upsert_day(r["date"], r)
                n += 1
        self.db.commit()
        if n:
            log.debug("%d dag(en) in het archief bijgewerkt", n)

    async def window(self, days):
        end = datetime.now(self.tz).date() - timedelta(days=1)
        return await self.analysis(end - timedelta(days=days - 1), end)

    async def build_report(self, kind="weekly"):
        an, _ = await self.window(7)
        prev = next((r for r in self.db.reports("weekly", 1)), None)
        cmp_pct = m.compare(an["agg"], prev["data"]["an"]["agg"]) if prev else None
        phase = tuner.phase(time.time(), self.db.get("install_ts"), self.cfg)
        proposal = await self.make_proposal()
        pdesc = tuner.describe(proposal) if proposal else None
        advice = None
        if self.cfg["anthropic_api_key"]:
            nums = {"agg": {k: v for k, v in an["agg"].items()}, "vergelijking_pct": cmp_pct,
                    "voorstel": pdesc, "fase": phase, "waarschuwingen": an["warnings"]}
            advice = await advisor.explain(self.cfg["anthropic_api_key"], self.cfg["anthropic_model"], nums)
        text = report.text_report(an, cmp_pct, pdesc, phase, advice)
        rec = {"an": an, "text": text, "cmp_pct": cmp_pct, "proposal": pdesc, "phase": phase}
        if kind in ("weekly", "daily"):
            self.db.add_report(kind, rec)
            if kind == "daily":
                self.db.prune("daily", 10)
            await self.publish_sensors(an, cmp_pct)
            if kind == "weekly":
                await self.push("SAT Autotune weekrapport", report.push_summary(an, cmp_pct, pdesc, phase))
        return rec

    async def make_proposal(self):
        if not self.entry_id or self.readonly_reason:
            return None
        an, _ = await self.window(self.cfg["wait_days"])
        snap = self.db.last_snapshot() or {}
        rejected = {}
        for c in self.db.changes(100, "reverted"):
            rejected[(c["key"], "down" if c["new"] < c["old"] else "up")] = c["closed_ts"] or c["ts"]
        return tuner.propose(an["agg"], self.current_opts(snap), self.cfg, rejected, time.time()) if an["agg"].get("valid_days") else None

    async def publish_sensors(self, an, cmp_pct):
        a = an["agg"]
        items = {
            "gas_per_graaddag": (a.get("gas_per_gd"), "m³/graaddag"),
            "starts_per_dag": (a.get("starts_per_day"), "starts/dag"),
            "gem_brandduur": (a.get("mean_burn_min"), "min"),
            "aanvoer_gem": (a.get("flow_mean"), "°C"),
            "pct_onder_55": (a.get("pct_flow_below55"), "%"),
            "gas_totaal_week": (a.get("gas_total_m3"), "m³"),
            "vergelijkbare_dagen": (a.get("valid_days"), "dagen"),
            "verschil_vorige_periode": (cmp_pct, "%"),
        }
        ch = self.db.changes(1)
        items["laatste_wijziging"] = (f"{ch[0]['key']}: {ch[0]['old']} → {ch[0]['new']}" if ch else "geen", None)
        for name, (val, unit) in items.items():
            attrs = {"friendly_name": "SAT analyse " + name.replace("_", " ")}
            if unit:
                attrs["unit_of_measurement"] = unit
                attrs["state_class"] = "measurement"
            if isinstance(val, float):
                val = round(val, 3)
            try:
                await self.ha.rest("POST", f"states/{SENSOR_PREFIX}{name}", {"state": "unknown" if val is None else val, "attributes": attrs})
            except Exception as e:
                log.warning("Sensor %s zetten mislukt: %s", name, e)

    # ------------------------------------------------------------ wijzigen / terugdraaien
    async def maybe_change(self):
        if self.readonly_reason or not self.entry_id:
            return
        now = datetime.now(self.tz)
        if self.db.active_change() or not await self.active():
            return
        if tuner.phase(time.time(), self.db.get("install_ts"), self.cfg) == "dry_run":
            return
        if not tuner.cooldown_over(time.time(), self.db.get("last_change_ts"), self.cfg):
            return
        if tuner.in_quiet_hours(now, self.cfg):
            return
        if self.db.get("proposal_day") == now.date().isoformat() or now.hour < 10:
            return
        self.db.set("proposal_day", now.date().isoformat())
        proposal = await self.make_proposal()
        if not proposal:
            log.info("Geen wijziging voorgesteld (bij twijfel niets doen)")
            return
        if not tuner.within_limits(self.cfg, proposal.key, proposal.new):
            log.error("Voorstel valt buiten de grenzen; genegeerd: %s", proposal)
            return
        an, _ = await self.window(self.cfg["wait_days"])
        old, new = await self.apply(proposal.key, proposal.new, proposal.reason)
        eval_ts = time.time() + self.cfg["wait_days"] * 86400
        cid = self.db.add_change(ts=time.time(), key=proposal.key, field=self.cfg[TUNABLE[proposal.key]], old=old, new=new,
                                 source="auto", reason=proposal.reason, status="pending_eval", eval_ts=eval_ts,
                                 baseline=an["agg"], result=None)
        self.db.set("last_change_ts", time.time())
        ev = datetime.fromtimestamp(eval_ts, self.tz).strftime("%d-%m")
        await self.push("SAT Autotune: wijziging",
                        f"{tuner.describe(proposal)}\nWaarom: {proposal.reason}\nEvaluatie op {ev}.",
                        actions=[{"action": REVERT_ACTION, "title": "Terugdraaien"}], tag=f"sat_autotune_{cid}")

    async def revert(self, ch, why, status="reverted"):
        if status == "reverted" and not await self.active():
            # Noodstop: de schakelaar staat uit, dus niets aanpassen, alleen melden
            await self.push("SAT Autotune", f"Terugdraaien van {LABELS.get(ch['key'])} gewenst ({why}), maar automatisch aanpassen staat uit.")
            return
        try:
            await self.apply(ch["key"], ch["old"], why)
        except Exception as e:
            log.error("Terugdraaien mislukt: %s", e)
            await self.push("SAT Autotune: FOUT", f"Terugdraaien van {LABELS.get(ch['key'])} mislukt: {e}. Controleer SAT handmatig.")
            return
        self.db.update_change(ch["id"], status=status, result={"reason": why}, closed_ts=time.time())
        self.db.set("last_change_ts", time.time())
        await self.push("SAT Autotune: teruggedraaid", f"{LABELS.get(ch['key'])} weer {ch['old']:g}.\nReden: {why}")

    async def on_action(self, event):
        if (event.get("data") or {}).get("action") != REVERT_ACTION:
            return
        ch = self.db.active_change()
        if ch:
            await self.revert(ch, "Handmatig teruggedraaid via de melding", status="reverted_manual")

    async def watch_active_change(self):
        ch = self.db.active_change()
        if not ch:
            return
        now = time.time()
        # Comfortbewaking (direct terugdraaien)
        data = await ha_data.fetch(self.ha, self.tz, (datetime.now(self.tz) - timedelta(hours=6)).date(), datetime.now(self.tz).date(), self.dhw)
        a, b = now - 6 * 3600, now
        lim, mx = self.cfg["comfort_limit_c"], self.cfg["comfort_max_hours"] * 3600
        for room in self.cfg["comfort_monitor_rooms"]:
            rs = data["rooms"].get(room)
            if rs and data["setpoint"]:
                st = max(a, ch["ts"])
                if m.longest_cold_streak(rs, data["setpoint"], data["person"] or None, st, b, lim) >= mx:
                    await self.revert(ch, f"{room} meer dan {lim:g} °C te koud gedurende {self.cfg['comfort_max_hours']:g} uur terwijl je thuis was")
                    return
        # Pendelen na OPV-wijziging
        if ch["key"] == "opv" and now - ch["ts"] > 86400:
            an, _ = await self.analysis(datetime.fromtimestamp(ch["ts"], self.tz).date() + timedelta(days=1), datetime.now(self.tz).date() - timedelta(days=1))
            if tuner.pendel_check(an["agg"], ch, self.cfg):
                await self.revert(ch, f"Pendelen: gemiddelde brandduur {an['agg']['mean_burn_min']:.1f} min (< 5 min)")
                return
        # Evaluatie na de wachttijd
        if now >= ch["eval_ts"]:
            start = datetime.fromtimestamp(ch["ts"], self.tz).date() + timedelta(days=1)
            an, _ = await self.analysis(start, datetime.now(self.tz).date() - timedelta(days=1))
            verdict, why = tuner.evaluate(ch["baseline"], an["agg"], ch, self.cfg)
            if verdict == "wait":
                if now < ch["eval_ts"] + 7 * 86400:
                    return
                verdict, why = "revert", why + "; na uitstel nog steeds onvoldoende data"
            if verdict == "keep":
                self.db.update_change(ch["id"], status="kept", result={"reason": why, "agg": an["agg"]}, closed_ts=now)
                self.db.set("last_change_ts", now)
                await self.push("SAT Autotune: wijziging behouden", f"{LABELS.get(ch['key'])} blijft {ch['new']:g}. {why}{self.saving_text(ch['baseline'], an['agg'])}")
            else:
                await self.revert(ch, why)

    # ------------------------------------------------------------ planning
    def saving_text(self, base_agg, after_agg):
        """Indicatie in euro's per jaar bij een gemiddeld stookseizoen (~2500 graaddagen)."""
        price = self.cfg.get("gas_price_eur_per_m3") or 0
        b, a = (base_agg or {}).get("gas_per_gd"), (after_agg or {}).get("gas_per_gd")
        if not price or not b or a is None or a >= b:
            return ""
        m3 = (b - a) * 2500
        return f" Ongeveer {m3:.0f} m³ (€ {m3 * price:.0f}) per jaar bij een gemiddeld stookseizoen."

    def report_due(self, now):
        h, mi = map(int, self.cfg["report_time"].split(":"))
        if WEEKDAYS[now.weekday()] != self.cfg["report_weekday"]:
            return False
        if (now.hour, now.minute) < (h, mi):
            return False
        return self.db.get("report_day") != now.date().isoformat()

    async def refresh_report(self):
        """Ververs de analyse voor het dashboard (zonder melding)."""
        self.db.set("daily_ts", time.time())
        try:
            await self.build_report("daily")
        except HAError:
            raise
        except Exception:
            log.exception("Analyse verversen mislukt")

    async def tick(self):
        now = datetime.now(self.tz)
        if time.time() - (self.db.get("daily_ts") or 0) > 6 * 3600:
            await self.refresh_report()
        if self.report_due(now):
            self.db.set("report_day", now.date().isoformat())
            await self.build_report("weekly")
        if self.entry_id and not self.readonly_reason:
            if self.db.get("snap_day") != now.date().isoformat():
                self.db.set("snap_day", now.date().isoformat())
                await self.sync_snapshot()
            await self.watch_active_change()
            await self.maybe_change()

    async def loop(self):
        while True:
            try:
                await self.tick()
            except HAError:
                raise
            except Exception:
                log.exception("tick mislukt")
            await asyncio.sleep(TICK)

    # ------------------------------------------------------------ testmodi
    async def run_test_7d(self):
        rec = await self.build_report("test")
        self.db.add_report("test", rec)
        print("\n" + "=" * 60 + "\n" + rec["text"] + "\n" + "=" * 60, flush=True)

    async def run_selftest(self):
        """Wijzig één onschuldige instelling, zet hem terug en toon dat de rest ongewijzigd is."""
        if not self.entry_id:
            raise SystemExit("Geen SAT config-entry")
        field = self.cfg["field_selftest"]
        before, loc, forms = await so.read_all(self.api, self.entry_id)
        if field not in before:
            raise SystemExit(f"Testveld '{field}' niet gevonden. Beschikbare velden: {sorted(before)}")
        cur, opts = before[field], forms[loc[field]][field]["options"]
        if opts:
            alt = next(o for o in opts if str(o) != str(cur))
        else:
            alt = cur + 0.5 if cur < 1 else cur - 0.5
        print(f"Selftest: {field} {cur!r} → {alt!r}", flush=True)
        self.db.set("own_write", field)
        _, mid = await so.set_field(self.api, self.entry_id, field, alt)
        print("Na wijzigen, verschil t.o.v. vooraf:", so.diff(before, mid), flush=True)
        _, after = await so.set_field(self.api, self.entry_id, field, cur)
        d = so.diff(before, after)
        print("Na terugzetten, verschil t.o.v. vooraf:", d or "GEEN (alle opties identiek)", flush=True)
        self.db.set("own_write", None)
        await self.sync_snapshot()
        print("Alle overige SAT-opties:", {k: v for k, v in sorted(after.items()) if k != field}, flush=True)
        print("RESULTAAT:", "OK" if not d else "AFWIJKING", flush=True)


async def amain():
    cfg = config.load()
    logging.basicConfig(level=cfg["log_level"].upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs("/data", exist_ok=True)
    db = DB("/data/sat_autotune.db")
    ha = HA()
    await ha.connect()
    eng = Engine(cfg, ha, db)
    await eng.start()
    from .web import start_web
    await start_web(eng)
    if cfg["run_mode"] == "test_7d":
        await eng.run_test_7d()
    elif cfg["run_mode"] == "selftest_options":
        await eng.run_selftest()
    log.info("SAT Autotune draait (fase: %s, actief: %s)", tuner.phase(time.time(), db.get("install_ts"), cfg), await eng.active())
    await eng.loop()


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(64)


if __name__ == "__main__":
    main()
