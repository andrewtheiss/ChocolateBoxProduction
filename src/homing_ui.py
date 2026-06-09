"""Startup / Homing page.

One place to bring every motor to its start position before running programs:
jog manually, drive automatically to a limit switch or encoder target, then
zero. Per-motor start configuration is edited here too.
"""

from nicegui import ui

from src import web_ui, program_model


def _saved_motors(station):
    return [m['name'] for m in web_ui.get_saved_station(station).get('motors', [])]


def _limit_names(station):
    return [s['name'] for s in web_ui.get_saved_station(station).get('limit_switches', [])]


def _encoder_names(station):
    return [e['name'] for e in web_ui.get_saved_station(station).get('encoders', [])]


def _home_cfg(station, motor):
    hc = web_ui.get_saved_station(station).setdefault('home_config', {})
    cfg = program_model.normalize_home_config(hc.get(motor) or {})
    hc[motor] = cfg
    return cfg


@ui.page('/homing')
def homing_page():
    controller = web_ui.ensure_homing_controller()
    content = web_ui.page_layout('homing')

    status_labels = {}  # (station, motor) -> {'pos':.., 'homed':.., 'msg':..}

    with content:
        with ui.column().classes('w-full gap-2'):
            ui.label('Startup / Homing').classes('h5 page-title')
            ui.label('Set every motor to its start position, then zero it. '
                     'Homed positions tie into programs that require homing.').classes('muted text-sm')

        # Global controls
        with ui.card().classes('panel w-full'):
            ui.label('All Motors').classes('eyebrow red')
            summary = ui.label('').classes('mono muted')

            def refresh_positions():
                controller.refresh_all_positions()
                tick()
                ui.notify('Positions refreshed', type='positive')

            def home_all():
                allowed = set()
                skipped = []
                for station in web_ui.STATION_ORDER:
                    for motor in _saved_motors(station):
                        cfg = _home_cfg(station, motor)
                        if cfg.get('method') not in (program_model.HOME_LIMIT, program_model.HOME_ENCODER):
                            continue
                        if web_ui.is_motor_verified(station, motor):
                            allowed.add((station, motor))
                        else:
                            skipped.append(f'{station}/{motor}')
                controller.home_all(allowed=allowed)
                if skipped:
                    ui.notify('Skipped (not verified): ' + ', '.join(skipped), type='warning')
                else:
                    ui.notify('Auto-homing verified motors', type='info')

            with ui.row().classes('items-center gap-2 flex-wrap'):
                web_ui.action_button('Home All', on_click=home_all, icon='home')
                web_ui.action_button('Refresh Positions', on_click=refresh_positions, variant='neutral', icon='sync')
                web_ui.action_button('Stop All', on_click=lambda: controller.stop(), variant='danger', icon='stop')
                web_ui.action_button('Verify Setup', on_click=lambda: ui.navigate.to('/verify'), variant='neutral', icon='fact_check')

        any_motor = False
        for station in web_ui.STATION_ORDER:
            motors = _saved_motors(station)
            if not motors:
                continue
            any_motor = True
            connected = web_ui.serials.get(station) is not None
            with ui.card().classes('panel w-full'):
                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.label(station.capitalize()).classes('eyebrow red')
                    web_ui.tag('online' if connected else 'offline', 'on' if connected else 'off')
                for motor in motors:
                    _render_motor(controller, station, motor, status_labels)

        if not any_motor:
            with ui.card().classes('panel w-full'):
                ui.label('No motors configured yet. Add motors on the station pages first.').classes('muted text-sm')

    def tick():
        snapshot = controller.get_status_snapshot()
        homed = 0
        total = len(status_labels)
        for (station, motor), labels in status_labels.items():
            entry = snapshot.get(f'{station}/{motor}', {})
            pos = entry.get('position')
            labels['pos'].text = f'pos {pos}' if pos is not None else 'pos —'
            is_homed = entry.get('homed')
            if is_homed:
                homed += 1
            busy = entry.get('busy')
            variant = 'run' if busy else ('on' if is_homed else 'idle')
            text = 'homing' if busy else ('homed' if is_homed else 'not homed')
            labels['homed'].content = f'<span class="tag tag-{variant}">{text}</span>'
            labels['msg'].text = entry.get('message', '')
        summary.text = f'{homed}/{total} motor(s) homed'

    ui.timer(1.0, tick)
    tick()


