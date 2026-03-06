import threading
import logging
from src.comms import start_station, stop_station


class StationWorker:
    """A worker thread for a single station. Waits for trigger, runs, signals next."""

    def __init__(self, name, ser, next_worker=None):
        self.name = name
        self.ser = ser
        self.next_worker = next_worker
        self.trigger_event = threading.Event()
        self.state = 'IDLE'
        self.last_result = None
        self.items_processed = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    def start(self):
        self._thread.start()

    def trigger(self):
        """Signal this station to process an item."""
        self.trigger_event.set()

    def _run_loop(self):
        while not self._stop:
            self.trigger_event.wait()
            self.trigger_event.clear()

            if self._stop:
                break

            self.state = 'PROCESSING'
            logging.info(f"{self.name}: processing...")

            resp = start_station(self.ser)
            status = resp.get('status', 'no_response') if resp else 'no_response'
            self.last_result = resp

            if status == 'done':
                self.items_processed += 1
                self.state = 'IDLE'
                logging.info(f"{self.name}: done (item #{self.items_processed})")

                if self.next_worker:
                    logging.info(f"{self.name}: triggering {self.next_worker.name}")
                    self.next_worker.trigger()
            else:
                self.state = 'ERROR'
                logging.error(f"{self.name}: failed with '{status}'")

    def shutdown(self):
        self._stop = True
        self.trigger_event.set()


class PipelineCoordinator:
    def __init__(self, serials, stations, fsm_config):
        self.serials = serials
        self.stations = stations
        self.state = 'IDLE'

        all_order = ['dispenser', 'roller', 'taper']
        self.station_order = [s for s in all_order if serials.get(s) is not None]

        # Build worker chain in reverse so each knows its next
        self.workers = {}
        prev_worker = None
        for name in reversed(self.station_order):
            worker = StationWorker(name, serials[name], next_worker=prev_worker)
            self.workers[name] = worker
            prev_worker = worker

        # Start all worker threads
        for worker in self.workers.values():
            worker.start()

        logging.info(f"Pipeline: {' → '.join(self.station_order) if self.station_order else '(none)'}")

    def run_pipeline(self):
        """Trigger the first station — the chain handles the rest."""
        if not self.station_order:
            return "[red]No stations connected — nothing to run.[/red]"

        first = self.station_order[0]
        first_worker = self.workers[first]

        if first_worker.state == 'PROCESSING':
            return f"[yellow]{first} is still processing. Wait or stop it first.[/yellow]"

        self.state = 'RUNNING'
        first_worker.trigger()
        return f"[green]Pipeline triggered → {first}. Chain will auto-advance.[/green]"

    def run_single(self, name):
        """Trigger a single station without chaining."""
        if name not in self.workers:
            return f"[red]{name} has no worker (not connected).[/red]"
        worker = self.workers[name]
        if worker.state == 'PROCESSING':
            return f"[yellow]{name} is still processing.[/yellow]"
        worker.trigger()
        return f"[green]Triggered {name}.[/green]"

    def get_worker_states(self):
        """Return dict of station name -> worker state info."""
        states = {}
        for name in self.station_order:
            w = self.workers[name]
            states[name] = {
                'state': w.state,
                'items': w.items_processed,
                'last_result': w.last_result,
            }
        return states

    def reset(self):
        """Reset all workers to IDLE."""
        for worker in self.workers.values():
            if worker.state == 'ERROR':
                worker.state = 'IDLE'
                worker.last_result = None
        self.state = 'IDLE'
        logging.info("Coordinator and workers reset to IDLE")
        return "[green]All stations reset to IDLE.[/green]"
