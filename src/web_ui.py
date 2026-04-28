from nicegui import ui
import json
import logging
from src.comms import (
    scan_ports, connect_serial, identify, get_status,
    start_station, stop_station, send_command,
    add_motor, remove_motor, list_motors, run_motor, run_motor_group,
    stop_motor, verify_pin, set_station_id,
)
from src.firmware import available_firmware_options, flash_firmware
from src.routine_engine import RoutineRunner
from src.state_machine import PipelineCoordinator
from src.web_state import clone_station_motors, load_web_state, save_web_state

# ── State ────────────────────────────────────
serials: dict = {}
coordinator: PipelineCoordinator = None
station_configs: dict = {}
fsm_config: dict = {}
persisted_state: dict = {}
routine_runner: RoutineRunner = None
log_lines: list = []
STATION_ORDER = ['dispenser', 'roller', 'taper']
MOTOR_PIN_PRESETS = [
    {'name': 'Motor 1', 'pul': 9, 'dir': 8, 'ena': 7},
    {'name': 'Motor 2', 'pul': 12, 'dir': 11, 'ena': 10},
    {'name': 'Motor 3', 'pul': 6, 'dir': 5, 'ena': 4},
]
station_motor_cache: dict = {}

CSS = '''
body {
    font-family: Source Sans Pro,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;
    line-height: 1.6;
    margin: 0;
    padding: .5rem;
    background-color: #f8f9fa;
    color: #000
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    background: #fff;
    border-radius: 8px;
    padding: .75rem;
    box-shadow: 0 2px 4px #0000001a
}

h1 {
    color: #000;
    font-size: 1.25rem;
    margin-bottom: .75rem;
    text-align: center
}

.h5 {
    font-size: 1.25rem;
    line-height: 1.35;
    font-weight: 600;
    margin: 0 0 .3rem
}

.widget-action-button {
    display: inline-block;
    padding: .65rem 1rem;
    background: #e60000;
    color: #fff;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-weight: 500;
    text-align: center;
    text-decoration: none;
    transition: all .2s ease;
    font-size: .9rem;
    min-width: 120px;
    line-height: 1.2
}

.widget-action-button:hover {
    background: #c00;
    transform: translateY(-1px);
    box-shadow: 0 2px 4px #0000001a
}

.widget-action-button:disabled {
    background: #ccc;
    cursor: not-allowed;
    transform: none;
    box-shadow: none
}

.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: .55rem 1rem;
    background: #e60000;
    color: #fff;
    border: 1px solid transparent;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 500;
    text-decoration: none;
    transition: all .2s ease
}

.btn:hover {
    background: #c00;
    transform: translateY(-1px);
    box-shadow: 0 2px 4px #e6000033
}

.btn:disabled {
    background: #ccc;
    border-color: #ccc;
    cursor: not-allowed;
    transform: none;
    box-shadow: none
}

.btn-outline {
    background: transparent;
    color: #e60000;
    border-color: #e60000
}

.btn-outline:hover {
    background: #e60000;
    color: #fff
}

.btn-sm {
    padding: .35rem .7rem;
    font-size: .9rem
}

.btn-block {
    width: 100%
}

.panel {
    background: #fff;
    border-radius: 8px;
    padding: .75rem;
    box-shadow: 0 1px 3px #00000014;
    border: 1px solid rgba(0,0,0,.06)
}

.badge {
    display: inline-block;
    padding: .2rem .5rem;
    border-radius: 999px;
    font-size: .8rem;
    font-weight: 600
}

.badge-primary {
    background: #e60000;
    color: #fff
}

.badge-neutral {
    background: #e9ecef;
    color: #000
}

.badge-accent {
    background: #f0b800;
    color: #000
}

.alert {
    padding: .75rem 1rem;
    border-radius: 6px;
    margin: .5rem 0;
    border-left: 4px solid transparent
}

.alert-info {
    background: #e7f3ff;
    color: #084298;
    border-left-color: #0d6efd
}

.alert-success {
    background: #e6f4ea;
    color: #0f5132;
    border-left-color: #198754
}

.alert-warning {
    background: #fff4e5;
    color: #664d03;
    border-left-color: #f6c343
}

.alert-danger {
    background: #fdecea;
    color: #842029;
    border-left-color: #dc3545
}

.input,.select,.textarea,input[type=text],input[type=number],input[type=email],input[type=password],select,textarea {
    width: 100%;
    padding: .5rem .6rem;
    border: 1px solid #dee2e6;
    border-radius: 6px;
    background: #fff;
    color: #000;
    box-shadow: inset 0 1px 2px #00000005
}

.input:focus,.select:focus,.textarea:focus,input[type=text]:focus,input[type=number]:focus,input[type=email]:focus,input[type=password]:focus,select:focus,textarea:focus {
    outline: none;
    border-color: #e60000;
    box-shadow: 0 0 0 3px #e600001f
}

.helper-text {
    color: #6c757d;
    font-size: .85rem
}

.error-text {
    color: #dc3545;
    font-size: .85rem
}

.table {
    width: 100%;
    border-collapse: collapse;
    background: #fff;
    border-radius: 6px;
    overflow: hidden
}

.table thead th {
    background: #f8f9fa;
    font-weight: 600
}

.center {
    display: grid;
    place-items: center
}

.spacer {
    height: 1rem
}

.divider {
    border-top: 1px solid #e9ecef;
    margin: .75rem 0
}

.muted {
    color: #6c757d
}

*,*:before,*:after {
    box-sizing: border-box
}

:root {
    --hw-black: #231f20;
    --hw-red: #c8102e;
    --hw-gold: #f0b323;
    --hw-gray-900: #1f2937;
    --hw-gray-800: #374151;
    --hw-gray-700: #4b5563;
    --hw-gray-600: #6b7280;
    --hw-gray-500: #9ca3af;
    --hw-gray-400: #cbd5e1;
    --hw-gray-300: #e5e7eb;
    --hw-gray-200: #edf0f3;
    --hw-gray-100: #f4f6f8;
    --hw-surface: #ffffff;
    --hw-font-sans: "Source Sans 3","Source Sans Pro","Source Sans", Arial, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans", "Liberation Sans", sans-serif;
    --hw-font-weight-black: 900;
    --hw-font-weight-bold: 700;
    --hw-font-weight-semib: 600;
    --hw-font-weight-reg: 400;
    --hw-font-weight-light: 300;
    --hw-radius: 8px;
    --hw-radius-sm: 6px;
    --hw-radius-xs: 4px;
    --hw-border: #e5e7eb;
    --hw-shadow: 0 1px 3px rgba(0,0,0,.08);
    --hw-focus-ring: 0 0 0 3px rgba(200,16,46,.18)
}

.eyebrow {
    font-size: .85rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    font-weight: var(--hw-font-weight-bold);
    color: var(--hw-gray-700)
}

.red {
    color: #bf2b34 !important;
    text-transform: uppercase
}

.mono {
    font-family: "SF Mono", "Fira Code", "Consolas", monospace;
    font-size: 12px
}

.tag {
    display: inline-block;
    padding: .2rem .55rem;
    border-radius: 999px;
    font-size: .8rem;
    font-weight: 700
}

.tag-on {
    background: #e6f4ea;
    color: #0f5132
}

.tag-off {
    background: #e9ecef;
    color: #495057
}

.tag-run {
    background: #e7f3ff;
    color: #084298
}

.tag-err {
    background: #fdecea;
    color: #842029
}

.tag-idle {
    background: #fff4e5;
    color: #664d03
}

.app-shell {
    min-height: calc(100vh - 1rem);
    display: grid;
    grid-template-rows: auto 1fr;
    gap: .75rem
}

.page {
    padding-block: 1.25rem
}

.app-shell__title {
    font-weight: 800;
    font-size: 1rem
}

.page-title {
    margin: 0;
    text-align: left
}

.content-grid {
    display: grid;
    gap: .75rem
}

.nav-item {
    min-height: 40px !important;
    padding: 6px 10px !important;
    border-radius: 6px;
    color: #000
}

.nav-item.active,
.nav-item:hover {
    background: #f8f9fa
}

.section-row {
    padding: .35rem 0
}

.readout,
.nicegui-log {
    background: #fafafa !important;
    border: 1px solid #eceff1 !important;
    border-radius: 4px !important;
    color: #000 !important;
    box-shadow: none !important
}

.readout {
    display: block;
    min-height: 2.5rem;
    padding: .45rem .55rem;
    line-height: 1.45;
    font-size: .9rem;
    white-space: pre-wrap;
    word-break: break-word;
    overflow: auto
}

.q-layout,
.q-page-container,
.q-page {
    background: transparent !important
}

.q-drawer {
    background: #fff !important;
    border-right: 1px solid #e9ecef !important
}

.q-header {
    background: #fff !important;
    color: #000 !important;
    border-bottom: 1px solid #e9ecef !important;
    box-shadow: none !important
}

.q-card.panel {
    box-shadow: 0 1px 3px #00000014 !important;
    border-radius: 8px !important;
    border: 1px solid rgba(0,0,0,.06) !important
}

.q-field__native,
.q-field__input,
.q-field__marginal,
.q-field__label {
    color: #000 !important
}

.pin-pul .q-field__label {
    color: #2563eb !important;
    font-weight: 700 !important
}

.pin-dir .q-field__label {
    color: #dc2626 !important;
    font-weight: 700 !important
}

.pin-ena .q-field__label {
    color: #16a34a !important;
    font-weight: 700 !important
}

.q-field--outlined .q-field__control:before {
    border: 1px solid #dee2e6 !important
}

.q-field--focused .q-field__control:before,
.q-field--focused .q-field__control:after {
    border-color: #e60000 !important
}

.q-toggle__inner,
.q-checkbox__inner {
    color: #e60000 !important
}

.btn-neutral {
    background: #e9ecef !important;
    color: #000 !important;
    border-color: #dee2e6 !important
}

.btn-neutral:hover {
    background: #dde2e6 !important
}

.btn-danger {
    background: #842029 !important;
    color: #fff !important
}

.btn-danger:hover {
    background: #6d1b25 !important
}

@media(max-width: 768px) {
    body {
        padding: .25rem
    }

    .container {
        padding: .5rem
    }
}
'''


