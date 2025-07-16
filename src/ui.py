import threading
from rich.live import Live
from rich.table import Table
from rich.console import Console
from src.comms import get_status

def start_ui(coordinator, stations):
    def ui_thread():
        console = Console()
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                table = Table(title="Production Line Status (Async Pipeline)")
                table.add_column("Station")
                table.add_column("State")
                table.add_column("Ready for Next?")
                
                for name, station in stations.items():
                    status = get_status(coordinator.serials[name])
                    station.state = status  # Sync state
                    ready = "Yes" if status == 'READY' else "No"
                    table.add_row(name, status, ready)
                
                live.update(table)
                time.sleep(1)
    threading.Thread(target=ui_thread, daemon=True).start()