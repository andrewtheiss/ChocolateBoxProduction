from nicegui import ui
import json
import logging
from src.comms import (
    scan_ports, connect_serial, identify, get_status,
    start_station, stop_station, send_command,
    add_motor, remove_motor, list_motors, run_motor,
    stop_motor, verify_pin, set_station_id,
)
from src.state_machine import PipelineCoordinator

# ── State ────────────────────────────────────
serials: dict = {}
coordinator: PipelineCoordinator = None
station_configs: dict = {}
fsm_config: dict = {}
log_lines: list = []
STATION_ORDER = ['dispenser', 'roller', 'taper']

CSS = '''
body { background: #111; color: #ccc; font-family: -apple-system, "Segoe UI", sans-serif; }
.q-header { background: #111 !important; border-bottom: 1px solid #222 !important; }
.q-card { background: #181818 !important; border: 1px solid #222 !important; border-radius: 6px !important; }
.q-btn { border-radius: 4px !important; text-transform: none !important; font-weight: 500 !important; letter-spacing: 0 !important; }
.q-field--dark .q-field__control { background: #1a1a1a !important; }
.nicegui-log { background: #1a1a1a !important; border: 1px solid #222 !important; border-radius: 4px !important; font-size: 12px !important; }
.nicegui-code { background: #1a1a1a !important; border: 1px solid #222 !important; border-radius: 4px !important; }
.q-drawer { background: #151515 !important; border-right: 1px solid #222 !important; }
.q-item { min-height: 36px !important; padding: 4px 12px !important; }
.sec { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #555; padding: 12px 0 4px 0; }
.dim { color: #555; }
.mono { font-family: "SF Mono", "Fira Code", "Consolas", monospace; font-size: 12px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.tag-on { background: #062a1e; color: #34d399; }
.tag-off { background: #1a1a1a; color: #555; }
.tag-run { background: #0c2340; color: #60a5fa; }
.tag-err { background: #2a0a0a; color: #f87171; }
.tag-idle { background: #1a1800; color: #ca8a04; }
'''


def add_log(msg):
    log_lines.append(msg)
    if len(log_lines) > 200:
        log_lines.pop(0)


def tag(text, variant='off'):
    return ui.html(f'<span class="tag tag-{variant}">{text}</span>')


def theme():
    ui.dark_mode().enable()
    ui.add_css(CSS)


def ensure_coordinator():
    global coordinator
    if coordinator is None:
        for n in STATION_ORDER:
            if n not in serials:
                serials[n] = None
        coordinator = PipelineCoordinator(serials, {}, fsm_config)


# ── Layout with sidebar ─────────────────────
def page_layout(active='dashboard'):
    theme()

    with ui.left_drawer(value=True, fixed=True).classes('p-0').props('width=180 bordered') as drawer:
        ui.html('<div class="sec" style="padding:16px 12px 4px">Navigation</div>')

        items = [
            ('Dashboard', 'grid_view', '/dashboard'),
            ('Devices', 'usb', '/devices'),
        ]
        for label, icon, url in items:
            with ui.item(on_click=lambda u=url: ui.navigate.to(u)).classes(
                'rounded mx-1' + (' bg-white/5' if active == label.lower() else '')
            ):
                with ui.item_section().props('avatar'):
                    ui.icon(icon, size='18px').classes('text-zinc-400')
                ui.item_label(label).classes('text-sm')

        ui.html('<div class="sec" style="padding:16px 12px 4px">Stations</div>')
        for name in STATION_ORDER:
            online = serials.get(name) is not None
            with ui.item(on_click=lambda n=name: ui.navigate.to(f'/station/{n}')).classes(
                'rounded mx-1' + (' bg-white/5' if active == name else '')
            ):
                with ui.item_section().props('avatar'):
                    ui.icon('circle', size='8px').classes('text-emerald-500' if online else 'text-zinc-600')
                ui.item_label(name).classes('text-sm')

    with ui.header().classes('items-center px-4 h-10'):
        ui.button(icon='menu', on_click=drawer.toggle).props('flat dense color=grey size=sm')
        ui.label('ChocolateBox').classes('text-sm font-semibold ml-2')

    return ui.column().classes('w-full max-w-3xl mx-auto px-4 py-6 gap-4')


