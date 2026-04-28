from copy import deepcopy
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "config" / "web_state.json"


def _default_station_state():
    return {
        "port_hint": None,
        "firmware": None,
        "motors": [],
        "limits": [],
        "triggers": [],
    }


def _default_state(station_names):
    return {
        "version": 1,
        "stations": {name: _default_station_state() for name in station_names},
        "routines": [],
        "last_active_routine": None,
    }


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

    data.setdefault("version", default["version"])
    data.setdefault("stations", {})
    data.setdefault("routines", [])
    data.setdefault("last_active_routine", None)

    for name in station_names:
        station = data["stations"].setdefault(name, _default_station_state())
        station.setdefault("port_hint", None)
        station.setdefault("firmware", None)
        station.setdefault("motors", [])
        station.setdefault("limits", [])
        station.setdefault("triggers", [])

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
