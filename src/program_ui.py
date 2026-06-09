"""Visual program editor pages for the motor sequencing platform.

Registers three NiceGUI routes:
  /routines        - program list + runner controls (kept at this path so the
                     existing nav link works; labelled "Programs" in the UI)
  /program/{name}  - the step-by-step visual editor for one program
  /settings        - global settings (shared speed default)

Shared application state (serials, persisted_state, helpers, layout) lives in
web_ui; this module accesses it at request time via the web_ui module object,
so the circular import between the two is safe.
"""

import json
from urllib.parse import quote

from nicegui import ui

from src import web_ui, program_model


# ── Summaries ────────────────────────────────
def _cond_summary(cond):
    ctype = cond.get('type')
    if ctype == program_model.COND_DURATION:
        return f'{cond.get("ms")}ms'
    if ctype == program_model.COND_LIMIT_SWITCH:
        return f'limit {cond.get("station")}/{cond.get("switch") or "?"}'
    if ctype == program_model.COND_ENCODER:
        return f'enc {cond.get("station")}/{cond.get("motor") or "?"} >= {cond.get("counts")}'
    if ctype == program_model.COND_MOTORS_IDLE:
        return 'motors idle'
    return str(ctype)


def step_summary(step, program):
    parts = []
    for task in step.get('tasks', []):
        motors = ', '.join(
            f'{m.get("name")}({"fwd" if m.get("forward", True) else "rev"})'
            for m in task.get('motors', [])
        )
        speed = task.get('speed_us')
        suffix = f' @{speed}us' if speed else ''
        parts.append(f'{task.get("station") or "?"}:[{motors}]{suffix}')
    motion = ' + '.join(parts) if parts else 'timer only'

    completion = step.get('completion', {})
    conds = [_cond_summary(c) for c in completion.get('conditions', [])]
    joiner = f' {completion.get("mode", "any")} '
    until = joiner.join(conds) if conds else 'never'
    on_done = 'stop' if step.get('on_complete') == program_model.ON_COMPLETE_STOP else 'continue'
    return f'{motion}  ->  until {until}  ->  {on_done}'


# ── Shared accessors ─────────────────────────
def _station_motor_names(station):
    if not station:
        return []
    return [m['name'] for m in web_ui.get_saved_station(station).get('motors', [])]


def _station_limit_names(station):
    if not station:
        return []
    return [s['name'] for s in web_ui.get_saved_station(station).get('limit_switches', [])]


def _station_encoder_names(station):
    if not station:
        return []
    return [e['name'] for e in web_ui.get_saved_station(station).get('encoders', [])]


def _persist():
    web_ui.persist_state()


def _program_motor_refs(program):
    refs = set()
    for step in program.get('steps', []):
        for task in step.get('tasks', []):
            station = task.get('station')
            for m in task.get('motors', []):
                if station and m.get('name'):
                    refs.add((station, m['name']))
    return refs


def _connection_block(program):
    """Return a warning if any station the program drives isn't connected.

    This is the usual reason a program 'won't move' a motor even though the
    manual tester works - the tester runs on whatever board is open, while the
    program targets a station by name that may be on a disconnected port.
    """
    stations = {task.get('station') for (task, step) in _program_tasks(program)
                if task.get('station') and step.get('enabled', True)}
    missing = [s for s in sorted(stations) if web_ui.serials.get(s) is None]
    if missing:
        return ('Not connected: ' + ', '.join(missing) +
                '. Connect these boards on the Devices page before running.')
    return None


def _program_tasks(program):
    for step in program.get('steps', []):
        for task in step.get('tasks', []):
            yield task, step


def _step_connection_block(step):
    """Return a warning if a station this step drives isn't connected."""
    stations = {t.get('station') for t in step.get('tasks', []) if t.get('station')}
    missing = [s for s in sorted(stations) if web_ui.serials.get(s) is None]
    if missing:
        return ('Not connected: ' + ', '.join(missing) +
                '. Connect these boards on the Devices page before testing this step.')
    return None


def _homing_block(program):
    """Return a warning string if the program requires homing and isn't homed."""
    if not program.get('require_homing'):
        return None
    controller = web_ui.ensure_homing_controller()
    missing = [f'{s}/{m}' for (s, m) in sorted(_program_motor_refs(program))
               if not controller.is_homed(s, m)]
    if missing:
        return 'Homing required first. Not homed: ' + ', '.join(missing)
    return None


