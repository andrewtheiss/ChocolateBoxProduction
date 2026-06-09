"""Saved-program data model for the visual motor sequencing platform.

Everything in this module operates on plain dicts so programs serialize
directly to ``config/web_state.json`` and bind cleanly to NiceGUI widgets.
The normalize_* helpers accept partial or legacy data and always return a
fully-formed structure with sane defaults, so the UI and execution engine can
trust the shape of what they receive.

Program
  name, version, trigger, global_speed_us, repeat, steps[]

Step (steps run sequentially)
  name, tasks[], completion, on_complete

Task (all tasks in a step start together = the "simultaneous" axis)
  station, motors:[{name, forward}], speed_us|None  (None inherits global)

Completion
  mode: any|all, conditions:[Condition]

Condition (one of)
  {type: duration, ms}
  {type: limit_switch, station, switch}
  {type: encoder, station, motor, counts}
  {type: motors_idle}

Station hardware config (lives alongside the saved station state)
  limit_switches:[{name, pin, normally_open, stops:[motor names]}]
  encoders:[{name, motor, pin_a, pin_b, counts_per_rev}]
"""

# ── Versioning ───────────────────────────────
PROGRAM_VERSION = 2
DEFAULT_SPEED_US = 62
DEFAULT_GLOBAL_SPEED_US = 62

# ── Triggers ─────────────────────────────────
TRIGGER_MANUAL = 'manual'
TRIGGER_TIMED = 'timed'
TRIGGER_ON_EVENT = 'on_event'
TRIGGER_TYPES = (TRIGGER_MANUAL, TRIGGER_TIMED, TRIGGER_ON_EVENT)

# ── Completion ───────────────────────────────
COMPLETION_ANY = 'any'   # whichever condition fires first
COMPLETION_ALL = 'all'   # wait for every condition
COMPLETION_MODES = (COMPLETION_ANY, COMPLETION_ALL)

ON_COMPLETE_STOP = 'stop_tasks'   # stop the step's motors before advancing
ON_COMPLETE_CONTINUE = 'continue'  # leave motors running, advance immediately
ON_COMPLETE_CHOICES = (ON_COMPLETE_STOP, ON_COMPLETE_CONTINUE)

# ── Condition types ──────────────────────────
COND_DURATION = 'duration'
COND_LIMIT_SWITCH = 'limit_switch'
COND_ENCODER = 'encoder'
COND_MOTORS_IDLE = 'motors_idle'
CONDITION_TYPES = (COND_DURATION, COND_LIMIT_SWITCH, COND_ENCODER, COND_MOTORS_IDLE)


# ── Small coercion helpers ───────────────────
def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on', 'fwd', 'forward'}
    return bool(value)


def _as_str(value, default=''):
    if value is None:
        return default
    return str(value)


def _as_speed(value):
    """Return an explicit speed (int) or None to inherit the global speed."""
    if value is None or value == '':
        return None
    speed = _as_int(value, DEFAULT_SPEED_US)
    return speed if speed > 0 else DEFAULT_SPEED_US


# ── Motor reference / task ───────────────────
def normalize_motor_ref(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        'name': _as_str(raw.get('name')),
        'forward': _as_bool(raw.get('forward'), True),
        # None inherits the task speed (which inherits the program global).
        'speed_us': _as_speed(raw.get('speed_us')),
    }


def new_task(station=None):
    return {
        'station': _as_str(station),
        'motors': [],
        'speed_us': None,
    }


def normalize_task(raw):
    raw = raw if isinstance(raw, dict) else {}
    motors = raw.get('motors')
    motors = motors if isinstance(motors, list) else []
    return {
        'station': _as_str(raw.get('station')),
        'motors': [normalize_motor_ref(m) for m in motors if (isinstance(m, dict) and m.get('name'))],
        'speed_us': _as_speed(raw.get('speed_us')),
    }


# ── Conditions ───────────────────────────────
def new_condition(cond_type=COND_DURATION):
    if cond_type == COND_DURATION:
        return {'type': COND_DURATION, 'ms': 1000}
    if cond_type == COND_LIMIT_SWITCH:
        return {'type': COND_LIMIT_SWITCH, 'station': '', 'switch': ''}
    if cond_type == COND_ENCODER:
        return {'type': COND_ENCODER, 'station': '', 'motor': '', 'counts': 1000}
    if cond_type == COND_MOTORS_IDLE:
        return {'type': COND_MOTORS_IDLE}
    return {'type': COND_DURATION, 'ms': 1000}