def _render_motor(controller, station, motor, status_labels):
    with ui.column().classes('w-full gap-2 section-row'):
        with ui.row().classes('items-center gap-3 flex-wrap'):
            ui.label(motor).classes('text-sm font-medium w-28')
            verified = web_ui.is_motor_verified(station, motor)
            web_ui.tag('verified' if verified else 'unverified', 'on' if verified else 'err')
            pos_lbl = ui.label('pos —').classes('mono muted text-xs w-24')
            homed_html = ui.html('<span class="tag tag-idle">not homed</span>')
            msg_lbl = ui.label('').classes('mono muted text-xs')
            status_labels[(station, motor)] = {'pos': pos_lbl, 'homed': homed_html, 'msg': msg_lbl}

        cfg = _home_cfg(station, motor)

        # Jog + zero controls
        with ui.row().classes('items-center gap-2 flex-wrap'):
            def jog(forward):
                ok, message = controller.jog(station, motor, forward)
                if not ok:
                    ui.notify(message, type='warning')

            def run_cont(forward):
                ok, message = controller.jog_start(station, motor, forward)
                if not ok:
                    ui.notify(message, type='warning')

            web_ui.action_button('Run Rev', on_click=lambda: run_cont(False), variant='neutral', icon='fast_rewind')
            web_ui.action_button('Jog Rev', on_click=lambda: jog(False), variant='outline')
            web_ui.action_button('Jog Fwd', on_click=lambda: jog(True), variant='outline')
            web_ui.action_button('Run Fwd', on_click=lambda: run_cont(True), variant='neutral', icon='fast_forward')
            web_ui.action_button('Stop', on_click=lambda: controller.jog_stop(station, motor), variant='danger', icon='stop')
            web_ui.action_button('Set Zero', on_click=lambda: (controller.set_zero(station, motor),
                                 ui.notify(f'{motor} zeroed', type='positive')), icon='adjust')
            if cfg.get('method') in (program_model.HOME_LIMIT, program_model.HOME_ENCODER):
                def do_home():
                    if not web_ui.is_motor_verified(station, motor):
                        ui.notify(f'Verify {motor} on the Verify Setup page before homing.', type='warning')
                        return
                    controller.home_motor(station, motor)
                    ui.notify(f'Homing {motor}', type='info')

                web_ui.action_button('Home', on_click=do_home, icon='home')

        # Start-position config (per motor)
        with ui.expansion('Start config').classes('w-full'):
            config_box = ui.column().classes('w-full gap-2')

            def render_config():
                config_box.clear()
                current = _home_cfg(station, motor)
                with config_box:
                    with ui.row().classes('items-end gap-2 flex-wrap'):
                        method_sel = ui.select(
                            options={
                                program_model.HOME_MANUAL: 'Manual jog',
                                program_model.HOME_LIMIT: 'To limit switch',
                                program_model.HOME_ENCODER: 'To encoder',
                            },
                            value=current.get('method'), label='Method',
                        ).props('outlined').classes('w-40')

                        def on_method(e):
                            current['method'] = e.value
                            web_ui.persist_state()
                            render_config()
                        method_sel.on_value_change(on_method)

                        ui.toggle(
                            {program_model.HOME_DIR_REVERSE: 'Toward home: Rev',
                             program_model.HOME_DIR_FORWARD: 'Toward home: Fwd'},
                            value=current.get('direction'),
                        ).bind_value(current, 'direction').on_value_change(lambda e: web_ui.persist_state())

                        ui.number('Jog step', min=1, max=100000, step=10).props('outlined').classes('w-28') \
                            .bind_value(current, 'jog_step').on_value_change(lambda e: web_ui.persist_state())

                    with ui.row().classes('items-end gap-2 flex-wrap'):
                        use_global = ui.checkbox('Global home speed', value=current.get('home_speed_us') is None)
                        speed_num = ui.number('Home speed us', min=1, max=5000, step=1,
                                              value=current.get('home_speed_us') or web_ui.get_global_settings().get('global_speed_us')) \
                            .props('outlined').classes('w-32')

                        def apply_speed(_=None):
                            if use_global.value:
                                current['home_speed_us'] = None
                            else:
                                current['home_speed_us'] = int(speed_num.value or 62)
                            web_ui.persist_state()
                        use_global.on_value_change(apply_speed)
                        speed_num.on_value_change(apply_speed)

                    if current.get('method') == program_model.HOME_LIMIT:
                        sw_sel = ui.select(options=_limit_names(station), value=current.get('switch') or None,
                                           label='Limit switch').props('outlined').classes('w-40')
                        sw_sel.on_value_change(lambda e: (current.__setitem__('switch', e.value or ''), web_ui.persist_state()))
                        if not _limit_names(station):
                            ui.label('No limit switches on this station yet (add on the station page).').classes('muted text-xs')

                    elif current.get('method') == program_model.HOME_ENCODER:
                        with ui.row().classes('items-end gap-2 flex-wrap'):
                            enc_sel = ui.select(options=_encoder_names(station), value=current.get('encoder') or None,
                                                label='Encoder').props('outlined').classes('w-40')
                            enc_sel.on_value_change(lambda e: (current.__setitem__('encoder', e.value or ''), web_ui.persist_state()))
                            ui.number('Target counts', min=1, max=1000000, step=1).props('outlined').classes('w-32') \
                                .bind_value(current, 'target_counts').on_value_change(lambda e: web_ui.persist_state())
                        if not _encoder_names(station):
                            ui.label('No encoders on this station yet (add on the station page).').classes('muted text-xs')

                    else:
                        ui.label('Manual: jog the motor to its start, then press Set Zero.').classes('muted text-xs')

            render_config()
