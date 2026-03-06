import logging
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from src.comms import stop_station, get_status, identify

console = Console()


def render_dashboard(coordinator, stations):
    """Build and print the dashboard table."""
    console.clear()
    table = Table(title="Production Line Status")
    table.add_column("Station")
    table.add_column("Worker")
    table.add_column("Connection")
    table.add_column("Items")

    worker_states = coordinator.get_worker_states()

    for name, station in stations.items():
        ser = coordinator.serials.get(name)

        if ser is not None:
            conn_col = "[green]connected[/green]"
        else:
            conn_col = "[dim]not connected[/dim]"

        if name in worker_states:
            ws = worker_states[name]
            w_state = ws['state']
            items = str(ws['items'])

            state_color = {
                'IDLE': 'yellow',
                'PROCESSING': 'cyan',
                'ERROR': 'red',
            }.get(w_state, 'white')

            worker_col = f"[{state_color}]{w_state}[/{state_color}]"
        else:
            worker_col = "[dim]—[/dim]"
            items = "—"

        table.add_row(name, worker_col, conn_col, items)

    pipeline_str = " → ".join(coordinator.station_order) if coordinator.station_order else "(none)"
    coord_state = coordinator.state
    coord_color = 'green' if coord_state == 'RUNNING' else ('red' if coord_state == 'ERROR' else 'yellow')

    panel = Panel(
        table,
        subtitle=f"Pipeline: {pipeline_str}  |  Coordinator: [{coord_color}]{coord_state}[/{coord_color}]",
    )
    console.print(panel)


def handle_command(cmd, coordinator, serials, stations):
    """Process a user command. Returns a message to display, or None."""
    parts = cmd.strip().lower().split()
    if not parts:
        return None

    action = parts[0]

    if action == 'help':
        connected = list(coordinator.station_order)
        lines = [
            "",
            "[bold]Pipeline:[/bold]",
            f"  [cyan]run[/cyan]                — trigger pipeline: {' → '.join(connected) if connected else '(none)'}",
            "  [cyan]run <n>[/cyan]            — trigger pipeline n times",
            "  [cyan]reset[/cyan]              — reset all workers after error",
            "",
            "[bold]Stations:[/bold]",
            "  [cyan]<station>[/cyan]          — trigger one station independently",
            "  [cyan]status <station>[/cyan]   — get status from Arduino",
            "  [cyan]identify <station>[/cyan] — ask Arduino to identify",
            "  [cyan]stop <station>[/cyan]     — emergency stop",
            "",
            "[bold]General:[/bold]",
            "  [cyan]help[/cyan]               — show this message",
            "  [cyan]quit[/cyan]               — exit",
            f"\n  Pipeline: {' → '.join(connected) if connected else '(none connected)'}",
            "",
        ]
        return "\n".join(lines)

    if action == 'quit':
        raise SystemExit(0)

    if action == 'run':
        count = 1
        if len(parts) > 1:
            try:
                count = int(parts[1])
            except ValueError:
                return "[red]Usage: run <number>[/red]"
        messages = []
        for i in range(count):
            msg = coordinator.run_pipeline()
            messages.append(msg)
        return "\n".join(messages)

    if action == 'reset':
        return coordinator.reset()

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

    if action in coordinator.workers:
        return coordinator.run_single(action)
    elif action in serials:
        return f"[red]{action} is not connected.[/red]"
    else:
        return f"[red]Unknown command '{cmd}'. Type 'help' for options.[/red]"


def run_dashboard(coordinator, stations, serials):
    """Unified loop: render dashboard, prompt for input, handle commands."""
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

        last_message = handle_command(cmd, coordinator, serials, stations)