# ── Devices page ─────────────────────────────
@ui.page('/devices')
def devices_page():
    content = page_layout('devices')

    with content:
        ui.html('<div class="sec">Scan & Assign</div>')

        scan_box = ui.column().classes('w-full gap-2')

        def refresh():
            scan_box.clear()
            ports = scan_ports()
            assigned_ports = {s.port for s in serials.values() if s}

            with scan_box:
                if not ports:
                    ui.label('No USB devices found.').classes('dim text-sm')
                    return

                for p in ports:
                    is_assigned = p.device in assigned_ports
                    assigned_to = None
                    if is_assigned:
                        assigned_to = next((n for n, s in serials.items() if s and s.port == p.device), None)

                    with ui.row().classes('items-center gap-3 py-1'):
                        ui.label(p.device).classes('mono flex-shrink-0')
                        ui.label(p.description or '—').classes('text-xs dim flex-shrink-0')

                        if assigned_to:
                            tag(assigned_to, 'on')

                            def mk_disconnect(port_name):
                                def do():
                                    if serials.get(port_name):
                                        serials[port_name].close()
                                        serials[port_name] = None
                                        add_log(f'Disconnected {port_name}')
                                        ui.notify(f'{port_name} disconnected')
                                        ensure_coordinator()
                                        refresh()
                                return do

                            ui.button('Disconnect', on_click=mk_disconnect(assigned_to)).props('flat dense color=red size=xs')
                        else:
                            available = [n for n in STATION_ORDER if not serials.get(n)]
                            if available:
                                sel = ui.select(options=available, value=available[0]).props('dense outlined dark').classes('w-28')

                                def mk_connect(port_info, select_w):
                                    def do():
                                        nm = select_w.value
                                        try:
                                            ser = connect_serial(port_info.device, station_configs.get(nm, {}).get('baud', 9600))
                                            serials[nm] = ser
                                            add_log(f'{nm} → {port_info.device}')
                                            ui.notify(f'{nm} connected', type='positive')
                                            ensure_coordinator()
                                            refresh()
                                        except Exception as e:
                                            ui.notify(str(e), type='negative')
                                    return do

                                ui.button('Connect', on_click=mk_connect(p, sel)).props('flat dense color=cyan size=xs')

        ui.button('Rescan', on_click=refresh, icon='refresh').props('flat dense color=grey size=sm')

        ui.html('<div class="sec" style="margin-top:12px">Status</div>')
        for name in STATION_ORDER:
            s = serials.get(name)
            with ui.row().classes('items-center gap-3'):
                ui.label(name).classes('text-sm w-20')
                if s:
                    ui.label(s.port).classes('mono dim')
                    tag('online', 'on')
                else:
                    tag('offline', 'off')

        refresh()


# ── Dashboard ────────────────────────────────
@ui.page('/dashboard')
@ui.page('/')
def dashboard_page():
    ensure_coordinator()
    content = page_layout('dashboard')

    with content:
        # Pipeline bar
        with ui.row().classes('items-center gap-3'):
            coord_tag = ui.html('<span class="tag tag-idle">IDLE</span>')
            ui.element('div').classes('flex-grow')
            ui.button('Run', on_click=lambda: (coordinator.run_pipeline(), add_log('Pipeline triggered')),
                      icon='play_arrow').props('flat dense color=cyan size=sm')
            ui.button('Reset', on_click=lambda: coordinator.reset(),
                      icon='restart_alt').props('flat dense color=grey size=sm')

        # Station rows
        ui.html('<div class="sec">Stations</div>')
        station_els = {}

        for name in STATION_ORDER:
            ser = serials.get(name)
            with ui.row().classes('items-center gap-3 py-1 w-full'):
                ui.label(name).classes('text-sm font-medium w-20')
                state_tag = ui.html('<span class="tag tag-idle">IDLE</span>')
                items_lbl = ui.label('0').classes('mono text-sm')
                ui.label('items').classes('dim text-xs')
                ui.element('div').classes('flex-grow')

                if ser:
                    tag('on', 'on')
                else:
                    tag('off', 'off')

                def mk_t(n):
                    def f():
                        if n in coordinator.workers:
                            coordinator.run_single(n)
                            add_log(f'Triggered {n}')
                    return f

                def mk_s(n):
                    def f():
                        if serials.get(n):
                            stop_station(serials[n])
                            add_log(f'Stopped {n}')
                    return f

                ui.button('Trigger', on_click=mk_t(name)).props('flat dense size=xs color=cyan')
                ui.button('Stop', on_click=mk_s(name)).props('flat dense size=xs color=red')
                ui.button('→', on_click=lambda n=name: ui.navigate.to(f'/station/{n}')).props('flat dense size=xs color=grey')

                station_els[name] = {'state': state_tag, 'items': items_lbl}

        # Log
        ui.html('<div class="sec" style="margin-top:8px">Log</div>')
        log_disp = ui.log(max_lines=40).classes('w-full h-32')

        def tick():
            cmap = {'IDLE': 'idle', 'RUNNING': 'run', 'ERROR': 'err'}
            cv = cmap.get(coordinator.state, 'off')
            coord_tag.content = f'<span class="tag tag-{cv}">{coordinator.state}</span>'

            ws = coordinator.get_worker_states()
            smap = {'IDLE': 'idle', 'PROCESSING': 'run', 'ERROR': 'err'}
            for nm, els in station_els.items():
                if nm in ws:
                    sv = smap.get(ws[nm]['state'], 'off')
                    els['state'].content = f'<span class="tag tag-{sv}">{ws[nm]["state"]}</span>'
                    els['items'].text = str(ws[nm]['items'])

            while log_lines:
                log_disp.push(log_lines.pop(0))

        ui.timer(0.5, tick)


