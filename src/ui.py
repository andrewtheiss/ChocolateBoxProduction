import threading
import time
import logging
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Console

def start_ui(coordinator, stations):
    # Remove console log handler so logs don't stomp on the dashboard
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)

    def ui_thread():
        console = Console()
        with Live(console=console, refresh_per_second=2) as live:
            while True:
                table = Table(title="Production Line Status")
                table.add_column("Station")
                table.add_column("State")
                table.add_column("Connection")
                table.add_column("Ready?")

                for name, station in stations.items():
                    ser = coordinator.serials.get(name)
                    fsm_state = station.state

                    if ser is not None:
                        conn_col = "[green]connected[/green]"
                    else:
                        conn_col = "[dim]not connected[/dim]"

                    state_color = {
                        'READY': 'green',
                        'PROCESSING': 'cyan',
                        'IDLE': 'yellow',
                        'ERROR': 'red',
                    }.get(fsm_state, 'white')

                    ready = "[green]Yes[/green]" if fsm_state == 'READY' else "[red]No[/red]"
                    table.add_row(name, f"[{state_color}]{fsm_state}[/{state_color}]", conn_col, ready)

                coord_state = coordinator.state
                coord_color = 'green' if coord_state == 'RUNNING' else 'red'
                panel = Panel(table, subtitle=f"Coordinator: [{coord_color}]{coord_state}[/{coord_color}]")
                live.update(panel)
                time.sleep(0.5)

    threading.Thread(target=ui_thread, daemon=True).start()
