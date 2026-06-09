from copy import deepcopy
import json
from pathlib import Path

from src import program_model


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "config" / "web_state.json"

STATE_VERSION = 2


def _default_station_state():
    return {
        "port_hint": None,
        "firmware": None,
        "motors": [],
        "limits": [],          # legacy placeholder, retained for compatibility
        "triggers": [],        # legacy placeholder, retained for compatibility
        "limit_switches": [],  # [{name, pin, normally_open, stops:[motor names]}]
        "encoders": [],        # [{name, motor, pin_a, pin_b, counts_per_rev}]
        "home_config": {},     # {motor_name: {method, direction, switch, encoder, ...}}
        "verification": {"motors": {}, "limit_switches": {}, "encoders": {}},
    }


def _default_global_settings():
    return {
        "global_speed_us": program_model.DEFAULT_GLOBAL_SPEED_US,
    }


def _default_state(station_names):
    return {
        "version": STATE_VERSION,
        "stations": {name: _default_station_state() for name in station_names},
        "global_settings": _default_global_settings(),
        "routines": [],
        "last_active_routine": None,
    }


def _migrate_station(station):
    """Backfill new hardware-config fields and normalize them in place."""
    station.setdefault("port_hint", None)
    station.setdefault("firmware", None)
    station.setdefault("motors", [])
    station.setdefault("limits", [])
    station.setdefault("triggers", [])
    station.setdefault("limit_switches", [])
    station.setdefault("encoders", [])
    station.setdefault("home_config", {})
    station.setdefault("verification", {})
    station["limit_switches"] = program_model.normalize_limit_switches(station.get("limit_switches"))
    station["encoders"] = program_model.normalize_encoders(station.get("encoders"))
    station["home_config"] = program_model.normalize_home_map(station.get("home_config"))
    station["verification"] = program_model.normalize_verification(station.get("verification"))
    return station


def load_web_state(station_names):
    default = _default_state(station_names)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not STATE_PATH.exists():
        save_web_state(default)
        return default

    try:
        data = json.loads(STATE_PATH.read_text())
    except Exception:
        save_web_state(default)
        return default

    data.setdefault("version", 1)
    data.setdefault("stations", {})
    data.setdefault("global_settings", _default_global_settings())
    data.setdefault("routines", [])
    data.setdefault("last_active_routine", None)

    # Global settings backfill.
    data["global_settings"].setdefault("global_speed_us", program_model.DEFAULT_GLOBAL_SPEED_US)

    # Station backfill + hardware-config normalization.
    for name in station_names:
        station = data["stations"].setdefault(name, _default_station_state())
        _migrate_station(station)
    for name, station in data["stations"].items():
        if isinstance(station, dict):
            _migrate_station(station)

    # Routines/programs: normalize (and migrate legacy step formats) to the
    # current program model so the editor and engine see a consistent shape.
    data["routines"] = [program_model.normalize_program(r) for r in data.get("routines", [])]

    data["version"] = STATE_VERSION
    save_web_state(data)
    return data


def save_web_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def clone_station_motors(motors):
    return deepcopy([
        {
            "name": motor["name"],
            "pul_pin": int(motor["pul_pin"]),
            "dir_pin": int(motor["dir_pin"]),
            "ena_pin": int(motor["ena_pin"]),
            "reversed": bool(motor.get("reversed", False)),
        }
        for motor in motors
    ])