def normalize_condition(raw):
    raw = raw if isinstance(raw, dict) else {}
    cond_type = raw.get('type')
    if cond_type not in CONDITION_TYPES:
        cond_type = COND_DURATION

    if cond_type == COND_DURATION:
        return {'type': COND_DURATION, 'ms': max(0, _as_int(raw.get('ms'), 1000))}
    if cond_type == COND_LIMIT_SWITCH:
        return {
            'type': COND_LIMIT_SWITCH,
            'station': _as_str(raw.get('station')),
            'switch': _as_str(raw.get('switch')),
        }
    if cond_type == COND_ENCODER:
        return {
            'type': COND_ENCODER,
            'station': _as_str(raw.get('station')),
            'motor': _as_str(raw.get('motor')),
            'counts': max(1, _as_int(raw.get('counts'), 1000)),
        }
    return {'type': COND_MOTORS_IDLE}


def new_completion():
    return {'mode': COMPLETION_ANY, 'conditions': [new_condition(COND_DURATION)]}


def normalize_completion(raw):
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get('mode')
    if mode not in COMPLETION_MODES:
        mode = COMPLETION_ANY
    conditions = raw.get('conditions')
    conditions = conditions if isinstance(conditions, list) else []
    return {
        'mode': mode,
        'conditions': [normalize_condition(c) for c in conditions],
    }


# ── Steps ────────────────────────────────────
def new_step(name='New Step'):
    return {
        'name': name,
        'enabled': True,
        'tasks': [],
        'completion': new_completion(),
        'on_complete': ON_COMPLETE_STOP,
    }


def _migrate_legacy_step(raw):
    """Convert an old routine step (delay/run_motor_for/run_group_for) into a Step."""
    step_type = raw.get('type')
    duration_ms = max(0, _as_int(raw.get('duration_ms'), 0))
    speed_us = _as_speed(raw.get('speed_us'))

    if step_type == 'delay':
        return {
            'name': f'Delay {duration_ms}ms',
            'tasks': [],
            'completion': {'mode': COMPLETION_ANY,
                           'conditions': [{'type': COND_DURATION, 'ms': duration_ms}]},
            'on_complete': ON_COMPLETE_CONTINUE,
        }

    if step_type == 'run_motor_for':
        task = {
            'station': _as_str(raw.get('station')),
            'motors': [{'name': _as_str(raw.get('motor')), 'forward': _as_bool(raw.get('forward'), True)}],
            'speed_us': speed_us,
        }
        return {
            'name': f'Run {raw.get("motor", "motor")}',
            'tasks': [task],
            'completion': {'mode': COMPLETION_ANY,
                           'conditions': [{'type': COND_DURATION, 'ms': duration_ms}]},
            'on_complete': ON_COMPLETE_STOP,
        }

    if step_type == 'run_group_for':
        motors = raw.get('motors')
        motors = motors if isinstance(motors, list) else []
        task = {
            'station': _as_str(raw.get('station')),
            'motors': [normalize_motor_ref(m) for m in motors if isinstance(m, dict)],
            'speed_us': speed_us,
        }
        return {
            'name': f'Group on {raw.get("station", "station")}',
            'tasks': [task],
            'completion': {'mode': COMPLETION_ANY,
                           'conditions': [{'type': COND_DURATION, 'ms': duration_ms}]},
            'on_complete': ON_COMPLETE_STOP,
        }

    return None


def normalize_step(raw):
    raw = raw if isinstance(raw, dict) else {}

    # Legacy steps carry a 'type' key; migrate them on the fly.
    if 'type' in raw and 'tasks' not in raw:
        migrated = _migrate_legacy_step(raw)
        if migrated is not None:
            return migrated

    tasks = raw.get('tasks')
    tasks = tasks if isinstance(tasks, list) else []
    on_complete = raw.get('on_complete')
    if on_complete not in ON_COMPLETE_CHOICES:
        on_complete = ON_COMPLETE_STOP

    return {
        'name': _as_str(raw.get('name'), 'Step') or 'Step',
        'enabled': _as_bool(raw.get('enabled'), True),
        'tasks': [normalize_task(t) for t in tasks],
        'completion': normalize_completion(raw.get('completion')),
        'on_complete': on_complete,
    }


