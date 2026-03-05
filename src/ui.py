import threading
import time
from rich.live import Live
from rich.table import Table
from rich.console import Console
from src.comms import get_status

def start_ui(coordinator, stations):
    def ui_thread():
        console = Console()
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                table = Table(title="Production Line Status")
                table.add_column("Station")
                table.add_column("FSM State")
                table.add_column("Serial")
                table.add_column("Ready?")

                for name, station in stations.items():
                    ser = coordinator.serials.get(name)
                    if ser is not None:
                        hw_status = get_status(ser)
                        serial_col = f"[green]connected[/green]"
                    else:
                        hw_status = None
                        serial_col = f"[yellow]mock[/yellow]"

                    fsm_state = station.state
                    ready = "[green]Yes[/green]" if fsm_state == 'READY' else "[red]No[/red]"
                    table.add_row(name, fsm_state, serial_col, ready)

                live.update(table)
                time.sleep(1)
    threading.Thread(target=ui_thread, daemon=True).start()
