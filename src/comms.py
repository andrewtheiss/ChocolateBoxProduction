import serial
import serial.tools.list_ports
from json import loads, dumps
import logging
import time

IGNORED_PORT_KEYWORDS = ['bluetooth', 'wlan', 'debug']


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
    """Send a JSON command and return the parsed JSON response dict, or None."""
    if ser is None:
        return None
    try:
        ser.reset_input_buffer()
        ser.write(dumps(payload).encode() + b'\n')
        line = ser.readline().decode().strip()
        if not line:
            return None
        return loads(line)
    except Exception as e:
        logging.warning(f"Comms error: {e}")
        return None


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


def run_motor(ser, name, steps=1000, speed_us=500, forward=True):
    return send_command(ser, "run_motor", name=name, steps=steps,
                        speed_us=speed_us, forward=forward)


def stop_motor(ser, name=None):
    if name:
        return send_command(ser, "stop_motor", name=name)
    return send_command(ser, "stop_motor")


def verify_pin(ser, pin, mode="output"):
    return send_command(ser, "verify_pin", pin=pin, mode=mode)


def set_station_id(ser, station_id):
    return send_command(ser, "set_id", id=station_id)