def add_log(msg):
    log_lines.append(msg)
    if len(log_lines) > 200:
        log_lines.pop(0)


def tag(text, variant='off'):
    return ui.html(f'<span class="tag tag-{variant}">{text}</span>')


def theme():
    ui.add_css(CSS)


def action_button(label, on_click=None, icon=None, variant='primary'):
    classes = ['btn']
    if variant == 'outline':
        classes.append('btn-outline')
    elif variant == 'neutral':
        classes.append('btn-neutral')
    elif variant == 'danger':
        classes.append('btn-danger')
    classes.append('btn-sm')
    return ui.button(label, on_click=on_click, icon=icon).props('unelevated no-caps').classes(' '.join(classes))


def ensure_coordinator():
    global coordinator
    if coordinator is None:
        for n in STATION_ORDER:
            if n not in serials:
                serials[n] = None
        coordinator = PipelineCoordinator(serials, {}, fsm_config)


def rebuild_coordinator():
    global coordinator
    if coordinator is not None:
        coordinator.shutdown()
    coordinator = None
    ensure_coordinator()


def ensure_routine_runner():
    global routine_runner
    if routine_runner is None:
        routine_runner = RoutineRunner(lambda: serials, add_log)


def get_saved_station(name):
    stations = persisted_state.setdefault('stations', {})
    station = stations.setdefault(name, {
        'port_hint': None,
        'firmware': None,
        'motors': [],
        'limits': [],
        'triggers': [],
    })
    station.setdefault('port_hint', None)
    station.setdefault('firmware', None)
    station.setdefault('motors', [])
    station.setdefault('limits', [])
    station.setdefault('triggers', [])
    return station


