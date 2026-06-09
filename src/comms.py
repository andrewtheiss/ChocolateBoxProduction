import serial
import serial.tools.list_ports
from json import loads, dumps
import logging
import time
import threading

IGNORED_PORT_KEYWORDS = ['bluetooth', 'wlan', 'debug']
_SERIAL_LOCKS = {}
_SERIAL_LOCKS_GUARD = threading.Lock()

# ── Async event dispatch ─────────────────────
# Firmware may emit unsolicited lines like {"event":"limit","name":...}.
# Handlers registered here are called as handler(ser, event_dict) whenever
# such a line is seen, either while waiting for a command response or when
# pump_events() is called during an idle poll.
_EVENT_HANDLERS = []
_EVENT_HANDLERS_GUARD = threading.Lock()


def register_event_handler(handler):
    with _EVENT_HANDLERS_GUARD:
        if handler not in _EVENT_HANDLERS:
            _EVENT_HANDLERS.append(handler)


def unregister_event_handler(handler):
    with _EVENT_HANDLERS_GUARD:
        if handler in _EVENT_HANDLERS:
            _EVENT_HANDLERS.remove(handler)


def _dispatch_event(ser, event):
    with _EVENT_HANDLERS_GUARD:
        handlers = list(_EVENT_HANDLERS)
    for handler in handlers:
        try:
            handler(ser, event)
        except Exception:
            logging.exception("Event handler error")


def _get_serial_lock(ser):
    key = getattr(ser, 'port', None) or id(ser)
    with _SERIAL_LOCKS_GUARD:
        lock = _SERIAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SERIAL_LOCKS[key] = lock
        return lock


def scan_ports():
    """Return list of available serial ports, filtering out non-Arduino system ports."""
    all_ports = serial.tools.list_ports.comports()
    usable = []
    for p in all_ports:
        if any(kw in p.device.lower() for kw in IGNORED_PORT_KEYWORDS):
            continue
        usable.append(p)
    return usable


def connect_serial(port, baud=9600):
    """Open a serial connection with a brief delay for Arduino reset."""
    conn = serial.Serial(port, baud, timeout=2)
    time.sleep(2)  # Arduino resets on serial open; wait for it to boot
    conn.reset_input_buffer()  # Discard any startup messages
    return conn


def send_json(ser, payload):
    """Send a JSON command and return the parsed JSON response dict, or None.

    Any async event lines ({"event": ...}) seen while waiting for the response
    are dispatched to registered handlers and skipped, so events never corrupt
    the request/response flow.
    """
    if ser is None:
        return None
    try:
        with _get_serial_lock(ser):
            ser.write(dumps(payload).encode() + b'\n')
            ser.flush()
            for _ in range(24):  # bounded so a noisy event stream can't hang us
                line = ser.readline().decode(errors='ignore').strip()
                if not line:
                    return None
                try:
                    msg = loads(line)
                except Exception:
                    continue
                if isinstance(msg, dict) and 'event' in msg:
                    _dispatch_event(ser, msg)
                    continue
                return msg
        return None
    except Exception as e:
        logging.warning(f"Comms error: {e}")
        return None


def pump_events(ser):
    """Drain any pending unsolicited event lines without sending a command.

    Call this from idle poll loops so firmware-emitted events (e.g. a limit
    switch trip that happens between commands) get dispatched promptly.
    """
    if ser is None:
        return
    try:
        with _get_serial_lock(ser):
            while getattr(ser, 'in_waiting', 0):
                line = ser.readline().decode(errors='ignore').strip()
                if not line:
                    break
                try:
                    msg = loads(line)
                except Exception:
                    continue
                if isinstance(msg, dict) and 'event' in msg:
                    _dispatch_event(ser, msg)
                # Stray (non-event) lines while idle are dropped.
    except Exception as e:
        logging.debug(f"pump_events error: {e}")


def send_command(ser, cmd, **params):
    """Send a command with optional params. Returns the response dict."""
    payload = {"cmd": cmd}
    payload.update(params)
    return send_json(ser, payload)


