import logging
import logging.config
import yaml
from rich.console import Console
from rich.table import Table
from src.state_machine import PipelineCoordinator
from src.comms import scan_ports, connect_serial
from src.ui import run_dashboard
from src.stations.base import load_stations

with open('config/stations.yaml') as f:
    stations_config = yaml.safe_load(f)
with open('config/states.yaml') as f:
    fsm_config = yaml.safe_load(f)

logging.config.fileConfig('config/logging.ini')
console = Console()


def show_header():
    console.clear()
    console.print("\n[bold]Chocolate Box Production Line[/bold]\n")


def device_setup(config):
    """Interactive device assignment: scan USB ports, let user assign each to a station."""
    station_names = list(config.keys())
    assignments = {}  # station_name -> Serial object

    while True:
        show_header()
        ports = scan_ports()

        # Show detected USB devices
        if ports:
            port_table = Table(title="USB Devices Detected")
            port_table.add_column("#", style="bold")
            port_table.add_column("Port")
            port_table.add_column("Description")
            for i, p in enumerate(ports, 1):
                port_table.add_row(str(i), p.device, p.description or "")
            console.print(port_table)
            console.print()
        else:
            console.print("[bold red]No USB devices detected.[/bold red]\n")

        # Show current assignments
        if assignments:
            assign_table = Table(title="[green]Assigned Stations[/green]")
            assign_table.add_column("Station")
            assign_table.add_column("Port")
            for name, ser in assignments.items():
                assign_table.add_row(name, ser.port)
            console.print(assign_table)
            console.print()

        # Show unassigned stations
        unassigned = [n for n in station_names if n not in assignments]
        if unassigned:
            console.print(f"[yellow]Unassigned stations:[/yellow] {', '.join(unassigned)}\n")

        # If everything is assigned, offer to start
        if not unassigned:
            console.print("[bold green]All stations assigned![/bold green]")
            resp = console.input("\nPress [bold]Enter[/bold] to start, [bold]r[/bold] to reassign, [bold]q[/bold] to quit: ").strip().lower()
            if resp == 'q':
                for s in assignments.values():
                    s.close()
                raise SystemExit(0)
            if resp == 'r':
                for s in assignments.values():
                    s.close()
                assignments.clear()
                continue
            break

        # No ports to assign from
        if not ports:
            console.print("Connect an Arduino via USB, then press [bold]Enter[/bold] to rescan.")
            console.print("Press [bold]Ctrl+C[/bold] to exit.\n")
            try:
                input()
            except KeyboardInterrupt:
                console.print("\nExiting.")
                raise SystemExit(0)
            continue

        # Assign a port to a station
        console.print("[bold]Assign a device to a station:[/bold]")

        # Pick the port
        available_indices = [
            i for i, p in enumerate(ports)
            if p.device not in {s.port for s in assignments.values()}
        ]
        if not available_indices:
            console.print("[yellow]All detected ports are already assigned.[/yellow]")
            console.print("Connect another Arduino and press [bold]Enter[/bold] to rescan, or [bold]s[/bold] to start with current assignments.\n")
            resp = input().strip().lower()
            if resp == 's' and assignments:
                break
            continue

        console.print(f"  Enter port number (1-{len(ports)}), [bold]r[/bold] to rescan, [bold]s[/bold] to start with current: ", end="")
        port_choice = input().strip().lower()

        if port_choice == 'r':
            continue
        if port_choice == 's' and assignments:
            break
        try:
            port_idx = int(port_choice) - 1
            if port_idx not in available_indices:
                console.print("[red]That port is already assigned or invalid.[/red]")
                input("Press Enter to continue...")
                continue
            selected_port = ports[port_idx]
        except (ValueError, IndexError):
            console.print("[red]Invalid choice.[/red]")
            input("Press Enter to continue...")
            continue

        # Pick the station
        console.print(f"  Assign [bold]{selected_port.device}[/bold] to which station?")
        for i, name in enumerate(unassigned, 1):
            console.print(f"    {i}. {name}")
        console.print(f"  Enter station number (1-{len(unassigned)}): ", end="")
        station_choice = input().strip()

        try:
            station_idx = int(station_choice) - 1
            station_name = unassigned[station_idx]
        except (ValueError, IndexError):
            console.print("[red]Invalid choice.[/red]")
            input("Press Enter to continue...")
            continue

        # Connect
        baud = config[station_name].get('baud', 9600)
        try:
            ser = connect_serial(selected_port.device, baud)
            assignments[station_name] = ser
            console.print(f"  [green]✓ {station_name} → {selected_port.device}[/green]")
            input("Press Enter to continue...")
        except Exception as e:
            console.print(f"  [red]Failed to open {selected_port.device}: {e}[/red]")
            input("Press Enter to continue...")

    # Build the serials dict (None for unassigned stations)
    serials = {}
    for name in station_names:
        serials[name] = assignments.get(name)
    return serials


def main():
    serials = device_setup(stations_config)
    stations = load_stations(stations_config, fsm_config)
    coordinator = PipelineCoordinator(serials, stations, fsm_config)

    connected = [n for n, s in serials.items() if s is not None]
    logging.info(f"Starting with stations: {', '.join(connected)}")

    run_dashboard(coordinator, stations, serials)

main()