def get_routines():
    return persisted_state.setdefault('routines', [])


def get_routine(name):
    for routine in get_routines():
        if routine.get('name') == name:
            return routine
    return None


def ensure_routine_defaults(routine):
    routine.setdefault('name', 'New Routine')
    routine.setdefault('steps', [])
    routine.setdefault('trigger', {'type': 'manual'})
    routine.setdefault('repeat', False)
    return routine


def make_new_routine(name):
    return ensure_routine_defaults({
        'name': name,
        'steps': [],
        'trigger': {'type': 'manual'},
        'repeat': False,
    })


def persist_state():
    save_web_state(persisted_state)


def remember_port_hint(name, port):
    station = get_saved_station(name)
    station['port_hint'] = port
    persist_state()


def remember_station_firmware(name, info):
    if not info:
        return
    station = get_saved_station(name)
    station['firmware'] = info.get('firmware', info.get('id'))
    persist_state()


def remember_station_motors(name, motors):
    station = get_saved_station(name)
    station['motors'] = clone_station_motors(motors)
    station_motor_cache[name] = clone_station_motors(motors)
    persist_state()


def read_live_motors(ser):
    response = list_motors(ser)
    if response and response.get('status') == 'ok':
        return response.get('motors', [])
    return None


def sync_station_state_from_board(name, ser):
    motors = read_live_motors(ser)
    if motors is None:
        return None
    remember_station_motors(name, motors)
    return motors


def apply_saved_station_state(name, ser):
    if ser is None:
        return {'status': 'not_connected'}

    identity = identify(ser)
    if not identity:
        return {'status': 'no_identity'}

    remember_station_firmware(name, identity)
    if identity.get('firmware') != 'generic':
        return {'status': 'unsupported_firmware', 'firmware': identity.get('firmware')}

    saved_motors = clone_station_motors(get_saved_station(name).get('motors', []))
    if not saved_motors:
        return {'status': 'no_saved_state'}

    current_motors = read_live_motors(ser)
    if current_motors is None:
        return {'status': 'read_failed'}

    current_by_name = {motor['name']: motor for motor in current_motors}
    saved_by_name = {motor['name']: motor for motor in saved_motors}

    def same_config(current_motor, saved_motor):
        return (
            int(current_motor['pul_pin']) == int(saved_motor['pul_pin'])
            and int(current_motor['dir_pin']) == int(saved_motor['dir_pin'])
            and int(current_motor['ena_pin']) == int(saved_motor['ena_pin'])
            and bool(current_motor.get('reversed', False)) == bool(saved_motor.get('reversed', False))
        )

    for motor_name, current_motor in list(current_by_name.items()):
        saved_motor = saved_by_name.get(motor_name)
        if saved_motor is None or not same_config(current_motor, saved_motor):
            resp = remove_motor(ser, motor_name)
            if not resp or resp.get('status') not in {'motor_removed', 'ok'}:
                return {'status': 'remove_failed', 'motor': motor_name, 'response': resp}

    current_motors = read_live_motors(ser) or []
    current_by_name = {motor['name']: motor for motor in current_motors}

    for saved_motor in saved_motors:
        if saved_motor['name'] in current_by_name:
            continue
        resp = add_motor(
            ser,
            name=saved_motor['name'],
            pul_pin=int(saved_motor['pul_pin']),
            dir_pin=int(saved_motor['dir_pin']),
            ena_pin=int(saved_motor['ena_pin']),
            reversed=bool(saved_motor.get('reversed', False)),
        )
        if not resp or resp.get('status') != 'motor_added':
            return {'status': 'add_failed', 'motor': saved_motor['name'], 'response': resp}

    remember_station_motors(name, saved_motors)
    return {'status': 'restored', 'count': len(saved_motors)}


