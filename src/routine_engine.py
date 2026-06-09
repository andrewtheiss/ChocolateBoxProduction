import logging
import threading
import time

from src import program_model
from src.comms import (
    clear_limit,
    get_encoder,
    get_status,
    pump_events,
    register_event_handler,
    run_motor,
    run_motor_group,
    stop_station,
)


def duration_to_steps(duration_ms, speed_us):
    if speed_us <= 0:
        speed_us = 62
    steps = int((max(duration_ms, 1) * 1000) / (2 * speed_us))
    return max(1, steps)


# Large step target used when a step has no duration condition, so motors keep
# pulsing until a completion condition (e.g. a limit switch) stops them.
CONTINUOUS_STEPS = 2_000_000
# Safety net so a step whose conditions never fire cannot hang forever.
DEFAULT_STEP_TIMEOUT_S = 600
POLL_INTERVAL_S = 0.05


class RoutineRunner:
    def __init__(self, serials_provider, log_callback=None):
        self._serials_provider = serials_provider
        self._log = log_callback or (lambda message: None)
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self.state = 'IDLE'
        self.current_routine = None
        self.current_step = None
        self.last_result = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def run(self, routine):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, 'Routine already running'

            self._stop_event.clear()
            self.state = 'RUNNING'
            self.current_routine = routine.get('name', 'unnamed')
            self.current_step = None
            self.last_result = None
            self._thread = threading.Thread(
                target=self._run_routine,
                args=(routine,),
                daemon=True,
            )
            self._thread.start()
            return True, f"Started routine {self.current_routine}"

    def stop(self):
        self._stop_event.set()
        for ser in self._serials_provider().values():
            if ser is not None:
                try:
                    stop_station(ser)
                except Exception:
                    pass
        self.state = 'STOPPING'
        return True

    def _run_routine(self, routine):
        try:
            steps = routine.get('steps', [])
            for idx, step in enumerate(steps):
                if self._stop_event.is_set():
                    self.last_result = {'status': 'stopped', 'step': idx}
                    self.state = 'STOPPED'
                    return

                self.current_step = idx
                result = self._execute_step(step, idx)
                self.last_result = result
                if result.get('status') != 'done':
                    self.state = 'ERROR'
                    return

            self.state = 'IDLE'
            self.current_step = None
            self.last_result = {'status': 'done'}
        except Exception as exc:
            logging.exception('Routine runner error')
            self.last_result = {'status': 'error', 'error': str(exc)}
            self.state = 'ERROR'
        finally:
            if self.state == 'STOPPING':
                self.state = 'STOPPED'
            self.current_step = None

    def _execute_step(self, step, idx):
        step_type = step.get('type')
        self._log(f'Routine step {idx + 1}: {step_type}')

        if step_type == 'delay':
            return self._execute_delay(step)
        if step_type == 'run_motor_for':
            return self._execute_run_motor_for(step)
        if step_type == 'run_group_for':
            return self._execute_run_group_for(step)
        return {'status': 'error', 'error': f'Unsupported step type: {step_type}'}

    def _execute_delay(self, step):
        duration_ms = int(step.get('duration_ms', 0))
        deadline = time.time() + max(duration_ms, 0) / 1000
        while time.time() < deadline:
            if self._stop_event.is_set():
                return {'status': 'stopped'}
            time.sleep(0.05)
        return {'status': 'done'}

    def _execute_run_motor_for(self, step):
        station = step.get('station')
        motor_name = step.get('motor')
        duration_ms = int(step.get('duration_ms', 0))
        speed_us = int(step.get('speed_us', 62))
        forward = bool(step.get('forward', True))

        ser = self._serials_provider().get(station)
        if ser is None:
            return {'status': 'error', 'error': f'Station {station} not connected'}

        steps = duration_to_steps(duration_ms, speed_us)
        response = run_motor(ser, motor_name, steps=steps, speed_us=speed_us, forward=forward)
        if not response or response.get('status') not in {'started', 'done'}:
            return {'status': 'error', 'error': 'Motor start failed', 'response': response}

        return self._wait_for_station_idle(ser, station, duration_ms)

    def _execute_run_group_for(self, step):
        station = step.get('station')
        motors = step.get('motors', [])
        duration_ms = int(step.get('duration_ms', 0))
        speed_us = int(step.get('speed_us', 62))

        ser = self._serials_provider().get(station)
        if ser is None:
            return {'status': 'error', 'error': f'Station {station} not connected'}
        if not motors:
            return {'status': 'error', 'error': 'No motors defined for group step'}

        steps = duration_to_steps(duration_ms, speed_us)
        response = run_motor_group(ser, motors=motors, steps=steps, speed_us=speed_us)
        if not response or response.get('status') not in {'started', 'done'}:
            return {'status': 'error', 'error': 'Group start failed', 'response': response}

        return self._wait_for_station_idle(ser, station, duration_ms)

    def _wait_for_station_idle(self, ser, station, duration_ms):
        deadline = time.time() + max(duration_ms, 0) / 1000 + 2.0
        while time.time() < deadline:
            if self._stop_event.is_set():
                stop_station(ser)
                return {'status': 'stopped', 'station': station}

            status = get_status(ser)
            if status and status.get('state') != 'PROCESSING':
                return {'status': 'done', 'station': station, 'response': status}
            time.sleep(0.05)

        return {'status': 'error', 'error': f'Timeout waiting for {station}', 'station': station}