# ── Programs list + runner ───────────────────
@ui.page('/routines')
def programs_page():
    engine = web_ui.ensure_sequence_engine()
    content = web_ui.page_layout('routines')

    with content:
        with ui.column().classes('w-full gap-2'):
            ui.label('Programs').classes('h5 page-title')
            ui.label('Build motor sequences from steps, conditions, and triggers.').classes('muted text-sm')

        # Runner status
        with ui.card().classes('panel w-full'):
            ui.label('Runner').classes('eyebrow red')
            runner_state = ui.label('').classes('readout mono')

            def refresh_runner():
                runner_state.text = json.dumps({
                    'state': engine.state,
                    'program': engine.current_program,
                    'step': engine.current_step,
                    'step_name': engine.current_step_name,
                    'last_result': engine.last_result,
                }, indent=2)

            with ui.row().classes('items-center gap-2 flex-wrap'):
                web_ui.action_button('Refresh', on_click=refresh_runner, variant='neutral')
                web_ui.action_button('Stop', on_click=lambda: (engine.stop(), refresh_runner()), variant='danger')
            ui.timer(1.0, refresh_runner)
            refresh_runner()

        # Saved programs
        with ui.card().classes('panel w-full'):
            ui.label('Saved Programs').classes('eyebrow red')
            programs_box = ui.column().classes('w-full gap-2')

            def render_programs():
                programs_box.clear()
                programs = web_ui.get_routines()
                with programs_box:
                    if not programs:
                        ui.label('No programs yet. Create one below.').classes('muted text-sm')
                        return
                    for program in programs:
                        program_model_normalized = program_model.normalize_program(program)
                        program.clear()
                        program.update(program_model_normalized)
                        steps = program.get('steps', [])

                        def mk_run(target):
                            def do():
                                block = _connection_block(target) or _homing_block(target)
                                if block:
                                    ui.notify(block, type='warning')
                                    return
                                ok, message = engine.run(target)
                                ui.notify(message, type='positive' if ok else 'warning')
                                refresh_runner()
                            return do

                        def mk_edit(target):
                            return lambda: ui.navigate.to('/program/' + quote(target.get('name', '')))

                        def mk_duplicate(target):
                            def do():
                                base = target.get('name', 'Program')
                                new_name = f'{base} copy'
                                i = 2
                                while web_ui.get_routine(new_name):
                                    new_name = f'{base} copy {i}'
                                    i += 1
                                clone = program_model.normalize_program(target)
                                clone['name'] = new_name
                                web_ui.get_routines().append(clone)
                                _persist()
                                render_programs()
                                ui.notify(f'Duplicated to {new_name}', type='positive')
                            return do

                        def mk_delete(target):
                            def do():
                                web_ui.get_routines().remove(target)
                                _persist()
                                render_programs()
                            return do

                        with ui.row().classes('items-start gap-3 section-row w-full flex-wrap'):
                            with ui.column().classes('gap-1'):
                                ui.label(program.get('name', 'Unnamed')).classes('text-sm font-medium')
                                ui.label(
                                    f'{len(steps)} step(s) | repeat: {program.get("repeat", False)} '
                                    f'| global {program.get("global_speed_us")}us | trigger: {program.get("trigger", {}).get("type", "manual")}'
                                ).classes('mono muted')
                                for step in steps[:4]:
                                    ui.label(f'· {step.get("name", "step")}: {step_summary(step, program)}').classes('mono muted')
                                if len(steps) > 4:
                                    ui.label(f'+ {len(steps) - 4} more step(s)').classes('mono muted')
                            ui.element('div').classes('flex-grow')
                            web_ui.action_button('Run', on_click=mk_run(program), icon='play_arrow')
                            web_ui.action_button('Edit', on_click=mk_edit(program), variant='outline', icon='edit')
                            web_ui.action_button('Duplicate', on_click=mk_duplicate(program), variant='neutral')
                            web_ui.action_button('Delete', on_click=mk_delete(program), variant='danger')

            render_programs()

        # New program
        with ui.card().classes('panel w-full'):
            ui.label('New Program').classes('eyebrow red')
            with ui.row().classes('items-end gap-3 flex-wrap'):
                new_name = ui.input('Name', value='New Program').props('outlined').classes('w-56')

                def add_program():
                    name = (new_name.value or '').strip()
                    if not name:
                        ui.notify('Name required', type='warning')
                        return
                    if web_ui.get_routine(name):
                        ui.notify('A program with that name exists', type='warning')
                        return
                    program = program_model.new_program(name)
                    program['global_speed_us'] = web_ui.get_global_settings().get(
                        'global_speed_us', program_model.DEFAULT_GLOBAL_SPEED_US)
                    web_ui.get_routines().append(program)
                    _persist()
                    ui.navigate.to('/program/' + quote(name))

                web_ui.action_button('Create & Edit', on_click=add_program, icon='add')