def step_summary(step):
    step_type = step.get('type')
    if step_type == 'run_motor_for':
        direction = 'forward' if step.get('forward', True) else 'reverse'
        return (
            f'{step.get("station")} → motor {step.get("motor")} {direction} '
            f'for {step.get("duration_ms")}ms @ {step.get("speed_us")}us'
        )
    if step_type == 'run_group_for':
        motor_parts = []
        for motor in step.get('motors', []):
            motor_parts.append(f'{motor.get("name")}({"fwd" if motor.get("forward", True) else "rev"})')
        return (
            f'{step.get("station")} → group [{", ".join(motor_parts)}] '
            f'for {step.get("duration_ms")}ms @ {step.get("speed_us")}us'
        )
    if step_type == 'delay':
        return f'delay for {step.get("duration_ms")}ms'
    return json.dumps(step)


# ── Layout with sidebar ─────────────────────
def page_layout(active='dashboard'):
    theme()

    with ui.left_drawer(value=True, fixed=True).classes('p-0').props('width=220 bordered') as drawer:
        with ui.column().classes('w-full gap-1 p-3'):
            ui.label('Navigation').classes('eyebrow red')

            items = [
                ('Dashboard', 'grid_view', '/dashboard'),
                ('Devices', 'usb', '/devices'),
                ('Routines', 'playlist_play', '/routines'),
            ]
            for label, icon, url in items:
                with ui.item(on_click=lambda u=url: ui.navigate.to(u)).classes(
                    'nav-item' + (' active' if active == label.lower() else '')
                ):
                    with ui.item_section().props('avatar'):
                        ui.icon(icon, size='18px').classes('muted')
                    ui.item_label(label).classes('text-sm')

            ui.separator().classes('divider')
            ui.label('Stations').classes('eyebrow red')
            for name in STATION_ORDER:
                online = serials.get(name) is not None
                with ui.item(on_click=lambda n=name: ui.navigate.to(f'/station/{n}')).classes(
                    'nav-item' + (' active' if active == name else '')
                ):
                    with ui.item_section().props('avatar'):
                        ui.icon('circle', size='8px').classes('text-green-600' if online else 'muted')
                    ui.item_label(name.capitalize()).classes('text-sm')

    with ui.header().classes('items-center px-4 py-2'):
        action_button('', on_click=drawer.toggle, icon='menu', variant='neutral').props('round')
        ui.label('ChocolateBox Production').classes('app-shell__title ml-2')

    return ui.column().classes('container page content-grid')