# ── Trigger ──────────────────────────────────
def normalize_trigger(raw):
    raw = raw if isinstance(raw, dict) else {}
    trig_type = raw.get('type')
    if trig_type not in TRIGGER_TYPES:
        trig_type = TRIGGER_MANUAL

    if trig_type == TRIGGER_TIMED:
        return {'type': TRIGGER_TIMED, 'interval_ms': max(0, _as_int(raw.get('interval_ms'), 0))}
    if trig_type == TRIGGER_ON_EVENT:
        return {
            'type': TRIGGER_ON_EVENT,
            'station': _as_str(raw.get('station')),
            'event': _as_str(raw.get('event')),
        }
    return {'type': TRIGGER_MANUAL}


# ── Program ──────────────────────────────────
def new_program(name='New Program'):
    return {
        'name': name,
        'version': PROGRAM_VERSION,
        'trigger': {'type': TRIGGER_MANUAL},
        'global_speed_us': DEFAULT_GLOBAL_SPEED_US,
        'repeat': False,
        'require_homing': False,
        'steps': [],
    }


def normalize_program(raw):
    raw = raw if isinstance(raw, dict) else {}
    steps = raw.get('steps')
    steps = steps if isinstance(steps, list) else []
    return {
        'name': _as_str(raw.get('name'), 'Unnamed Program') or 'Unnamed Program',
        'version': PROGRAM_VERSION,
        'trigger': normalize_trigger(raw.get('trigger')),
        'global_speed_us': max(1, _as_int(raw.get('global_speed_us'), DEFAULT_GLOBAL_SPEED_US)),
        'repeat': _as_bool(raw.get('repeat'), False),
        'require_homing': _as_bool(raw.get('require_homing'), False),
        'steps': [normalize_step(s) for s in steps],
    }


def task_speed(task, program):
    """Resolve a task's effective speed, inheriting the program global if unset."""
    speed = task.get('speed_us')
    if speed is None:
        return max(1, _as_int(program.get('global_speed_us'), DEFAULT_GLOBAL_SPEED_US))
    return max(1, _as_int(speed, DEFAULT_SPEED_US))


def motor_speed(motor_ref, task, program):
    """Resolve a single motor's effective speed.

    Precedence: motor override -> task override -> program global.
    """
    speed = motor_ref.get('speed_us') if isinstance(motor_ref, dict) else None
    if speed is not None:
        return max(1, _as_int(speed, DEFAULT_SPEED_US))
    return task_speed(task, program)


# ── Station hardware config ──────────────────
def new_limit_switch(name='Limit'):
    return {'name': name, 'pin': 2, 'normally_open': True, 'stops': []}


def normalize_limit_switch(raw):
    raw = raw if isinstance(raw, dict) else {}
    stops = raw.get('stops')
    stops = stops if isinstance(stops, list) else []
    return {
        'name': _as_str(raw.get('name')),
        'pin': _as_int(raw.get('pin'), -1),
        'normally_open': _as_bool(raw.get('normally_open'), True),
        'stops': [_as_str(s) for s in stops if _as_str(s)],
    }


def new_encoder(name='Encoder'):
    return {'name': name, 'motor': '', 'pin_a': 2, 'pin_b': 3, 'counts_per_rev': 0}


def normalize_encoder(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        'name': _as_str(raw.get('name')),
        'motor': _as_str(raw.get('motor')),
        'pin_a': _as_int(raw.get('pin_a'), -1),
        'pin_b': _as_int(raw.get('pin_b'), -1),
        'counts_per_rev': max(0, _as_int(raw.get('counts_per_rev'), 0)),
    }


def normalize_limit_switches(raw):
    raw = raw if isinstance(raw, list) else []
    return [normalize_limit_switch(item) for item in raw if (isinstance(item, dict) and item.get('name'))]


def normalize_encoders(raw):
    raw = raw if isinstance(raw, list) else []
    return [normalize_encoder(item) for item in raw if (isinstance(item, dict) and item.get('name'))]


# ── Per-motor home / start-position config ───
HOME_MANUAL = 'manual'      # operator jogs the motor, then presses Set Zero
HOME_LIMIT = 'limit'        # drive toward a limit switch, then zero
HOME_ENCODER = 'encoder'    # drive until an encoder count target, then zero
HOME_METHODS = (HOME_MANUAL, HOME_LIMIT, HOME_ENCODER)