# ── Program editor ───────────────────────────
@ui.page('/program/{name}')
def program_editor_page(name: str):
    web_ui.ensure_sequence_engine()
    content = web_ui.page_layout('routines')

    program = web_ui.get_routine(name)
    with content:
        if program is None:
            ui.label('Program not found.').classes('h5 red page-title')
            web_ui.action_button('Back to Programs', on_click=lambda: ui.navigate.to('/routines'), variant='neutral')
            return

        # Normalize once so every widget binds to a well-formed structure.
        normalized = program_model.normalize_program(program)
        program.clear()
        program.update(normalized)
        _persist()

        with ui.row().classes('items-center gap-3 flex-wrap'):
            ui.label(program.get('name')).classes('h5 red page-title')
            web_ui.action_button('Back', on_click=lambda: ui.navigate.to('/routines'), variant='neutral', icon='arrow_back')

        # ── Program settings + run ──
        with ui.card().classes('panel w-full'):
            ui.label('Program Settings').classes('eyebrow red')
            with ui.row().classes('items-end gap-3 flex-wrap'):
                rename_in = ui.input('Name', value=program.get('name')).props('outlined').classes('w-56')

                def do_rename():
                    new = (rename_in.value or '').strip()
                    if not new:
                        ui.notify('Name required', type='warning')
                        return
                    if new != program.get('name') and web_ui.get_routine(new):
                        ui.notify('A program with that name exists', type='warning')
                        return
                    program['name'] = new
                    _persist()
                    ui.navigate.to('/program/' + quote(new))

                web_ui.action_button('Rename', on_click=do_rename, variant='neutral')

                ui.number('Global speed (us/step)', min=1, max=5000, step=1) \
                    .props('outlined').classes('w-44') \
                    .bind_value(program, 'global_speed_us').on_value_change(lambda e: _persist())

                ui.switch('Repeat').bind_value(program, 'repeat').on_value_change(lambda e: _persist())

                ui.switch('Require homing').bind_value(program, 'require_homing').on_value_change(lambda e: _persist())

            with ui.row().classes('items-end gap-3 flex-wrap'):
                trigger = program.setdefault('trigger', {'type': 'manual'})
                trig_sel = ui.select(
                    options={'manual': 'Manual', 'timed': 'Timed (coming soon)', 'on_event': 'On event (coming soon)'},
                    value=trigger.get('type', 'manual'), label='Trigger',
                ).props('outlined').classes('w-56')

                def on_trigger(e):
                    program['trigger'] = program_model.normalize_trigger({'type': e.value})
                    _persist()
                trig_sel.on_value_change(on_trigger)
                ui.label('Only manual triggers execute today; timed/event are reserved.').classes('muted text-xs')

            validation_box = ui.column().classes('w-full gap-1')

            def refresh_validation():
                validation_box.clear()
                station_motors = {s: _station_motor_names(s) for s in web_ui.STATION_ORDER}
                hw = web_ui.station_hw_provider()
                issues = program_model.validate_program(program, station_motors, hw)
                with validation_box:
                    if not issues:
                        ui.html('<div class="alert alert-success">Program looks runnable.</div>')
                    else:
                        for issue in issues:
                            ui.html(f'<div class="alert alert-warning">{issue}</div>')

            with ui.row().classes('items-center gap-2 flex-wrap'):
                def run_now():
                    block = _connection_block(program) or _homing_block(program)
                    if block:
                        ui.notify(block, type='warning')
                        return
                    engine = web_ui.ensure_sequence_engine()
                    ok, message = engine.run(program)
                    ui.notify(message, type='positive' if ok else 'warning')

                web_ui.action_button('Run', on_click=run_now, icon='play_arrow')
                web_ui.action_button('Stop', on_click=lambda: web_ui.ensure_sequence_engine().stop(), variant='danger')
                web_ui.action_button('Validate', on_click=refresh_validation, variant='neutral')
            refresh_validation()

            run_status = ui.label('').classes('mono muted text-xs')

            def refresh_run_status():
                eng = web_ui.ensure_sequence_engine()
                parts = [f'runner: {eng.state}']
                if eng.current_step_name:
                    parts.append(f'step: {eng.current_step_name}')
                last = eng.last_result
                if isinstance(last, dict) and last.get('status') not in (None, 'done'):
                    detail = last.get('error') or last.get('status')
                    parts.append(f'last: {detail}')
                run_status.text = '  |  '.join(parts)
            ui.timer(1.0, refresh_run_status)
            refresh_run_status()

        # ── Steps ──
        with ui.card().classes('panel w-full'):
            with ui.row().classes('items-center gap-2 w-full flex-wrap'):
                ui.label('Steps').classes('eyebrow red')
                ui.element('div').classes('flex-grow')

                def add_step():
                    program['steps'].append(program_model.new_step(f'Step {len(program["steps"]) + 1}'))
                    _persist()
                    render_steps()

                web_ui.action_button('Add Step', on_click=add_step, icon='add')

            steps_container = ui.column().classes('w-full gap-2')

            def render_steps():
                steps_container.clear()
                with steps_container:
                    if not program['steps']:
                        ui.label('No steps yet. Add one above.').classes('muted text-sm')
                    for idx, step in enumerate(program['steps']):
                        _render_step_card(program, idx, step, render_steps, refresh_validation)

            render_steps()