# ── Devices page ─────────────────────────────
@ui.page('/devices')
def devices_page():
    content = page_layout('devices')

    with content:
        ui.label('Devices').classes('h5 red page-title')

        with ui.card().classes('panel w-full'):
            with ui.column().classes('w-full gap-3'):
                ui.label('Scan & Assign').classes('eyebrow red')
                scan_box = ui.column().classes('w-full gap-2')

                def refresh():
                    scan_box.clear()
                    ports = scan_ports()
                    assigned_ports = {s.port for s in serials.values() if s}

                    with scan_box:
                        if not ports:
                            ui.label('No USB devices found.').classes('muted text-sm')
                            return

                        for p in ports:
                            is_assigned = p.device in assigned_ports
                            assigned_to = None
                            assigned_info = None
                            if is_assigned:
                                assigned_to = next((n for n, s in serials.items() if s and s.port == p.device), None)
                                if assigned_to and serials.get(assigned_to):
                                    assigned_info = identify(serials[assigned_to])

                            with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                                ui.label(p.device).classes('mono')
                                ui.label(p.description or '—').classes('text-xs muted')
                                if assigned_info:
                                    fw = assigned_info.get('firmware', assigned_info.get('id', '?'))
                                    ver = assigned_info.get('version', '?')
                                    build = assigned_info.get('build', '?')
                                    ui.label(f'{fw} {ver}').classes('mono muted text-xs')
                                    ui.label(build).classes('mono muted text-xs')
                                ui.element('div').classes('flex-grow')

                                if assigned_to:
                                    tag(assigned_to, 'on')

                                    def mk_disconnect(port_name):
                                        def do():
                                            if serials.get(port_name):
                                                serials[port_name].close()
                                                serials[port_name] = None
                                                add_log(f'Disconnected {port_name}')
                                                ui.notify(f'{port_name} disconnected')
                                                rebuild_coordinator()
                                                refresh()
                                        return do

                                    action_button('Disconnect', on_click=mk_disconnect(assigned_to), variant='danger')
                                else:
                                    available = [n for n in STATION_ORDER if not serials.get(n)]
                                    if available:
                                        sel = ui.select(options=available, value=available[0]).props('outlined').classes('w-32')

                                        def mk_connect(port_info, select_w):
                                            def do():
                                                nm = select_w.value
                                                try:
                                                    ser = connect_serial(port_info.device, station_configs.get(nm, {}).get('baud', 9600))
                                                    serials[nm] = ser
                                                    remember_port_hint(nm, port_info.device)
                                                    info = identify(ser)
                                                    remember_station_firmware(nm, info)
                                                    restore = apply_saved_station_state(nm, ser)
                                                    add_log(f'{nm} → {port_info.device}')
                                                    ui.notify(f'{nm} connected', type='positive')
                                                    if restore.get('status') == 'restored':
                                                        ui.notify(f'Restored {restore["count"]} saved motor(s) to {nm}', type='positive')
                                                    rebuild_coordinator()
                                                    refresh()
                                                except Exception as e:
                                                    ui.notify(str(e), type='negative')
                                            return do

                                        action_button('Connect', on_click=mk_connect(p, sel))

                action_button('Rescan', on_click=refresh, icon='refresh', variant='neutral')

        with ui.card().classes('panel w-full'):
            with ui.column().classes('w-full gap-2'):
                ui.label('Status').classes('eyebrow red')
                for name in STATION_ORDER:
                    s = serials.get(name)
                    with ui.row().classes('items-center gap-3 section-row'):
                        ui.label(name.capitalize()).classes('text-sm w-20')
                        if s:
                            info = identify(s)
                            ui.label(s.port).classes('mono muted')
                            tag('online', 'on')
                            if info:
                                fw = info.get('firmware', info.get('id', '?'))
                                ver = info.get('version', '?')
                                build = info.get('build', '?')
                                ui.label(f'{fw} {ver}').classes('mono muted text-xs')
                                ui.label(build).classes('mono muted text-xs')
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
        ui.label('Dashboard').classes('h5 red page-title')

        with ui.card().classes('panel w-full'):
            with ui.row().classes('items-center gap-3 flex-wrap'):
                coord_tag = ui.html('<span class="tag tag-idle">IDLE</span>')
                ui.element('div').classes('flex-grow')
                action_button('Run', on_click=lambda: (coordinator.run_pipeline(), add_log('Pipeline triggered')),
                              icon='play_arrow')
                action_button('Reset', on_click=lambda: coordinator.reset(),
                              icon='restart_alt', variant='neutral')

        with ui.card().classes('panel w-full'):
            ui.label('Stations').classes('eyebrow red')
            station_els = {}

            for name in STATION_ORDER:
                ser = serials.get(name)
                with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                    ui.label(name.capitalize()).classes('text-sm font-medium w-24')
                    state_tag = ui.html('<span class="tag tag-idle">IDLE</span>')
                    items_lbl = ui.label('0').classes('mono text-sm')
                    ui.label('items').classes('muted text-xs')
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

                    action_button('Trigger', on_click=mk_t(name))
                    action_button('Stop', on_click=mk_s(name), variant='danger')
                    action_button('Details', on_click=lambda n=name: ui.navigate.to(f'/station/{n}'), variant='outline')

                    station_els[name] = {'state': state_tag, 'items': items_lbl}

        with ui.card().classes('panel w-full'):
            ui.label('Log').classes('eyebrow red')
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
    selected_motors = set()
    motor_state = {'count': 0}

    with content:
        with ui.row().classes('items-center gap-3 flex-wrap'):
            ui.label(name.capitalize()).classes('h5 red page-title')
            if ser:
                online_tag = tag('online', 'on')
                ui.label(ser.port).classes('mono muted')
            else:
                online_tag = tag('offline', 'off')

        with ui.card().classes('panel w-full'):
            ui.label('Status').classes('eyebrow red')
            firmware_meta = ui.label('Firmware: —').classes('mono muted text-xs')
            status_out = ui.label('—').classes('w-full readout mono')

            def refresh_identity():
                if not ser:
                    firmware_meta.text = 'Firmware: —'
                    return None

                info = identify(ser)
                if info:
                    fw = info.get('firmware', info.get('id', '?'))
                    ver = info.get('version', '?')
                    build = info.get('build', '?')
                    firmware_meta.text = f'Firmware: {fw} | Version: {ver} | Build: {build}'
                else:
                    firmware_meta.text = 'Firmware: no response'
                return info

            def poll():
                if not ser:
                    firmware_meta.text = 'Firmware: —'
                    return
                refresh_identity()
                r = get_status(ser)
                status_out.text = json.dumps(r, indent=2) if r else 'No response'

            with ui.row().classes('items-center gap-2 flex-wrap'):
                action_button('Refresh', on_click=poll, variant='neutral')
                auto = ui.switch('Auto')

            ui.timer(2.0, lambda: poll() if auto.value else None)

        with ui.card().classes('panel w-full'):
            ui.label('Firmware').classes('eyebrow red')
            if ser:
                firmware_options = available_firmware_options(name)
                with ui.row().classes('items-end gap-2 flex-wrap w-full'):
                    fw_sel = ui.select(
                        options=firmware_options,
                        value=next(iter(firmware_options)),
                    ).props('outlined').classes('w-40')
                    fw_out = ui.label('—').classes('w-full readout mono')

                    def do_flash():
                        nonlocal ser

                        current_ser = ser
                        current_port = current_ser.port if current_ser else None
                        if not current_port:
                            ui.notify('No connected device for this station', type='warning')
                            return

                        try:
                            current_ser.close()
                        except Exception:
                            pass

                        ser = None
                        serials[name] = None
                        rebuild_coordinator()
                        online_tag.content = '<span class="tag tag-off">offline</span>'
                        fw_out.text = 'Compiling and uploading...'
                        ui.notify(f'Flashing {fw_sel.value} to {name}', type='info')

                        result = flash_firmware(current_port, fw_sel.value)
                        fw_out.text = result['output'] or 'No output'

                        try:
                            reopened = connect_serial(
                                current_port,
                                station_configs.get(name, {}).get('baud', 9600),
                            )
                            ser = reopened
                            serials[name] = reopened
                            remember_port_hint(name, current_port)
                            info = identify(reopened)
                            remember_station_firmware(name, info)
                            restore = apply_saved_station_state(name, reopened)
                            rebuild_coordinator()
                            online_tag.content = '<span class="tag tag-on">online</span>'
                            if restore.get('status') == 'restored':
                                fw_out.text += f'\n\nRestored {restore["count"]} saved motor(s).'
                        except Exception as exc:
                            ser = None
                            serials[name] = None
                            rebuild_coordinator()
                            online_tag.content = '<span class="tag tag-off">offline</span>'
                            fw_out.text += f'\n\nReconnect failed: {exc}'

                        if result['ok']:
                            add_log(f'Flashed {fw_sel.value} to {name}')
                            ui.notify('Flash complete', type='positive')
                            refresh_identity()
                            poll()
                            refresh_motors()
                        else:
                            ui.notify('Flash failed', type='negative')

                    action_button('Flash', on_click=do_flash, icon='memory')
                    action_button('Read Version', on_click=refresh_identity, variant='neutral')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            ui.label('Saved State').classes('eyebrow red')
            saved_state_meta = ui.label('').classes('mono muted text-xs')

            def refresh_saved_state_meta():
                station = get_saved_station(name)
                saved_state_meta.text = (
                    f'Saved motors: {len(station.get("motors", []))} | '
                    f'Saved firmware: {station.get("firmware") or "—"} | '
                    f'Routines: {len(persisted_state.get("routines", []))}'
                )

            def save_from_board():
                if not ser:
                    ui.notify('Connect first', type='warning')
                    return
                motors = sync_station_state_from_board(name, ser)
                if motors is None:
                    ui.notify('Could not read board state', type='negative')
                    return
                refresh_saved_state_meta()
                ui.notify(f'Saved {len(motors)} motor(s) to web state', type='positive')

            def refresh_state():
                if not ser:
                    ui.notify('Connect first', type='warning')
                    return
                result = apply_saved_station_state(name, ser)
                refresh_saved_state_meta()
                if result.get('status') == 'restored':
                    add_log(f'{name}: restored saved state')
                    ui.notify(f'Restored {result["count"]} saved motor(s)', type='positive')
                    refresh_motors(force_result_text='Restored saved web state')
                elif result.get('status') == 'no_saved_state':
                    ui.notify('No saved web state for this station yet', type='warning')
                elif result.get('status') == 'unsupported_firmware':
                    ui.notify(f'Saved state restore only applies to generic firmware ({result.get("firmware")})', type='warning')
                else:
                    ui.notify(f'Refresh state failed: {result.get("status")}', type='negative')

            with ui.row().classes('items-center gap-2 flex-wrap'):
                action_button('Refresh State', on_click=refresh_state, icon='sync')
                action_button('Save From Board', on_click=save_from_board, variant='neutral')
            refresh_saved_state_meta()

        with ui.card().classes('panel w-full'):
            ui.label('Motors').classes('eyebrow red')
            motor_status = ui.label('').classes('mono muted text-xs')
            motors_box = ui.column().classes('w-full gap-2')
            tester_options = {}
            tester_motor = None
            tester_result = None
            nn = None
            np = None
            nd = None
            ne = None
            nr = None

            def apply_default_motor_preset():
                if not all(widget is not None for widget in [nn, np, nd, ne, nr]):
                    return

                preset_index = min(motor_state['count'], len(MOTOR_PIN_PRESETS) - 1)
                preset = MOTOR_PIN_PRESETS[preset_index]

                if not nn.value:
                    nn.value = preset['name']
                np.value = preset['pul']
                nd.value = preset['dir']
                ne.value = preset['ena']
                nr.value = False

            def refresh_motors(force_result_text=None):
                motors_box.clear()
                cached_motors = station_motor_cache.get(name, [])

                if ser:
                    r = list_motors(ser)
                    if r and r.get('status') == 'ok':
                        mlist = r.get('motors', [])
                        station_motor_cache[name] = mlist
                        motor_status.text = f'Live inventory: {len(mlist)} motor(s)'
                    else:
                        mlist = cached_motors
                        if cached_motors:
                            motor_status.text = 'Live refresh failed; showing cached motors'
                        else:
                            motor_status.text = 'No motor inventory response'
                else:
                    mlist = cached_motors
                    motor_status.text = 'Disconnected; showing cached motors' if cached_motors else 'No connection'

                motor_state['count'] = len(mlist)
                current_motor_names = {m['name'] for m in mlist}
                selected_motors.intersection_update(current_motor_names)
                tester_options.clear()
                tester_options.update({m['name']: m['name'] for m in mlist})
                if tester_motor is not None:
                    tester_motor.options = tester_options
                    if tester_motor.value not in tester_options:
                        tester_motor.value = next(iter(tester_options), None)
                    tester_motor.update()

                if not mlist:
                    with motors_box:
                        ui.label('No motors configured yet. Add one below.').classes('muted text-sm')
                    if tester_result is not None and force_result_text is not None:
                        tester_result.text = force_result_text
                    return

                if tester_result is not None and force_result_text is not None:
                    tester_result.text = force_result_text
                apply_default_motor_preset()
                with motors_box:
                    for m in mlist:
                        with ui.row().classes('items-center gap-2 section-row w-full flex-wrap'):
                            selected_box = ui.checkbox(value=m['name'] in selected_motors)

                            def update_selected(event, motor_name=m['name']):
                                if event.value:
                                    selected_motors.add(motor_name)
                                else:
                                    selected_motors.discard(motor_name)
                                if tester_result is not None and not force_result_text:
                                    tester_result.text = f'{len(selected_motors)} motor(s) checked'

                            selected_box.on_value_change(update_selected)
                            ui.label(m['name']).classes('text-sm font-medium w-24')
                            tag('run' if m.get('running') else 'idle', 'run' if m.get('running') else 'idle')
                            if m.get('reversed'):
                                tag('rev', 'err')
                            ui.label(f'{m["pul_pin"]}/{m["dir_pin"]}/{m["ena_pin"]}').classes('mono muted text-xs')
                            ui.element('div').classes('flex-grow')

                            s_w = ui.number(value=1000, min=1, max=50000, step=100).props('outlined').classes('w-24').tooltip('Steps')
                            sp_w = ui.number(value=500, min=50, max=5000, step=50).props('outlined').classes('w-20').tooltip('μs/step')

                            def mk_run(mn, sw, spw, fwd):
                                def f():
                                    r = run_motor(ser, mn, steps=int(sw.value), speed_us=int(spw.value), forward=fwd)
                                    st = r.get('status', '?') if r else '?'
                                    add_log(f'{name}/{mn} {"fwd" if fwd else "rev"} → {st}')
                                    refresh_motors(force_result_text=json.dumps(r) if r else 'no response')
                                return f

                            def mk_stp(mn):
                                def f():
                                    stop_motor(ser, mn)
                                    refresh_motors(force_result_text=f'Stopped {mn}')
                                return f

                            def mk_rm(mn):
                                def f():
                                    resp = remove_motor(ser, mn)
                                    if resp and resp.get('status') in {'motor_removed', 'ok'}:
                                        add_log(f'{name}: -{mn}')
                                        sync_station_state_from_board(name, ser)
                                        refresh_saved_state_meta()
                                        refresh_motors(force_result_text=f'Removed {mn}')
                                    else:
                                        ui.notify(f'Failed to remove {mn}', type='negative')
                                return f

                            action_button('Forward', on_click=mk_run(m['name'], s_w, sp_w, True))
                            action_button('Reverse', on_click=mk_run(m['name'], s_w, sp_w, False), variant='outline')
                            action_button('Stop', on_click=mk_stp(m['name']), variant='danger')
                            action_button('Remove', on_click=mk_rm(m['name']), variant='neutral')

            with ui.row().classes('gap-2 flex-wrap'):
                action_button('Refresh', on_click=refresh_motors, variant='neutral')
                if ser:
                    action_button('Stop All', on_click=lambda: (stop_station(ser), refresh_motors(force_result_text='Stopped all motors')), variant='danger')

        with ui.card().classes('panel w-full'):
            ui.label('Motor Tester').classes('eyebrow red')
            if ser:
                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('items-end gap-2 flex-wrap'):
                        tester_motor = ui.select(options={}).props('outlined').classes('w-36')
                        tester_steps = ui.number('Steps', value=1000, min=1, max=50000, step=100).props('outlined').classes('w-28')
                        tester_speed = ui.number('Speed μs', value=500, min=50, max=5000, step=50).props('outlined').classes('w-28')
                        tester_direction = ui.toggle(['forward', 'reverse'], value='forward')

                        def run_test():
                            if not tester_motor.value:
                                ui.notify('Choose a motor to test', type='warning')
                                return
                            resp = run_motor(
                                ser,
                                tester_motor.value,
                                steps=int(tester_steps.value),
                                speed_us=int(tester_speed.value),
                                forward=tester_direction.value == 'forward',
                            )
                            status = resp.get('status', 'no response') if resp else 'no response'
                            tester_result.text = json.dumps(resp) if resp else 'no response'
                            add_log(f'{name}/{tester_motor.value} test → {status}')
                            refresh_motors(force_result_text=tester_result.text)

                        action_button('Run Test', on_click=run_test, icon='play_arrow')
                        action_button(
                            'Stop Test',
                            on_click=lambda: (stop_motor(ser, tester_motor.value), refresh_motors(force_result_text=f'Stopped {tester_motor.value}' if tester_motor.value else 'Stopped test')),
                            variant='danger',
                        )

                    with ui.row().classes('items-end gap-2 flex-wrap'):
                        def run_checked(forward):
                            names = sorted(selected_motors)
                            if not names:
                                ui.notify('Check at least one motor', type='warning')
                                return
                            resp = run_motor_group(
                                ser,
                                names=names,
                                steps=int(tester_steps.value),
                                speed_us=int(tester_speed.value),
                                forward=forward,
                            )
                            status = resp.get('status', 'no response') if resp else 'no response'
                            tester_result.text = json.dumps(resp) if resp else 'no response'
                            add_log(f'{name}/group {",".join(names)} → {status}')
                            refresh_motors(force_result_text=tester_result.text)

                        action_button('Run Checked', on_click=lambda: run_checked(True), icon='playlist_play')
                        action_button('Run Checked Rev', on_click=lambda: run_checked(False), variant='outline')
                        action_button('Clear Checks', on_click=lambda: (selected_motors.clear(), refresh_motors(force_result_text='Cleared checked motors')), variant='neutral')

                    tester_result = ui.label('Select a motor and run a test').classes('mono muted text-xs')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        refresh_motors()

        with ui.card().classes('panel w-full'):
            ui.label('Add Motor').classes('eyebrow red')
            if ser:
                with ui.row().classes('items-end gap-2 flex-wrap'):
                    nn = ui.input('Name').props('outlined').classes('w-28')
                    np = ui.number('PUL', value=9, min=0, max=19).props('outlined').classes('w-20 pin-pul')
                    nd = ui.number('DIR', value=8, min=0, max=19).props('outlined').classes('w-20 pin-dir')
                    ne = ui.number('ENA', value=7, min=0, max=19).props('outlined').classes('w-20 pin-ena')
                    nr = ui.checkbox('Rev')

                    def do_add():
                        if not nn.value:
                            ui.notify('Name required', type='warning')
                            return
                        r = add_motor(ser, name=nn.value, pul_pin=int(np.value),
                                      dir_pin=int(nd.value), ena_pin=int(ne.value), reversed=nr.value)
                        if r and r.get('status') == 'motor_added':
                            add_log(f'{name}: +{nn.value}')
                            nn.value = ''
                            sync_station_state_from_board(name, ser)
                            refresh_saved_state_meta()
                            refresh_motors()
                        else:
                            ui.notify(f'Failed: {r.get("error") if r else "no response"}', type='negative')

                    action_button('Add', on_click=do_add, icon='add')
                    action_button('Preset', on_click=apply_default_motor_preset, variant='neutral')
                ui.label('Default motor presets: 9/8/7, 12/11/10, 6/5/4').classes('mono muted text-xs')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        apply_default_motor_preset()
        with ui.card().classes('panel w-full'):
            ui.label('Pin Test').classes('eyebrow red')
            if ser:
                with ui.row().classes('items-end gap-2 flex-wrap'):
                    tp = ui.number('Pin', value=9, min=0, max=19).props('outlined').classes('w-20')
                    tm = ui.toggle(['out', 'in'], value='out')
                    pin_out = ui.label('').classes('mono muted text-xs')

                    def test():
                        mode = 'output' if tm.value == 'out' else 'input'
                        r = verify_pin(ser, pin=int(tp.value), mode=mode)
                        if r:
                            pin_out.text = json.dumps(r)
                        else:
                            pin_out.text = 'no response'

                    action_button('Test', on_click=test, variant='neutral')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            ui.label('Raw Command').classes('eyebrow red')
            if ser:
                with ui.row().classes('items-end gap-2 w-full flex-wrap'):
                    ci = ui.input('cmd').props('outlined').classes('flex-grow')
                    ro = ui.label('').classes('mono muted text-xs')

                    def raw():
                        r = send_command(ser, ci.value)
                        ro.text = json.dumps(r) if r else 'no response'

                    action_button('Send', on_click=raw, variant='neutral')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        refresh_identity()


# ── Entry ────────────────────────────────────
def start_web_ui(stations_cfg, fsm_cfg):
    global station_configs, fsm_config, persisted_state
    station_configs = stations_cfg
    fsm_config = fsm_cfg
    persisted_state = load_web_state(STATION_ORDER)
    for station_name in STATION_ORDER:
        station_motor_cache[station_name] = clone_station_motors(
            get_saved_station(station_name).get('motors', [])
        )
    logging.info("Web UI → http://localhost:8080")
    ui.run(title='ChocolateBox', port=8080, reload=False, favicon='🏭')
