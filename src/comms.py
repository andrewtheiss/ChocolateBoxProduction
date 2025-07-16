import serial
import json import loads, dumps
import time

def init_serials(config):
    return {name: serial.Serial(cfg['port'], cfg['baud'], timeout=1)
            for name, cfg in config.items()}

def send_command(serial, cmd):
    try:
        serial.write(dumps({"cmd": cmd}).encode() + b'\n')
        resp = loads(serial.readline().decode().strip())
        return resp.get('status', 'error')
    except Exception as e:
        logging.warning(f"Comms error: {e}")
        return 'error'  # Add retry: for _ in range(3): ... 

def get_status(serial):
    return send_command(serial, 'get_status')