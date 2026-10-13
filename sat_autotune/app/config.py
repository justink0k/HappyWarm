from __future__ import annotations

import json
import os

DEFAULTS = {
    "run_mode": "normal", "log_level": "info", "expected_sat_version": "4.2.0",
    "degree_day_base": 18.0, "base_gas_m3_per_day": 0.0,
    "report_weekday": "mon", "report_time": "07:00",
    "dry_run_weeks": 4, "wait_days": 14, "min_valid_days": 7,
    "min_degree_days_per_day": 5.0, "max_away_fraction": 0.25, "max_person_away_fraction": 0.75,
    "min_improvement_pct": 2.0, "max_starts_increase_pct": 20.0,
    "quiet_start": "22:00", "quiet_end": "07:00",
    "comfort_monitor_rooms": ["woonkamer", "kantoor"],
    "comfort_limit_c": 1.0, "comfort_max_hours": 2.0,
    "coef_min": 1.0, "coef_max": 3.0, "coef_step": 0.1,
    "max_setpoint_min": 50, "max_setpoint_max": 65, "max_setpoint_step": 5,
    "cycles_min": 3, "cycles_max": 4,
    "opv_min": 42, "opv_max": 52, "opv_step": 1,
    "field_coef": "heating_curve_coefficient",
    "field_max_setpoint": "maximum_setpoint",
    "field_cycles": "cycles_per_hour",
    "field_opv": "",
    "field_selftest": "target_temperature_step",
    "anthropic_api_key": "", "anthropic_model": "claude-sonnet-5-5",
}


def load(path="/data/options.json"):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    # stapgrootte coëfficiënt begrensd op 0,1-0,2
    cfg["coef_step"] = min(0.2, max(0.1, float(cfg["coef_step"])))
    return cfg
