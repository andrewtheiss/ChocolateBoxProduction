import serial
import serial.tools.list_ports
from json import loads, dumps
import logging

def scan_ports():
    """Return list of available serial port device paths."""
    return [p.device for p in serial.tools.list_ports.comports()]

def connect_serials(config):
    """Try to open configured serial ports. Returns dict of name -> Serial or None."""
    connections = {}
    for name, cfg in config.items():
        try:
            conn = serial.Serial(cfg['port'], cfg['baud'], timeout=1)
            connections[name] = conn
            logging.info(f"{name}: connected on {cfg['port']}")
        except (serial.SerialException, OSError):
            connections[name] = None
    return connections

def send_command(ser, cmd):
    if ser is None:
        return None
    try:
        ser.write(dumps({"cmd": cmd}).encode() + b'\n')
        line = ser.readline().decode().strip()
        if not line:
            return 'no_response'
        resp = loads(line)
        return resp.get('status', 'error')
    except Exception as e:
        logging.warning(f"Comms error: {e}")
        return 'error'

def get_status(ser):
    return send_command(ser, 'get_status')