class SequenceEngine:
    """Interprets the Program model (see src/program_model.py).

    Steps run sequentially. Within a step, every task starts together (tasks on
    different stations run truly in parallel; tasks sharing a station are merged
    into one motor group since the firmware runs one group at a time). Each step
    ends when its completion conditions are satisfied (any = whichever first,
    all = wait for every one), then on_complete optionally stops the motors.

    Completion conditions:
      - duration: host timer (and sizes the firmware step count)
      - limit_switch: firmware-level trip event (firmware stops motors instantly)
      - motors_idle: involved stations report not PROCESSING
      - encoder: encoder count reaches target (best effort; firmware reserved)
    """

    def __init__(self, serials_provider, hw_provider=None, log_callback=None):
        self._serials_provider = serials_provider
        self._hw_provider = hw_provider or (lambda: {})
        self._log = log_callback or (lambda message: None)
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

        # (station, switch) tuples that have tripped since the current step began.
        self._trips = set()
        self._trips_lock = threading.Lock()
        register_event_handler(self._on_serial_event)

        self.state = 'IDLE'
        self.current_program = None
        self.current_step = None
        self.current_step_name = None
        self.last_result = None

    # ── event capture ────────────────────────
    def _station_for_serial(self, ser):
        for name, candidate in self._serials_provider().items():
            if candidate is ser:
                return name
        return None

    def _on_serial_event(self, ser, event):
        if not isinstance(event, dict) or event.get('event') != 'limit':
            return
        station = self._station_for_serial(ser)
        switch = event.get('name')
        if station and switch:
            with self._trips_lock:
                self._trips.add((station, switch))
            self._log(f'Limit "{switch}" tripped on {station}')

    # ── public control ───────────────────────
    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def run(self, program):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, 'A program is already running'

            program = program_model.normalize_program(program)
            self._stop_event.clear()
            self.state = 'RUNNING'
            self.current_program = program.get('name', 'unnamed')
            self.current_step = None
            self.current_step_name = None
            self.last_result = None
            self._thread = threading.Thread(target=self._run_program, args=(program,), daemon=True)
            self._thread.start()
            return True, f"Started program {self.current_program}"

    def run_step(self, program, step_index):
        """Run a single step on its own (for testing), ignoring its enabled flag."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, 'A program is already running'

            program = program_model.normalize_program(program)
            steps = program.get('steps', [])
            if step_index < 0 or step_index >= len(steps):
                return False, 'Step not found'

            self._stop_event.clear()
            self.state = 'RUNNING'
            self.current_program = f"{program.get('name', 'unnamed')} (step {step_index + 1})"
            self.current_step = step_index
            self.current_step_name = steps[step_index].get('name')
            self.last_result = None
            self._thread = threading.Thread(
                target=self._run_single_step, args=(program, step_index), daemon=True)
            self._thread.start()
            return True, f"Running step {step_index + 1}: {steps[step_index].get('name', '?')}"

    def _run_single_step(self, program, step_index):
        try:
            step = program.get('steps', [])[step_index]
            self._log(f'Test step {step_index + 1}: {step.get("name", "?")}')
            outcome = self._execute_step(step, program)
            self.last_result = outcome
            self.state = ('IDLE' if outcome.get('status') == 'done'
                          else 'STOPPED' if outcome.get('status') == 'stopped' else 'ERROR')
        except Exception as exc:
            logging.exception('Sequence engine step error')
            self.last_result = {'status': 'error', 'error': str(exc)}
            self.state = 'ERROR'
        finally:
            if self.state == 'STOPPING':
                self.state = 'STOPPED'
            self.current_step = None
            self.current_step_name = None

    def stop(self):
        self._stop_event.set()
        for ser in self._serials_provider().values():
            if ser is not None:
                try:
                    stop_station(ser)
                except Exception:
                    pass
        self.state = 'STOPPING'
        return True

    # ── execution ────────────────────────────
    def _run_program(self, program):
        try:
            repeat = bool(program.get('repeat'))
            while True:
                outcome = self._run_once(program)
                if outcome.get('status') != 'done':
                    self.state = 'STOPPED' if outcome.get('status') == 'stopped' else 'ERROR'
                    self.last_result = outcome
                    return
                if not repeat or self._stop_event.is_set():
                    break

            self.state = 'IDLE'
            self.current_step = None
            self.current_step_name = None
            self.last_result = {'status': 'done'}
        except Exception as exc:
            logging.exception('Sequence engine error')
            self.last_result = {'status': 'error', 'error': str(exc)}
            self.state = 'ERROR'
        finally:
            if self.state == 'STOPPING':
                self.state = 'STOPPED'
            self.current_step = None

    def _run_once(self, program):
        steps = program.get('steps', [])
        for idx, step in enumerate(steps):
            if self._stop_event.is_set():
                return {'status': 'stopped', 'step': idx}
            if not step.get('enabled', True):
                self._log(f'Step {idx + 1}: {step.get("name", "?")} (disabled, skipped)')
                continue
            self.current_step = idx
            self.current_step_name = step.get('name')
            self._log(f'Step {idx + 1}: {step.get("name", "?")}')
            result = self._execute_step(step, program)
            self.last_result = result
            if result.get('status') != 'done':
                return result
        return {'status': 'done'}

    def _grouped_tasks(self, step, program):
        """Merge a step's tasks into one motor group per station.

        Each motor carries its own resolved speed (motor override -> task ->
        global) so the firmware can drive them at independent us/step rates.
        """
        groups = {}
        for task in step.get('tasks', []):
            station = task.get('station')
            motors = task.get('motors', [])
            if not station or not motors:
                continue
            group = groups.setdefault(station, {'motors': []})
            for ref in motors:
                group['motors'].append({
                    'name': ref['name'],
                    'forward': bool(ref.get('forward', True)),
                    'speed_us': program_model.motor_speed(ref, task, program),
                })
        return groups

    def _execute_step(self, step, program):
        serials = self._serials_provider()
        completion = step.get('completion', {})
        conditions = completion.get('conditions', [])
        mode = completion.get('mode', program_model.COMPLETION_ANY)
        groups = self._grouped_tasks(step, program)

        # Stations this step touches (for motors_idle + event pumping).
        involved = set(groups.keys())
        for cond in conditions:
            if cond.get('station'):
                involved.add(cond.get('station'))

        # Clear any stale limit trips for switches this step waits on.
        with self._trips_lock:
            for cond in conditions:
                if cond.get('type') == program_model.COND_LIMIT_SWITCH:
                    self._trips.discard((cond.get('station'), cond.get('switch')))
                    ser = serials.get(cond.get('station'))
                    if ser is not None:
                        try:
                            clear_limit(ser, cond.get('switch'))
                        except Exception:
                            pass

        duration_ms = self._duration_condition_ms(conditions)

        # Start each station's motor group. Each motor is sized to the step's
        # duration at its own speed, so motors at different rates all run for
        # the same wall-clock time.
        for station, group in groups.items():
            ser = serials.get(station)
            if ser is None:
                return {'status': 'error', 'error': f'Station {station} not connected'}
            motors_payload = []
            for m in group['motors']:
                speed = m['speed_us']
                steps_target = (duration_to_steps(duration_ms, speed)
                                if duration_ms is not None else CONTINUOUS_STEPS)
                motors_payload.append({
                    'name': m['name'],
                    'forward': m['forward'],
                    'speed_us': speed,
                    'steps': steps_target,
                })
            # Group-level steps/speed sized to the duration so firmware that
            # doesn't read per-motor fields still runs for the full time. Use
            # the slowest motor's speed so the group lasts at least as long.
            group_speed = min(m['speed_us'] for m in group['motors'])
            group_steps = (duration_to_steps(duration_ms, group_speed)
                           if duration_ms is not None else CONTINUOUS_STEPS)
            response = run_motor_group(ser, motors=motors_payload,
                                       steps=group_steps, speed_us=group_speed)
            if not response or response.get('status') not in {'started', 'done'}:
                self._stop_stations(involved)
                return {'status': 'error', 'error': f'Group start failed on {station}', 'response': response}

        wait_result = self._wait_for_completion(step, program, conditions, mode, involved, duration_ms)

        if step.get('on_complete', program_model.ON_COMPLETE_STOP) == program_model.ON_COMPLETE_STOP:
            self._stop_stations(involved)

        return wait_result

    def _wait_for_completion(self, step, program, conditions, mode, involved, duration_ms):
        serials = self._serials_provider()
        start = time.time()
        base = (duration_ms or 0) / 1000.0
        cap = start + base + DEFAULT_STEP_TIMEOUT_S

        # A step with no conditions and no motion is a no-op; treat as done.
        if not conditions:
            return {'status': 'done', 'step': self.current_step}

        while True:
            if self._stop_event.is_set():
                self._stop_stations(involved)
                return {'status': 'stopped', 'step': self.current_step}

            for station in involved:
                pump_events(serials.get(station))

            met = [self._condition_met(cond, start, involved, serials) for cond in conditions]
            satisfied = all(met) if mode == program_model.COMPLETION_ALL else any(met)
            if satisfied:
                return {'status': 'done', 'step': self.current_step}

            if time.time() > cap:
                return {'status': 'error', 'error': f'Step {self.current_step} timed out', 'step': self.current_step}

            time.sleep(POLL_INTERVAL_S)

    def _duration_condition_ms(self, conditions):
        durations = [c.get('ms', 0) for c in conditions if c.get('type') == program_model.COND_DURATION]
        return max(durations) if durations else None

    def _condition_met(self, cond, start, involved, serials):
        ctype = cond.get('type')

        if ctype == program_model.COND_DURATION:
            return (time.time() - start) * 1000.0 >= cond.get('ms', 0)

        if ctype == program_model.COND_LIMIT_SWITCH:
            with self._trips_lock:
                return (cond.get('station'), cond.get('switch')) in self._trips

        if ctype == program_model.COND_MOTORS_IDLE:
            targets = [cond.get('station')] if cond.get('station') else list(involved)
            if not targets:
                return True
            for station in targets:
                ser = serials.get(station)
                if ser is None:
                    continue
                status = get_status(ser)
                if status and status.get('state') == 'PROCESSING':
                    return False
            return True

        if ctype == program_model.COND_ENCODER:
            ser = serials.get(cond.get('station'))
            if ser is None:
                return False
            resp = get_encoder(ser, cond.get('motor'))
            if resp and resp.get('status') == 'ok':
                return resp.get('count', 0) >= cond.get('counts', 0)
            return False

        return False

    def _stop_stations(self, stations):
        serials = self._serials_provider()
        for station in stations:
            ser = serials.get(station)
            if ser is not None:
                try:
                    stop_station(ser)
                except Exception:
                    pass
