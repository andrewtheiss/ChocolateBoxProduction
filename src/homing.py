"""Startup homing / zeroing controller.

Brings each motor to its known start position so the rest of the platform runs
from a consistent reference. Three per-motor methods (see program_model home
config):

  manual  - operator jogs the motor with buttons, then presses Set Zero
  limit   - drive toward a limit switch until it trips, then zero
  encoder - drive until an encoder reaches a target count, then zero

The firmware tracks a signed step position per motor; "zeroing" sets that
position to 0. Homed motors are remembered so programs can require homing.
"""

import threading
import time

from src import program_model
from src.comms import (
    clear_limit,
    get_encoder,
    get_position,
    pump_events,
    register_event_handler,
    reset_encoder,
    run_motor,
    set_zero,
    stop_motor,
)

HOMING_MAX_STEPS = 2_000_000   # "run continuously" until a condition stops it
HOMING_TIMEOUT_S = 120
DEFAULT_HOME_SPEED_US = 62
POLL_S = 0.05


class HomingController:
    def __init__(self, serials_provider, config_provider, log_callback=None):
        self._serials = serials_provider          # () -> {station: ser}
        self._config = config_provider            # () -> snapshot dict (see web_ui)
        self._log = log_callback or (lambda message: None)

        self._status = {}                          # (station, motor) -> dict
        self._status_lock = threading.Lock()
        self._busy = {}                            # (station, motor) -> Event-ish bool
        self._stop = {}                            # (station, motor) -> bool
        self._threads = {}

        self._trips = set()                        # (station, switch) trips since last clear
        self._trips_lock = threading.Lock()
        register_event_handler(self._on_event)

    # ── event capture ────────────────────────
    def _station_for_serial(self, ser):
        for name, candidate in self._serials().items():
            if candidate is ser:
                return name
        return None

    def _on_event(self, ser, event):
        if not isinstance(event, dict) or event.get('event') != 'limit':
            return
        station = self._station_for_serial(ser)
        switch = event.get('name')
        if station and switch:
            with self._trips_lock:
                self._trips.add((station, switch))

    # ── status helpers ───────────────────────
    def _entry(self, station, motor):
        key = (station, motor)
        with self._status_lock:
            return self._status.setdefault(key, {
                'station': station, 'motor': motor,
                'position': None, 'homed': False, 'busy': False, 'message': '',
            })

    def _update(self, station, motor, **fields):
        with self._status_lock:
            entry = self._status.setdefault((station, motor), {
                'station': station, 'motor': motor,
                'position': None, 'homed': False, 'busy': False, 'message': '',
            })
            entry.update(fields)

    def get_status_snapshot(self):
        with self._status_lock:
            return {f'{s}/{m}': dict(v) for (s, m), v in self._status.items()}

    def is_homed(self, station, motor):
        with self._status_lock:
            entry = self._status.get((station, motor))
            return bool(entry and entry.get('homed'))

    def homed_motors(self):
        with self._status_lock:
            return {(s, m) for (s, m), v in self._status.items() if v.get('homed')}

    # ── config access ────────────────────────
    def _home_cfg(self, station, motor):
        snapshot = self._config()
        st = snapshot.get('stations', {}).get(station, {})
        cfg = st.get('home_config', {}).get(motor)
        return program_model.normalize_home_config(cfg or {})

    def _speed(self, cfg):
        if cfg.get('home_speed_us'):
            return cfg['home_speed_us']
        return self._config().get('global_speed_us', DEFAULT_HOME_SPEED_US)

    # ── position refresh ─────────────────────
    def refresh_position(self, station, motor):
        ser = self._serials().get(station)
        if ser is None:
            return None
        resp = get_position(ser, motor)
        if resp and resp.get('status') == 'ok':
            self._update(station, motor, position=resp.get('position'))
            return resp.get('position')
        return None

    def refresh_all_positions(self):
        snapshot = self._config()
        for station, st in snapshot.get('stations', {}).items():
            if self._serials().get(station) is None:
                continue
            for motor in st.get('motors', []):
                self.refresh_position(station, motor)

    # ── jogging ──────────────────────────────
    def jog(self, station, motor, forward, steps=None):
        ser = self._serials().get(station)
        if ser is None:
            return False, f'{station} not connected'
        cfg = self._home_cfg(station, motor)
        steps = int(steps or cfg.get('jog_step', program_model.DEFAULT_JOG_STEP))
        speed = self._speed(cfg)
        resp = run_motor(ser, motor, steps=steps, speed_us=speed, forward=forward)
        if not resp or resp.get('status') not in {'started', 'done'}:
            return False, f'Jog rejected: {resp.get("error") if resp else "no response"}'
        self._wait_idle(ser, motor, timeout=max(2.0, steps * 2 * speed / 1_000_000 + 1.0))
        self.refresh_position(station, motor)
        return True, 'jogged'

    def jog_start(self, station, motor, forward):
        ser = self._serials().get(station)
        if ser is None:
            return False, f'{station} not connected'
        cfg = self._home_cfg(station, motor)
        speed = self._speed(cfg)
        resp = run_motor(ser, motor, steps=HOMING_MAX_STEPS, speed_us=speed, forward=forward)
        ok = bool(resp and resp.get('status') in {'started', 'done'})
        return ok, ('jogging' if ok else 'jog rejected')

    def jog_stop(self, station, motor):
        ser = self._serials().get(station)
        if ser is None:
            return False, f'{station} not connected'
        stop_motor(ser, motor)
        self.refresh_position(station, motor)
        return True, 'stopped'

    # ── set zero ─────────────────────────────
    def set_zero(self, station, motor):
        ser = self._serials().get(station)
        if ser is None:
            return False, f'{station} not connected'
        resp = set_zero(ser, motor)
        if not resp or resp.get('status') not in {'zeroed', 'zeroed_all', 'ok'}:
            return False, 'Set zero rejected'
        self._update(station, motor, position=0, homed=True, message='zeroed at current position')
        self._log(f'{station}/{motor}: set zero')
        return True, 'zeroed'

    # ── auto homing ──────────────────────────
    def home_motor(self, station, motor):
        key = (station, motor)
        if self._busy.get(key):
            return False, 'already homing'
        thread = threading.Thread(target=self._do_home, args=(station, motor), daemon=True)
        self._threads[key] = thread
        thread.start()
        return True, 'homing started'

    def home_all(self, allowed=None):
        """Auto-home every limit/encoder motor. If allowed is a set of
        (station, motor) tuples, only those are homed (others skipped)."""
        thread = threading.Thread(target=self._do_home_all, args=(allowed,), daemon=True)
        self._threads[('*', '*')] = thread
        thread.start()
        return True, 'homing all'

    def _do_home_all(self, allowed=None):
        snapshot = self._config()
        for station, st in snapshot.get('stations', {}).items():
            if self._serials().get(station) is None:
                continue
            for motor in st.get('motors', []):
                if allowed is not None and (station, motor) not in allowed:
                    continue
                cfg = self._home_cfg(station, motor)
                if cfg.get('method') in (program_model.HOME_LIMIT, program_model.HOME_ENCODER):
                    self._do_home(station, motor)

    def stop(self, station=None, motor=None):
        if station is None:
            for key in list(self._busy.keys()):
                self._stop[key] = True
            for st, ser in self._serials().items():
                if ser is not None:
                    stop_motor(ser)
            return True, 'stopping all'
        self._stop[(station, motor)] = True
        ser = self._serials().get(station)
        if ser is not None:
            stop_motor(ser, motor)
        return True, 'stopping'

    def _do_home(self, station, motor):
        key = (station, motor)
        self._busy[key] = True
        self._stop[key] = False
        self._update(station, motor, busy=True, message='homing...')
        ser = self._serials().get(station)
        try:
            if ser is None:
                self._update(station, motor, busy=False, message=f'{station} not connected')
                return

            cfg = self._home_cfg(station, motor)
            method = cfg.get('method')
            forward = cfg.get('direction') == program_model.HOME_DIR_FORWARD
            speed = self._speed(cfg)

            if method == program_model.HOME_LIMIT:
                self._home_to_limit(station, motor, ser, cfg, forward, speed)
            elif method == program_model.HOME_ENCODER:
                self._home_to_encoder(station, motor, ser, cfg, forward, speed)
            else:
                self._update(station, motor, message='manual: jog then Set Zero')
        finally:
            self._busy[key] = False
            self._stop[key] = False
            with self._status_lock:
                entry = self._status.get(key)
                if entry:
                    entry['busy'] = False

    def _home_to_limit(self, station, motor, ser, cfg, forward, speed):
        switch = cfg.get('switch')
        if not switch:
            self._update(station, motor, busy=False, message='no limit switch configured')
            return
        with self._trips_lock:
            self._trips.discard((station, switch))
        clear_limit(ser, switch)

        resp = run_motor(ser, motor, steps=HOMING_MAX_STEPS, speed_us=speed, forward=forward)
        if not resp or resp.get('status') not in {'started', 'done'}:
            self._update(station, motor, busy=False, message='could not start motor')
            return

        outcome = self._wait(ser, lambda: self._tripped(station, switch))
        stop_motor(ser, motor)

        if outcome == 'ok':
            self.set_zero(station, motor)
            self._update(station, motor, message='homed to limit switch')
            self._log(f'{station}/{motor}: homed to limit "{switch}"')
        elif outcome == 'stopped':
            self._update(station, motor, message='homing stopped')
        else:
            self._update(station, motor, message='homing timed out (switch not hit)')

    def _home_to_encoder(self, station, motor, ser, cfg, forward, speed):
        encoder = cfg.get('encoder')
        target = cfg.get('target_counts', 0)
        if not encoder:
            self._update(station, motor, busy=False, message='no encoder configured')
            return
        reset_encoder(ser, encoder)

        resp = run_motor(ser, motor, steps=HOMING_MAX_STEPS, speed_us=speed, forward=forward)
        if not resp or resp.get('status') not in {'started', 'done'}:
            self._update(station, motor, busy=False, message='could not start motor')
            return

        def reached():
            r = get_encoder(ser, encoder)
            return bool(r and r.get('status') == 'ok' and r.get('count', 0) >= target)

        outcome = self._wait(ser, reached)
        stop_motor(ser, motor)

        if outcome == 'ok':
            self.set_zero(station, motor)
            self._update(station, motor, message=f'homed to encoder {target}')
            self._log(f'{station}/{motor}: homed to encoder "{encoder}" >= {target}')
        elif outcome == 'stopped':
            self._update(station, motor, message='homing stopped')
        else:
            self._update(station, motor, message='homing timed out (encoder target not reached)')

    # ── wait helpers ─────────────────────────
    def _tripped(self, station, switch):
        with self._trips_lock:
            return (station, switch) in self._trips

    def _wait(self, ser, predicate, timeout=HOMING_TIMEOUT_S):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._any_stop_requested(ser):
                return 'stopped'
            pump_events(ser)
            if predicate():
                return 'ok'
            time.sleep(POLL_S)
        return 'timeout'

    def _wait_idle(self, ser, motor, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pump_events(ser)
            resp = get_position(ser, motor)
            if resp and resp.get('status') == 'ok' and not resp.get('running'):
                return
            time.sleep(POLL_S)

    def _any_stop_requested(self, ser):
        station = self._station_for_serial(ser)
        for (st, _mt), flag in self._stop.items():
            if st == station and flag:
                return True
        return False
