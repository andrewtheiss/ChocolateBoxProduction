import sys
import select
import logging
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from src.comms import start_station, stop_station, get_status, identify

console = Console()


def render_dashboard(coordinator, stations):
    """Build and print the dashboard table."""
    console.clear()
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
    console.print(panel)


def handle_command(cmd, serials, stations):
    """Process a user command. Returns a message to display, or None."""
    parts = cmd.strip().lower().split()
    if not parts:
        return None

    action = parts[0]

    if action == 'help':
        lines = [
            "",
            "[bold]Commands:[/bold]",
            "  [cyan]<station>[/cyan]          — trigger station (e.g. 'roller')",
            "  [cyan]status <station>[/cyan]   — get station status from Arduino",
            "  [cyan]identify <station>[/cyan] — ask Arduino to identify itself",
            "  [cyan]stop <station>[/cyan]     — emergency stop",
            "  [cyan]help[/cyan]               — show this message",
            "  [cyan]quit[/cyan]               — exit",
            f"\n  Stations: {', '.join(stations.keys())}",
            "",
        ]
        return "\n".join(lines)

    if action == 'quit':
        raise SystemExit(0)

    if action == 'stop' and len(parts) > 1:
        name = parts[1]
        if name in serials and serials[name]:
            resp = stop_station(serials[name])
            return f"[yellow]stop {name}:[/yellow] {resp}"
        return f"[red]{name} is not connected.[/red]"

    if action == 'status' and len(parts) > 1:
        name = parts[1]
        if name in serials and serials[name]:
            resp = get_status(serials[name])
            return f"[cyan]status {name}:[/cyan] {resp}"
        return f"[red]{name} is not connected.[/red]"

    if action == 'identify' and len(parts) > 1:
        name = parts[1]
        if name in serials and serials[name]:
            resp = identify(serials[name])
            return f"[cyan]identify {name}:[/cyan] {resp}"
        return f"[red]{name} is not connected.[/red]"

    if action in serials and serials[action] is not None:
        ser = serials[action]
        console.print(f"[dim]Triggering {action}...[/dim]")
        resp = start_station(ser)
        return f"[green]{action}:[/green] {resp}"
    elif action in serials:
        return f"[red]{action} is not connected.[/red]"
    else:
        return f"[red]Unknown command '{cmd}'. Type 'help' for options.[/red]"


def run_dashboard(coordinator, stations, serials):
    """Unified loop: render dashboard, prompt for input, handle commands."""
    # Remove console log handler so logs only go to file
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)

    last_message = None

    while True:
        render_dashboard(coordinator, stations)

        if last_message:
            console.print(last_message)
            last_message = None

        try:
            cmd = console.input("\n[bold]>[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nExiting.")
            raise SystemExit(0)

        if not cmd:
            continue

        last_message = handle_command(cmd, serials, stations)