def _render_step_card(program, idx, step, rerender_steps, refresh_validation):
    steps = program['steps']
    step.setdefault('enabled', True)
    with ui.card().classes('panel w-full' + ('' if step.get('enabled', True) else ' step-disabled')):
        with ui.row().classes('items-center gap-2 w-full flex-wrap'):
            def on_enabled(e):
                step['enabled'] = e.value
                web_ui.persist_state()
                rerender_steps()
            ui.switch(value=step.get('enabled', True)) \
                .on_value_change(on_enabled).tooltip('Enable/disable this step in the full run')

            ui.input('Step name').props('outlined dense').classes('w-48') \
                .bind_value(step, 'name').on_value_change(lambda e: web_ui.persist_state())
            ui.label(step_summary(step, program)).classes('mono muted')
            if not step.get('enabled', True):
                ui.label('(disabled)').classes('mono muted')
            ui.element('div').classes('flex-grow')

            def run_step_now():
                block = _step_connection_block(step)
                if block:
                    ui.notify(block, type='warning')
                    return
                engine = web_ui.ensure_sequence_engine()
                ok, message = engine.run_step(program, idx)
                ui.notify(message, type='positive' if ok else 'warning')

            web_ui.action_button('Run Step', on_click=run_step_now, icon='play_arrow', variant='outline')

            def move_up():
                if idx > 0:
                    steps[idx - 1], steps[idx] = steps[idx], steps[idx - 1]
                    web_ui.persist_state()
                    rerender_steps()

            def move_down():
                if idx < len(steps) - 1:
                    steps[idx + 1], steps[idx] = steps[idx], steps[idx + 1]
                    web_ui.persist_state()
                    rerender_steps()

            def delete_step():
                steps.pop(idx)
                web_ui.persist_state()
                rerender_steps()
                refresh_validation()

            web_ui.action_button('Up', on_click=move_up, variant='neutral', icon='arrow_upward')
            web_ui.action_button('Down', on_click=move_down, variant='neutral', icon='arrow_downward')
            web_ui.action_button('Delete', on_click=delete_step, variant='danger', icon='delete')

        # Tasks
        ui.label('Tasks (start together)').classes('eyebrow red')
        tasks_box = ui.column().classes('w-full gap-2')

        def render_tasks():
            tasks_box.clear()
            with tasks_box:
                if not step['tasks']:
                    ui.label('No tasks - this is a timer/condition-only step.').classes('muted text-sm')
                for ti, task in enumerate(step['tasks']):
                    _render_task_row(program, step, task, ti, render_tasks)

        render_tasks()

        def add_task():
            step['tasks'].append(program_model.new_task())
            web_ui.persist_state()
            render_tasks()

        web_ui.action_button('Add Task', on_click=add_task, variant='neutral', icon='add')

        # Completion
        ui.separator().classes('divider')
        completion = step.setdefault('completion', program_model.new_completion())
        with ui.row().classes('items-center gap-3 flex-wrap'):
            ui.label('Completion').classes('eyebrow red')
            ui.toggle({'any': 'Any (whichever first)', 'all': 'All'}, value=completion.get('mode', 'any')) \
                .bind_value(completion, 'mode').on_value_change(lambda e: web_ui.persist_state())
        ui.label('How long this step runs = when its conditions are met. '
                 'Add a Duration (ms) to run for a fixed time; use Limit switch / Encoder / '
                 'Motors idle to end on an event. "Any" ends at the first met condition, '
                 '"All" waits for every one.').classes('muted text-xs')

        conds_box = ui.column().classes('w-full gap-2')

        def render_conditions():
            conds_box.clear()
            with conds_box:
                if not completion['conditions']:
                    ui.label('No conditions - step would never end. Add one.').classes('muted text-sm')
                for ci, cond in enumerate(completion['conditions']):
                    _render_condition_row(completion, cond, ci, render_conditions)

        render_conditions()

        def add_condition():
            completion['conditions'].append(program_model.new_condition(program_model.COND_DURATION))
            web_ui.persist_state()
            render_conditions()

        with ui.row().classes('items-center gap-2 flex-wrap'):
            web_ui.action_button('Add Condition', on_click=add_condition, variant='neutral', icon='add')
            ui.toggle(
                {program_model.ON_COMPLETE_STOP: 'Stop motors', program_model.ON_COMPLETE_CONTINUE: 'Leave running'},
                value=step.get('on_complete', program_model.ON_COMPLETE_STOP),
            ).bind_value(step, 'on_complete').on_value_change(lambda e: web_ui.persist_state())


