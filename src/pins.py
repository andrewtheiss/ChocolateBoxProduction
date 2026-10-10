"""Pin name helpers for the Arduino Nano Every.

The board's analog header (A0-A7) can double as digital I/O, which is handy
once the D-side runs out of pins for extra motor controllers. On the Nano
Every the analog pins map onto digital pin numbers like this:

    A0 -> 14   A1 -> 15   A2 -> 16
    A3 -> 17   A4 -> 18   A5 -> 19

A6 (20) and A7 (21) are analog-input only and have no digital buffer, so they
cannot be used for motor/limit/encoder I/O.

The firmware speaks plain integers, so the UI accepts a friendly "A0".."A5"
label and we translate to/from the underlying digital number here. This keeps
the wire format and EEPROM storage unchanged.
"""

# Friendly analog label -> underlying digital pin number (usable for digital I/O).
ANALOG_TO_DIGITAL = {f'A{i}': 14 + i for i in range(6)}  # A0..A5 -> 14..19

# Reverse map for display. Include A6/A7 so any stale config still renders
# sensibly, even though they are not valid for digital use.
_DIGITAL_TO_ANALOG = {14 + i: f'A{i}' for i in range(8)}  # 14..21 -> A0..A7

# Analog pins that exist but cannot be used as digital I/O on the Nano Every.
ANALOG_ONLY = {'A6', 'A7'}

# Options for select widgets / help text: D0-D13 plus A0-A5.
PIN_OPTIONS = [str(i) for i in range(0, 14)] + [f'A{i}' for i in range(6)]


def parse_pin(value):
    """Normalize a pin entry to an int digital pin number.

    Accepts ints, numeric strings, or analog labels like 'A0'..'A5'
    (case-insensitive). Empty/None becomes -1 (meaning "not set"). Raises
    ValueError for A6/A7 (analog-only) or unparseable input.
    """
    if value is None:
        return -1
    if isinstance(value, bool):
        raise ValueError(f'invalid pin: {value!r}')
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s == '':
        return -1
    up = s.upper()
    if up in ANALOG_TO_DIGITAL:
        return ANALOG_TO_DIGITAL[up]
    if up in ANALOG_ONLY:
        raise ValueError(f'{up} is analog-only and cannot be used for digital I/O (use A0-A5)')
    if up.startswith('A') and up[1:].isdigit():
        raise ValueError(f'{up} is not a usable analog pin (use A0-A5)')
    try:
        return int(s)
    except ValueError:
        raise ValueError(f'invalid pin "{s}" (use 0-13 or A0-A5)')


def format_pin(num):
    """Render a digital pin number for display, showing A-labels for 14-21."""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return str(num)
    if n < 0:
        return ''
    return _DIGITAL_TO_ANALOG.get(n, str(n))
