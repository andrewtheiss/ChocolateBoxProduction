"""Verify Setup dashboard.

A per-subsystem (Arduino) pre-flight check. When you plug motors and sensors
in, this page shows the saved wiring (which pins each motor/sensor uses),
compares it against what the board actually reports, and lets you physically
confirm each device (nudge a motor, read a switch) and mark it verified before
moving on to homing.
"""

from nicegui import ui

from src import web_ui

from src.comms import run_motor, stop_motor, verify_pin, get_encoder

NUDGE_STEPS = 200
NUDGE_SPEED_US = 80

_STATUS_TAG = {
    'match': ('on', 'wired ok'),
    'mismatch': ('err', 'PIN MISMATCH'),
    'missing_on_board': ('err', 'not on board'),
    'extra_on_board': ('idle', 'extra on board'),
    'offline': ('off', 'offline'),
}


def _pins_text(motor):
    if not motor:
        return '—'
    return f'PUL {motor["pul_pin"]} / DIR {motor["dir_pin"]} / ENA {motor["ena_pin"]}' + \
        (' (rev)' if motor.get('reversed') else '')


@ui.page('/verify')
def verify_page():
    content = web_ui.page_layout('verify')

    with content:
        with ui.column().classes('w-full gap-2'):
            ui.label('Verify Setup').classes('h5 page-title')
            ui.label('Confirm each subsystem is wired to match its saved config, '
                     'test every motor and sensor, then proceed to homing.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.label('When ready').classes('eyebrow red')
                ui.element('div').classes('flex-grow')
                web_ui.action_button('Proceed to Homing', on_click=lambda: ui.navigate.to('/homing'),
                                     icon='home')

        for station in web_ui.STATION_ORDER:
            _render_station(station)


def _render_station(station):
    with ui.card().classes('panel w-full'):
        card_body = ui.column().classes('w-full gap-2')

        def render():
            card_body.clear()
            ser = web_ui.serials.get(station)
            saved = web_ui.get_saved_station(station)
            verification = web_ui.get_station_verification(station)
            with card_body:
                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.label(station.capitalize()).classes('eyebrow red')
                    web_ui.tag('online' if ser else 'offline', 'on' if ser else 'off')
                    if ser:
                        ui.label(ser.port).classes('mono muted text-xs')
                    ui.element('div').classes('flex-grow')
                    web_ui.action_button('Refresh', on_click=render, variant='neutral', icon='sync')

                # Verified summary
                motors = saved.get('motors', [])
                switches = saved.get('limit_switches', [])
                encoders = saved.get('encoders', [])
                total = len(motors) + len(switches) + len(encoders)
                done = (sum(1 for m in motors if verification['motors'].get(m['name']))
                        + sum(1 for s in switches if verification['limit_switches'].get(s['name']))
                        + sum(1 for e in encoders if verification['encoders'].get(e['name'])))
                ui.label(f'{done}/{total} device(s) verified').classes('mono muted text-xs')

                if total == 0:
                    ui.label('No devices configured for this station yet. '
                             'Add motors/sensors on the station page.').classes('muted text-sm')

                # ── Motors ──
                if motors:
                    ui.label('Motors').classes('eyebrow red')
                    report = web_ui.motor_config_report(station)
                    report_by_name = {r['name']: r for r in report['motors']}
                    for m in motors:
                        row = report_by_name.get(m['name'], {'status': 'offline', 'saved': m, 'live': None})
                        _render_motor_row(station, m, row, verification, render)
                    # Any extras present on the board but not saved.
                    for r in report['motors']:
                        if r['status'] == 'extra_on_board':
                            with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                                ui.label(r['name']).classes('text-sm font-medium w-28')
                                variant, text = _STATUS_TAG['extra_on_board']
                                ui.html(f'<span class="tag tag-{variant}">{text}</span>')
                                ui.label(_pins_text(r['live'])).classes('mono muted text-xs')
                                ui.label('on board but not in saved config').classes('muted text-xs')

                # ── Limit switches ──
                if switches:
                    ui.label('Limit Switches').classes('eyebrow red')
                    for sw in switches:
                        _render_switch_row(station, sw, verification, render)

                # ── Encoders ──
                if encoders:
                    ui.label('Encoders').classes('eyebrow red')
                    for enc in encoders:
                        _render_encoder_row(station, enc, verification, render)

        render()


def _render_motor_row(station, motor, row, verification, rerender):
    ser = web_ui.serials.get(station)
    with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
        ui.label(motor['name']).classes('text-sm font-medium w-28')
        variant, text = _STATUS_TAG.get(row['status'], ('off', row['status']))
        ui.html(f'<span class="tag tag-{variant}">{text}</span>')
        ui.label(_pins_text(motor)).classes('mono muted text-xs')
        if row['status'] == 'mismatch' and row.get('live'):
            ui.label(f'board: {_pins_text(row["live"])}').classes('mono red text-xs')
        ui.element('div').classes('flex-grow')

        if ser:
            def nudge(forward):
                resp = run_motor(ser, motor['name'], steps=NUDGE_STEPS, speed_us=NUDGE_SPEED_US, forward=forward)
                if not resp or resp.get('status') not in {'started', 'done'}:
                    ui.notify(f'Nudge rejected: {resp.get("error") if resp else "no response"}', type='warning')
                else:
                    ui.notify(f'Nudging {motor["name"]} {"fwd" if forward else "rev"}', type='info')

            web_ui.action_button('Nudge Fwd', on_click=lambda: nudge(True), variant='outline')
            web_ui.action_button('Nudge Rev', on_click=lambda: nudge(False), variant='outline')
            web_ui.action_button('Stop', on_click=lambda: stop_motor(ser, motor['name']), variant='danger')

        verified = bool(verification['motors'].get(motor['name']))

        def on_verify(e):
            web_ui.set_device_verified(station, 'motors', motor['name'], e.value)
            rerender()

        ui.checkbox('Verified', value=verified).on_value_change(on_verify)


def _render_switch_row(station, sw, verification, rerender):
    ser = web_ui.serials.get(station)
    with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
        ui.label(sw['name']).classes('text-sm font-medium w-28')
        web_ui.tag('NO' if sw.get('normally_open', True) else 'NC', 'idle')
        ui.label(f'pin {sw.get("pin")}').classes('mono muted text-xs')
        reading = ui.label('').classes('mono muted text-xs')
        ui.element('div').classes('flex-grow')

        if ser:
            def read():
                resp = verify_pin(ser, pin=int(sw.get('pin')), mode='input')
                if resp and 'value' in resp:
                    value = resp['value']
                    active = (value == 0) if sw.get('normally_open', True) else (value == 1)
                    reading.text = f'raw {value} -> {"TRIGGERED" if active else "open"}'
                else:
                    reading.text = 'no response'

            web_ui.action_button('Read', on_click=read, variant='neutral', icon='sensors')

        verified = bool(verification['limit_switches'].get(sw['name']))

        def on_verify(e):
            web_ui.set_device_verified(station, 'limit_switches', sw['name'], e.value)
            rerender()

        ui.checkbox('Verified', value=verified).on_value_change(on_verify)


def _render_encoder_row(station, enc, verification, rerender):
    ser = web_ui.serials.get(station)
    with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
        ui.label(enc['name']).classes('text-sm font-medium w-28')
        ui.label(f'motor {enc.get("motor") or "—"}').classes('mono muted text-xs')
        ui.label(f'A{enc.get("pin_a")}/B{enc.get("pin_b")}').classes('mono muted text-xs')
        count_lbl = ui.label('').classes('mono muted text-xs')
        ui.element('div').classes('flex-grow')

        if ser:
            def read():
                resp = get_encoder(ser, enc['name'])
                if resp and resp.get('status') == 'ok':
                    count_lbl.text = f'count {resp.get("count")}'
                else:
                    count_lbl.text = 'no response (encoder counting is reserved)'

            web_ui.action_button('Read', on_click=read, variant='neutral', icon='sensors')

        verified = bool(verification['encoders'].get(enc['name']))

        def on_verify(e):
            web_ui.set_device_verified(station, 'encoders', enc['name'], e.value)
            rerender()

        ui.checkbox('Verified', value=verified).on_value_change(on_verify)