def _render_task_row(program, step, task, ti, rerender_tasks):
    with ui.column().classes('w-full gap-2 section-row'):
        with ui.row().classes('items-end gap-2 flex-wrap'):
            station_sel = ui.select(
                options=list(web_ui.STATION_ORDER), value=task.get('station') or None, label='Station',
            ).props('outlined').classes('w-32')

            def on_station(e):
                task['station'] = e.value
                task['motors'] = []
                web_ui.persist_state()
                rerender_tasks()
            station_sel.on_value_change(on_station)

            use_global = ui.checkbox('Global speed', value=task.get('speed_us') is None)
            speed_num = ui.number('us/step', min=1, max=5000, step=1,
                                  value=task.get('speed_us') or program.get('global_speed_us')) \
                .props('outlined').classes('w-28')

            def apply_speed(_=None):
                if use_global.value:
                    task['speed_us'] = None
                else:
                    task['speed_us'] = int(speed_num.value or program.get('global_speed_us'))
                web_ui.persist_state()
            use_global.on_value_change(apply_speed)
            speed_num.on_value_change(apply_speed)

            ui.element('div').classes('flex-grow')

            def remove_task():
                step['tasks'].pop(ti)
                web_ui.persist_state()
                rerender_tasks()

            web_ui.action_button('Remove Task', on_click=remove_task, variant='danger', icon='delete')

        # Motor selection for the chosen station.
        names = _station_motor_names(task.get('station'))
        selected = {m.get('name'): m for m in task.get('motors', [])}
        if not task.get('station'):
            ui.label('Pick a station to choose motors.').classes('muted text-xs')
        elif not names:
            ui.label('No motors configured on this station yet (add them on the station page).').classes('muted text-xs')
        else:
            widgets = []
            with ui.column().classes('w-full gap-1'):
                for mn in names:
                    with ui.row().classes('items-center gap-2'):
                        cb = ui.checkbox(mn, value=mn in selected)
                        direction = ui.toggle(
                            {'fwd': 'Fwd', 'rev': 'Rev'},
                            value='fwd' if selected.get(mn, {}).get('forward', True) else 'rev',
                        )
                        spd = ui.number(
                            'us/step', value=selected.get(mn, {}).get('speed_us'),
                            min=1, max=5000, step=1,
                        ).props('outlined dense placeholder="task"').classes('w-28') \
                            .tooltip('Per-motor speed. Blank = use the task/global speed.')
                        widgets.append((mn, cb, direction, spd))
            ui.label('Leave a motor\'s us/step blank to inherit the task speed. '
                     'Set it to run that motor at its own rate.').classes('muted text-xs')

            def rebuild_motors(_=None):
                motors = []
                for mn, cb, direction, spd in widgets:
                    if cb.value:
                        ref = {'name': mn, 'forward': direction.value == 'fwd'}
                        if spd.value:
                            ref['speed_us'] = int(spd.value)
                        motors.append(ref)
                task['motors'] = motors
                web_ui.persist_state()

            for _, cb, direction, spd in widgets:
                cb.on_value_change(rebuild_motors)
                direction.on_value_change(rebuild_motors)
                spd.on_value_change(rebuild_motors)


