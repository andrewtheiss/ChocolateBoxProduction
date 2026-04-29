import logging
import threading
import time

from src.comms import get_status, run_motor, run_motor_group, stop_station


def duration_to_steps(duration_ms, speed_us):
    if speed_us <= 0:
        speed_us = 62
    steps = int((max(duration_ms, 1) * 1000) / (2 * speed_us))
    return max(1, steps)


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