# ── Station detail ───────────────────────────
@ui.page('/station/{name}')
def station_detail_page(name: str):
    ensure_coordinator()
    ser = serials.get(name)
    content = page_layout(name)

    with content:

        # ── Header row ──
        with ui.row().classes('items-center gap-3'):
            ui.label(name.capitalize()).classes('text-lg font-semibold')
            if ser:
                tag('online', 'on')
                ui.label(ser.port).classes('mono dim')
            else:
                tag('offline', 'off')

        # ── Status ──
        ui.html('<div class="sec">Status</div>')
        status_out = ui.code('—').classes('w-full')

        def poll():
            if not ser:
                return
            r = get_status(ser)
            status_out.content = json.dumps(r, indent=2) if r else 'No response'

        with ui.row().classes('items-center gap-2'):
            ui.button('Refresh', on_click=poll).props('flat dense color=grey size=xs')
            auto = ui.switch('Auto').props('dense color=cyan')

        ui.timer(2.0, lambda: poll() if auto.value else None)

        # ── Motors ──
        ui.html('<div class="sec">Motors</div>')
        motors_box = ui.column().classes('w-full gap-2')

        def refresh_motors():
            motors_box.clear()
            if not ser:
                with motors_box:
                    ui.label('Not connected.').classes('dim text-sm')
                return

            r = list_motors(ser)
            mlist = r.get('motors', []) if r and r.get('status') == 'ok' else []

            if not mlist:
                with motors_box:
                    ui.label('No motors. Add one below.').classes('dim text-sm')
                return

            with motors_box:
                for m in mlist:
                    with ui.row().classes('items-center gap-2 py-1 w-full border-b border-zinc-800'):
                        ui.label(m['name']).classes('text-sm font-medium w-24')
                        tag('run' if m.get('running') else 'idle', 'run' if m.get('running') else 'idle')
                        if m.get('reversed'):
                            tag('rev', 'err')
                        ui.label(f'{m["pul_pin"]}/{m["dir_pin"]}/{m["ena_pin"]}').classes('mono dim text-xs')
                        ui.element('div').classes('flex-grow')

                        s_w = ui.number(value=1000, min=1, max=50000, step=100).props('dense outlined dark').classes('w-20').tooltip('Steps')
                        sp_w = ui.number(value=500, min=50, max=5000, step=50).props('dense outlined dark').classes('w-16').tooltip('μs/step')

                        def mk_run(mn, sw, spw, fwd):
                            def f():
                                r = run_motor(ser, mn, steps=int(sw.value), speed_us=int(spw.value), forward=fwd)
                                st = r.get('status', '?') if r else '?'
                                add_log(f'{name}/{mn} {"fwd" if fwd else "rev"} → {st}')
                                refresh_motors()
                            return f

                        def mk_stp(mn):
                            def f():
                                stop_motor(ser, mn)
                                refresh_motors()
                            return f

                        def mk_rm(mn):
                            def f():
                                remove_motor(ser, mn)
                                add_log(f'{name}: -{mn}')
                                refresh_motors()
                            return f

                        ui.button('▶', on_click=mk_run(m['name'], s_w, sp_w, True)).props('flat dense color=cyan size=xs').tooltip('Forward')
                        ui.button('◀', on_click=mk_run(m['name'], s_w, sp_w, False)).props('flat dense color=cyan size=xs').tooltip('Reverse')
                        ui.button('■', on_click=mk_stp(m['name'])).props('flat dense color=red size=xs').tooltip('Stop')
                        ui.button('✕', on_click=mk_rm(m['name'])).props('flat dense color=grey size=xs').tooltip('Remove')

        with ui.row().classes('gap-2'):
            ui.button('Refresh', on_click=refresh_motors).props('flat dense color=grey size=xs')
            if ser:
                ui.button('Stop All', on_click=lambda: (stop_station(ser), refresh_motors())).props('flat dense color=red size=xs')

        refresh_motors()

        # ── Add motor ──
        ui.html('<div class="sec">Add Motor</div>')
        if ser:
            with ui.row().classes('items-end gap-2 flex-wrap'):
                nn = ui.input('Name').props('dense outlined dark').classes('w-24')
                np = ui.number('PUL', value=9, min=0, max=19).props('dense outlined dark').classes('w-16')
                nd = ui.number('DIR', value=8, min=0, max=19).props('dense outlined dark').classes('w-16')
                ne = ui.number('ENA', value=7, min=0, max=19).props('dense outlined dark').classes('w-16')
                nr = ui.checkbox('Rev').props('dense color=cyan')

                def do_add():
                    if not nn.value:
                        ui.notify('Name required', type='warning')
                        return
                    r = add_motor(ser, name=nn.value, pul_pin=int(np.value),
                                  dir_pin=int(nd.value), ena_pin=int(ne.value), reversed=nr.value)
                    if r and r.get('status') == 'motor_added':
                        add_log(f'{name}: +{nn.value}')
                        nn.value = ''
                        refresh_motors()
                    else:
                        ui.notify(f'Failed: {r.get("error") if r else "no response"}', type='negative')

                ui.button('Add', on_click=do_add, icon='add').props('flat dense color=cyan size=xs')
        else:
            ui.label('Connect first.').classes('dim text-sm')

        # ── Pin test ──
        ui.html('<div class="sec">Pin Test</div>')
        if ser:
            with ui.row().classes('items-end gap-2'):
                tp = ui.number('Pin', value=9, min=0, max=19).props('dense outlined dark').classes('w-16')
                tm = ui.toggle(['out', 'in'], value='out').props('dense color=cyan')
                pin_out = ui.label('').classes('mono dim text-xs')

                def test():
                    mode = 'output' if tm.value == 'out' else 'input'
                    r = verify_pin(ser, pin=int(tp.value), mode=mode)
                    if r:
                        pin_out.text = json.dumps(r)
                    else:
                        pin_out.text = 'no response'

                ui.button('Test', on_click=test).props('flat dense color=grey size=xs')
        else:
            ui.label('Connect first.').classes('dim text-sm')

        # ── Raw ──
        ui.html('<div class="sec">Raw Command</div>')
        if ser:
            with ui.row().classes('items-end gap-2 w-full'):
                ci = ui.input('cmd').props('dense outlined dark').classes('flex-grow')
                ro = ui.label('').classes('mono dim text-xs')

                def raw():
                    r = send_command(ser, ci.value)
                    ro.text = json.dumps(r) if r else 'no response'

                ui.button('Send', on_click=raw).props('flat dense color=grey size=xs')
        else:
            ui.label('Connect first.').classes('dim text-sm')


# ── Entry ────────────────────────────────────
def start_web_ui(stations_cfg, fsm_cfg):
    global station_configs, fsm_config
    station_configs = stations_cfg
    fsm_config = fsm_cfg
    logging.info("Web UI → http://localhost:8080")
    ui.run(title='ChocolateBox', port=8080, reload=False, favicon='🏭')