def _render_condition_row(completion, cond, ci, rerender_conditions):
    with ui.row().classes('items-end gap-2 section-row w-full flex-wrap'):
        type_sel = ui.select(
            options={
                program_model.COND_DURATION: 'Duration',
                program_model.COND_LIMIT_SWITCH: 'Limit switch',
                program_model.COND_ENCODER: 'Encoder count',
                program_model.COND_MOTORS_IDLE: 'Motors idle',
            },
            value=cond.get('type'), label='Type',
        ).props('outlined').classes('w-36')

        def on_type(e):
            new = program_model.new_condition(e.value)
            cond.clear()
            cond.update(new)
            web_ui.persist_state()
            rerender_conditions()
        type_sel.on_value_change(on_type)

        ctype = cond.get('type')
        if ctype == program_model.COND_DURATION:
            ui.number('ms', min=0, max=600000, step=50).props('outlined').classes('w-28') \
                .bind_value(cond, 'ms').on_value_change(lambda e: web_ui.persist_state())

        elif ctype == program_model.COND_LIMIT_SWITCH:
            st_sel = ui.select(options=list(web_ui.STATION_ORDER), value=cond.get('station') or None, label='Station') \
                .props('outlined').classes('w-32')

            def on_cond_station(e):
                cond['station'] = e.value
                cond['switch'] = ''
                web_ui.persist_state()
                rerender_conditions()
            st_sel.on_value_change(on_cond_station)

            sw_options = _station_limit_names(cond.get('station'))
            sw_sel = ui.select(options=sw_options, value=cond.get('switch') or None, label='Switch') \
                .props('outlined').classes('w-32')

            def on_switch(e):
                cond['switch'] = e.value or ''
                web_ui.persist_state()
            sw_sel.on_value_change(on_switch)

        elif ctype == program_model.COND_ENCODER:
            st_sel = ui.select(options=list(web_ui.STATION_ORDER), value=cond.get('station') or None, label='Station') \
                .props('outlined').classes('w-32')

            def on_enc_station(e):
                cond['station'] = e.value
                cond['motor'] = ''
                web_ui.persist_state()
                rerender_conditions()
            st_sel.on_value_change(on_enc_station)

            enc_options = _station_encoder_names(cond.get('station'))
            enc_sel = ui.select(options=enc_options, value=cond.get('motor') or None, label='Encoder') \
                .props('outlined').classes('w-32')

            def on_encoder(e):
                cond['motor'] = e.value or ''
                web_ui.persist_state()
            enc_sel.on_value_change(on_encoder)

            ui.number('counts', min=1, max=1000000, step=1).props('outlined').classes('w-28') \
                .bind_value(cond, 'counts').on_value_change(lambda e: web_ui.persist_state())

        elif ctype == program_model.COND_MOTORS_IDLE:
            ui.label('Waits for the step\'s motors to finish.').classes('muted text-xs')

        ui.element('div').classes('flex-grow')

        def remove_condition():
            completion['conditions'].pop(ci)
            web_ui.persist_state()
            rerender_conditions()

        web_ui.action_button('Remove', on_click=remove_condition, variant='danger', icon='delete')


# ── Settings ─────────────────────────────────
@ui.page('/settings')
def settings_page():
    content = web_ui.page_layout('settings')
    with content:
        with ui.column().classes('w-full gap-2'):
            ui.label('Settings').classes('h5 page-title')
            ui.label('Shared defaults applied across programs.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            ui.label('Global Speed').classes('eyebrow red')
            settings = web_ui.get_global_settings()
            ui.number('Default speed (us/step)', min=1, max=5000, step=1) \
                .props('outlined').classes('w-56') \
                .bind_value(settings, 'global_speed_us').on_value_change(lambda e: web_ui.persist_state())
            ui.label('New programs start at this speed; each task can override it or inherit "Global speed".') \
                .classes('muted text-xs')
