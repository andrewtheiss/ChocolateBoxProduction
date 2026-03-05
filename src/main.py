import asyncio
import logging
import logging.config
import yaml
from rich.console import Console
from rich.table import Table
from src.state_machine import PipelineCoordinator
from src.comms import scan_ports, connect_serials
from src.ui import start_ui
from src.stations.base import load_stations

with open('config/stations.yaml') as f:
    stations_config = yaml.safe_load(f)
with open('config/states.yaml') as f:
    fsm_config = yaml.safe_load(f)

logging.config.fileConfig('config/logging.ini')
console = Console()

def wait_for_devices(config):
    """Scan, show status, and let the user connect devices before proceeding."""
    while True:
        available_ports = scan_ports()
        serials = connect_serials(config)

        connected = {n: s for n, s in serials.items() if s is not None}
        missing = {n: config[n]['port'] for n, s in serials.items() if s is None}

        console.clear()
        console.print("\n[bold]Chocolate Box Production Line[/bold]\n")

        if available_ports:
            port_table = Table(title="Serial Ports Detected on System")
            port_table.add_column("Port")
            for port in available_ports:
                port_table.add_row(port)
            console.print(port_table)
            console.print()

        if connected:
            conn_table = Table(title="[green]Connected Stations[/green]")
            conn_table.add_column("Station")
            conn_table.add_column("Port")
            for name, ser in connected.items():
                conn_table.add_row(name, ser.port)
            console.print(conn_table)
            console.print()

        if missing:
            miss_table = Table(title="[yellow]Not Connected[/yellow]")
            miss_table.add_column("Station")
            miss_table.add_column("Configured Port")
            for name, port in missing.items():
                miss_table.add_row(name, port)
            console.print(miss_table)
            console.print()

        if not connected:
            console.print("[bold red]No devices connected.[/bold red]\n")
            console.print("Connect an Arduino via USB, then press [bold]Enter[/bold] to rescan.")
            console.print("Or press [bold]Ctrl+C[/bold] to exit.\n")
            try:
                input()
            except KeyboardInterrupt:
                console.print("\nExiting.")
                raise SystemExit(0)
            continue

        console.print(f"[green]{len(connected)}/{len(serials)} stations online.[/green]")
        resp = console.input("\nPress [bold]Enter[/bold] to start, [bold]r[/bold] to rescan, [bold]q[/bold] to quit: ").strip().lower()
        if resp == 'q':
            for s in connected.values():
                s.close()
            raise SystemExit(0)
        if resp == 'r':
            for s in connected.values():
                s.close()
            continue

        return serials

async def main():
    serials = wait_for_devices(stations_config)
    stations = load_stations(stations_config, fsm_config)
    coordinator = PipelineCoordinator(serials, stations, fsm_config)

    connected = [n for n, s in serials.items() if s is not None]
    logging.info(f"Starting with stations: {', '.join(connected)}")

    start_ui(coordinator, stations)
    logging.info("Starting production line coordinator...")
    await coordinator.run()

asyncio.run(main())