HOME_DIR_FORWARD = 'forward'
HOME_DIR_REVERSE = 'reverse'
DEFAULT_JOG_STEP = 200


def new_home_config():
    return {
        'method': HOME_MANUAL,
        'direction': HOME_DIR_REVERSE,   # direction to travel toward home
        'switch': '',                    # limit-switch name (HOME_LIMIT)
        'encoder': '',                   # encoder name (HOME_ENCODER)
        'target_counts': 0,              # encoder target (HOME_ENCODER)
        'jog_step': DEFAULT_JOG_STEP,    # steps per jog click
        'home_speed_us': None,           # speed while auto-homing (None = global)
    }


def normalize_home_config(raw):
    raw = raw if isinstance(raw, dict) else {}
    method = raw.get('method')
    if method not in HOME_METHODS:
        method = HOME_MANUAL
    direction = raw.get('direction')
    if direction not in (HOME_DIR_FORWARD, HOME_DIR_REVERSE):
        direction = HOME_DIR_REVERSE
    return {
        'method': method,
        'direction': direction,
        'switch': _as_str(raw.get('switch')),
        'encoder': _as_str(raw.get('encoder')),
        'target_counts': max(0, _as_int(raw.get('target_counts'), 0)),
        'jog_step': max(1, _as_int(raw.get('jog_step'), DEFAULT_JOG_STEP)),
        'home_speed_us': _as_speed(raw.get('home_speed_us')),
    }


def normalize_home_map(raw):
    """Normalize a {motor_name: home_config} mapping."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for motor_name, cfg in raw.items():
        if motor_name:
            result[str(motor_name)] = normalize_home_config(cfg)
    return result


# ── Pre-flight verification state ────────────
def _bool_map(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {str(k): _as_bool(v, False) for k, v in raw.items()}


def new_verification():
    return {'motors': {}, 'limit_switches': {}, 'encoders': {}}


def normalize_verification(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        'motors': _bool_map(raw.get('motors')),
        'limit_switches': _bool_map(raw.get('limit_switches')),
        'encoders': _bool_map(raw.get('encoders')),
    }


# ── Validation (used for UI warnings + run gating) ──
def validate_program(program, station_motors=None, station_hw=None):
    """Return a list of human-readable issues. Empty list means runnable.

    station_motors: {station: set/list of motor names}
    station_hw: {station: {'limit_switches': [...], 'encoders': [...]}}
    """
    issues = []
    station_motors = station_motors or {}
    station_hw = station_hw or {}

    def motors_for(station):
        names = station_motors.get(station, [])
        return {m if isinstance(m, str) else m.get('name') for m in names}

    def switches_for(station):
        hw = station_hw.get(station, {})
        return {s.get('name') for s in hw.get('limit_switches', [])}

    def encoders_for(station):
        hw = station_hw.get(station, {})
        return {e.get('name') for e in hw.get('encoders', [])}

    steps = program.get('steps', [])
    if not steps:
        issues.append('Program has no steps.')

    for i, step in enumerate(steps, 1):
        label = f'Step {i} ({step.get("name", "?")})'
        for task in step.get('tasks', []):
            station = task.get('station')
            if not station:
                issues.append(f'{label}: a task has no station.')
                continue
            valid = motors_for(station)
            for ref in task.get('motors', []):
                if valid and ref.get('name') not in valid:
                    issues.append(f'{label}: motor "{ref.get("name")}" not configured on {station}.')
            if not task.get('motors'):
                issues.append(f'{label}: a task on {station} has no motors selected.')

        conditions = step.get('completion', {}).get('conditions', [])
        if not conditions:
            issues.append(f'{label}: no completion condition (step would never end).')
        for cond in conditions:
            ctype = cond.get('type')
            if ctype == COND_LIMIT_SWITCH:
                if cond.get('switch') and cond.get('switch') not in switches_for(cond.get('station')):
                    issues.append(f'{label}: limit switch "{cond.get("switch")}" not configured on {cond.get("station")}.')
            elif ctype == COND_ENCODER:
                if cond.get('motor') and cond.get('motor') not in encoders_for(cond.get('station')):
                    issues.append(f'{label}: encoder "{cond.get("motor")}" not configured on {cond.get("station")}.')

    return issues
