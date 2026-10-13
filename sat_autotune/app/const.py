"""Entiteiten en constanten. Alle namen zijn op 13 oktober 2026 in HA gecontroleerd."""
from __future__ import annotations

ENT = {
    "climate": "climate.living_room_smart_autotune_thermostat_otgw",
    "flame": "binary_sensor.opentherm_gateway_otgw_flame_2",
    "flow": "sensor.opentherm_gateway_otgw_boiler_flow_water_temperature",
    "modulation": "sensor.opentherm_gateway_otgw_relative_modulation_level",
    "control_setpoint": "sensor.opentherm_gateway_otgw_control_setpoint_3",
    "starts": "sensor.opentherm_gateway_otgw_burner_starts",
    "hours": "sensor.opentherm_gateway_otgw_burner_operation_hours",
    "gas": "sensor.gas_meter_gasverbruik",
    "consumption_est": "sensor.woonkamer_smart_autotune_thermostat_current_consumption_otgw_boiler",
    "manual": "input_boolean.verwarming_handmatig",
    "person": "person.justin_kok",
    "killswitch": "input_boolean.sat_autotune_actief",
}
NOTIFY_SERVICE = "mobile_app_iphone_van_justin_kok"  # notify.mobile_app_iphone_van_justin_kok

ROOMS = {
    "slaapkamer": "sensor.slaapkamer_temperature",
    "kantoor": "sensor.kantoor_temperature",
    "hal": "sensor.hal_temperature",
    "woonkamer": "sensor.living_room_living_room_temperatuur",
}

SAT_DOMAIN = "sat"
REVERT_ACTION = "SAT_AUTOTUNE_REVERT"
SENSOR_PREFIX = "sensor.sat_analyse_"

# Logische naam -> optienaam in config.yaml met het SAT-veld
TUNABLE = {
    "coef": "field_coef",
    "max_setpoint": "field_max_setpoint",
    "cycles": "field_cycles",
    "opv": "field_opv",
}
LABELS = {
    "coef": "stooklijncoëfficiënt",
    "max_setpoint": "maximaal setpoint",
    "cycles": "cycli per uur",
    "opv": "overshoot protection value",
    "selftest": "stapgrootte doeltemperatuur (test)",
}