def identify(ser):
    """Ask an Arduino to identify itself. Returns {'id': 'roller', 'version': '1.0'} or None."""
    return send_json(ser, {"cmd": "identify"})


def get_status(ser):
    """Get station status. Returns full response dict."""
    return send_command(ser, "get_status")


def start_station(ser, **params):
    """Send start command with optional params like steps, speed_us."""
    return send_command(ser, "start", **params)


def stop_station(ser):
    """Send emergency stop."""
    return send_command(ser, "stop")


# ── Motor management ────────────────────────

def add_motor(ser, name, pul_pin, dir_pin, ena_pin, reversed=False):
    return send_command(ser, "add_motor", name=name, pul_pin=pul_pin,
                        dir_pin=dir_pin, ena_pin=ena_pin, reversed=reversed)


def remove_motor(ser, name):
    return send_command(ser, "remove_motor", name=name)


def list_motors(ser):
    return send_command(ser, "list_motors")


def run_motor(ser, name, steps=1000, speed_us=62, forward=True):
    return send_command(ser, "run_motor", name=name, steps=steps,
                        speed_us=speed_us, forward=forward)


def run_motor_group(ser, names=None, motors=None, steps=1000, speed_us=62, forward=True):
    payload = {
        "steps": steps,
        "speed_us": speed_us,
        "forward": forward,
    }
    if motors is not None:
        payload["motors"] = motors
    else:
        payload["names"] = names or []
    return send_command(ser, "run_group", **payload)


def stop_motor(ser, name=None):
    if name:
        return send_command(ser, "stop_motor", name=name)
    return send_command(ser, "stop_motor")


def verify_pin(ser, pin, mode="output"):
    return send_command(ser, "verify_pin", pin=pin, mode=mode)


def set_zero(ser, name=None):
    """Mark a motor's current position as 0 (or all motors if name is None)."""
    if name:
        return send_command(ser, "set_zero", name=name)
    return send_command(ser, "set_zero")


def get_position(ser, name):
    """Return {'status':'ok','name':..., 'position': int, 'running': bool}."""
    return send_command(ser, "get_position", name=name)


def set_station_id(ser, station_id):
    return send_command(ser, "set_id", id=station_id)


def save_config(ser):
    """Persist the board's current motors/limits/encoders to its EEPROM so they
    are reconstructed automatically on the next boot/reconnect."""
    return send_command(ser, "save_config")


def clear_config(ser):
    """Erase the board's persisted EEPROM config."""
    return send_command(ser, "clear_config")


# ── Limit switches ──────────────────────────

def add_limit(ser, name, pin, normally_open=True, stops=None):
    """Register a limit switch on the board. When tripped, the firmware stops
    the associated motors immediately and emits an async limit event."""
    return send_command(ser, "add_limit", name=name, pin=pin,
                        normally_open=bool(normally_open), stops=stops or [])


def remove_limit(ser, name):
    return send_command(ser, "remove_limit", name=name)


def list_limits(ser):
    return send_command(ser, "list_limits")


def clear_limit(ser, name=None):
    """Clear a latched limit-trip flag so the switch can fire again."""
    if name:
        return send_command(ser, "clear_limit", name=name)
    return send_command(ser, "clear_limit")


# ── Encoders (firmware support reserved; see generic.ino) ──

def add_encoder(ser, name, pin_a, pin_b=-1, counts_per_rev=0):
    return send_command(ser, "add_encoder", name=name, pin_a=pin_a,
                        pin_b=pin_b, counts_per_rev=counts_per_rev)


def remove_encoder(ser, name):
    return send_command(ser, "remove_encoder", name=name)


def list_encoders(ser):
    return send_command(ser, "list_encoders")


def get_encoder(ser, name):
    return send_command(ser, "get_encoder", name=name)


def reset_encoder(ser, name):
    return send_command(ser, "reset_encoder", name=name)
