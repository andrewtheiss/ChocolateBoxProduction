from nicegui import ui
import json
import logging
from src.comms import (
    scan_ports, connect_serial, identify, get_status,
    start_station, stop_station, send_command,
    add_motor, remove_motor, list_motors, run_motor, run_motor_group,
    stop_motor, verify_pin, set_station_id,
    add_limit, remove_limit, list_limits, clear_limit,
    add_encoder, remove_encoder, list_encoders,
    save_config, clear_config,
)
from src.firmware import available_firmware_options, flash_firmware
from src.routine_engine import SequenceEngine
from src.homing import HomingController
from src.state_machine import PipelineCoordinator
from src.web_state import clone_station_motors, load_web_state, save_web_state
from src import program_model

# ── State ────────────────────────────────────
serials: dict = {}
coordinator: PipelineCoordinator = None
station_configs: dict = {}
fsm_config: dict = {}
persisted_state: dict = {}
sequence_engine: SequenceEngine = None
homing_controller: HomingController = None
log_lines: list = []
STATION_ORDER = ['dispenser', 'roller', 'punch', 'crease']
MOTOR_PIN_PRESETS = [
    {'name': 'Motor 1', 'pul': 9, 'dir': 8, 'ena': 7},
    {'name': 'Motor 2', 'pul': 12, 'dir': 11, 'ena': 10},
    {'name': 'Motor 3', 'pul': 6, 'dir': 5, 'ena': 4},
]
station_motor_cache: dict = {}

CSS = '''
:root {
    color-scheme: dark;
    --cb-bg: #080909;
    --cb-bg-soft: #0d0f0f;
    --cb-surface: #111414;
    --cb-surface-2: #171b1b;
    --cb-surface-3: #202626;
    --cb-elevated: #151919;
    --cb-border: #303737;
    --cb-border-strong: #465050;
    --cb-text: #f2efe8;
    --cb-text-soft: #c9c5bc;
    --cb-muted: #8f958f;
    --cb-red: #c86f4a;
    --cb-red-dark: #9f5134;
    --cb-blue: #8aa4a8;
    --cb-green: #86a17c;
    --cb-gold: #c5a15a;
    --cb-danger: #b85c52;
    --cb-radius: 0;
    --cb-radius-sm: 0;
    --cb-shadow: 0 18px 48px rgba(0,0,0,.38);
    --cb-shadow-sm: 0 10px 24px rgba(0,0,0,.24);
    --cb-focus: 0 0 0 2px rgba(200,111,74,.34);
    --cb-font-sans: Inter, "Source Sans 3", "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    --cb-font-mono: "SF Mono", "Fira Code", Consolas, monospace;
}

*, *:before, *:after {
    box-sizing: border-box;
    border-radius: 0 !important;
}

html,
body,
#app,
.nicegui-content,
.q-layout,
.q-page-container,
.q-page {
    min-height: 100%;
    background: linear-gradient(180deg, #0d0f0f 0%, var(--cb-bg) 42%, #060707 100%) !important;
    color: var(--cb-text) !important;
    font-family: var(--cb-font-sans);
}

body {
    margin: 0;
    padding: 0;
    line-height: 1.5;
}

a {
    color: var(--cb-blue);
}

.container {
    width: min(100%, 1220px);
    margin: 0 auto;
    padding: 24px 28px;
    background: transparent;
}

.page {
    padding-block: 26px 48px;
}

.content-grid {
    display: grid;
    gap: 16px;
}

.h5,
.page-title {
    margin: 0;
    color: var(--cb-text) !important;
    font-size: clamp(1.45rem, 2vw, 2rem);
    font-weight: 800;
    letter-spacing: -.04em;
    line-height: 1.08;
    text-transform: none !important;
}

.eyebrow {
    color: var(--cb-muted) !important;
    font-size: .72rem;
    font-weight: 800;
    letter-spacing: .12em;
    line-height: 1;
    text-transform: uppercase;
}

.red {
    color: var(--cb-red) !important;
}

.muted {
    color: var(--cb-muted) !important;
}

.mono {
    color: var(--cb-text-soft);
    font-family: var(--cb-font-mono);
    font-size: 12px;
    letter-spacing: -.01em;
}

.flex-grow {
    min-width: 12px;
}

.divider,
.q-separator {
    background: var(--cb-border) !important;
    border: 0 !important;
    height: 1px !important;
    margin: 14px 0 !important;
}

.q-drawer {
    background: #0d0f0f !important;
    border-right: 1px solid var(--cb-border) !important;
    color: var(--cb-text) !important;
}

.q-header {
    min-height: 56px;
    background: rgba(13,15,15,.94) !important;
    color: var(--cb-text) !important;
    border-bottom: 1px solid var(--cb-border) !important;
    box-shadow: none !important;
    backdrop-filter: blur(10px);
}

.app-shell__title {
    color: var(--cb-text);
    font-size: .98rem;
    font-weight: 800;
    letter-spacing: -.02em;
}

.nav-item {
    display: flex !important;
    align-items: center !important;
    min-height: 40px !important;
    height: 40px !important;
    margin: 0;
    padding: 0 10px !important;
    color: var(--cb-text-soft) !important;
    border: 1px solid transparent;
    border-radius: var(--cb-radius-sm);
    transition: background .16s ease, border-color .16s ease, color .16s ease;
}

.nav-item .q-focus-helper {
    display: none !important;
}

.nav-item .q-item__section {
    min-height: 0 !important;
    padding: 0 !important;
    line-height: 1 !important;
}

.nav-item .q-item__section--avatar {
    align-items: center !important;
    justify-content: center !important;
    min-width: 24px !important;
    width: 24px !important;
    margin-right: 8px !important;
}

.nav-item .q-item__section--main {
    justify-content: center !important;
}

.nav-item .q-item__label,
.nav-item .q-icon {
    color: inherit !important;
    line-height: 1 !important;
}

.nav-item .q-item__label {
    display: flex !important;
    align-items: center !important;
    height: 16px !important;
    font-size: .88rem !important;
    transform: translateY(0) !important;
}

.nav-item .q-icon {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 18px !important;
    height: 18px !important;
    min-height: 18px !important;
    font-size: 18px !important;
}

.nav-item.active,
.nav-item:hover {
    color: var(--cb-text) !important;
    background: #171b1b !important;
    border-color: var(--cb-border);
}

.nav-item.active {
    box-shadow: inset 3px 0 0 var(--cb-red);
}

.panel,
.q-card.panel {
    width: 100%;
    padding: 20px !important;
    color: var(--cb-text) !important;
    background: var(--cb-surface) !important;
    border: 1px solid var(--cb-border) !important;
    border-radius: var(--cb-radius) !important;
    box-shadow: var(--cb-shadow-sm) !important;
}

.panel > .q-card__section {
    padding: 0 !important;
}

.section-row {
    min-height: 48px;
    padding: 12px 0;
    border-bottom: 1px solid rgba(242,239,232,.08);
}

.section-row:last-child {
    border-bottom: 0;
}

.readout,
.nicegui-log {
    width: 100%;
    color: var(--cb-text-soft) !important;
    background: #0a0c0c !important;
    border: 1px solid var(--cb-border) !important;
    border-radius: var(--cb-radius-sm) !important;
    box-shadow: inset 0 1px 0 rgba(242,239,232,.03) !important;
}

.readout {
    display: block;
    min-height: 58px;
    max-height: 320px;
    padding: 12px 13px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
}

.nicegui-log {
    padding: 10px !important;
}

.btn,
.widget-action-button,
.q-btn.btn {
    height: 40px !important;
    min-height: 40px !important;
    max-height: 40px !important;
    padding: 0 14px !important;
    color: white !important;
    background: var(--cb-red-dark) !important;
    border: 1px solid #c86f4a !important;
    border-radius: var(--cb-radius-sm) !important;
    box-shadow: none !important;
    font-size: .82rem !important;
    font-weight: 800 !important;
    letter-spacing: .01em;
    line-height: 1 !important;
    text-transform: uppercase;
    text-decoration: none;
    transition: border-color .12s ease, background .12s ease, color .12s ease;
}

.q-btn.btn .q-btn__content {
    min-height: 0 !important;
    line-height: 1 !important;
}

.q-btn.btn .q-icon {
    font-size: 18px !important;
    line-height: 1 !important;
    margin-top: 0 !important;
}

.q-btn,
.q-btn:before,
.q-btn:after,
.q-focus-helper,
.q-ripple,
.q-ripple__inner {
    border-radius: 0 !important;
}

.btn:hover,
.widget-action-button:hover,
.q-btn.btn:hover {
    background: var(--cb-red) !important;
    filter: none;
    transform: none;
}

.btn:disabled,
.q-btn.disabled,
.q-btn[disabled] {
    opacity: .48 !important;
    transform: none !important;
    filter: none !important;
    cursor: not-allowed !important;
}

.btn-sm {
    height: 40px !important;
    min-height: 40px !important;
    max-height: 40px !important;
}

.btn-neutral {
    color: var(--cb-text) !important;
    background: var(--cb-surface-3) !important;
    border-color: var(--cb-border-strong) !important;
    box-shadow: none !important;
}

.btn-outline {
    color: var(--cb-red) !important;
    background: transparent !important;
    border-color: var(--cb-red) !important;
    box-shadow: none !important;
}

.btn-danger {
    color: #fff !important;
    background: #7f2f2b !important;
    border-color: var(--cb-danger) !important;
    box-shadow: none !important;
}

.tag,
.badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 22px;
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: 0;
    font-size: .68rem;
    font-weight: 900;
    letter-spacing: .04em;
    line-height: 1;
    text-transform: uppercase;
}

.tag-on,
.badge-success {
    color: #d9ead4;
    background: rgba(134,161,124,.13);
    border-color: rgba(134,161,124,.42);
}

.tag-off,
.badge-neutral {
    color: var(--cb-muted);
    background: rgba(129,144,167,.12);
    border-color: rgba(129,144,167,.22);
}

.tag-run,
.badge-primary {
    color: #dbe6e8;
    background: rgba(138,164,168,.14);
    border-color: rgba(138,164,168,.42);
}

.tag-err,
.badge-danger {
    color: #f2d5d2;
    background: rgba(184,92,82,.14);
    border-color: rgba(184,92,82,.42);
}

.tag-idle,
.badge-accent {
    color: #eadcbf;
    background: rgba(197,161,90,.13);
    border-color: rgba(197,161,90,.4);
}

.q-field {
    --field-radius: var(--cb-radius-sm);
    min-height: 0 !important;
    padding: 0 !important;
}

.q-field--labeled {
    padding-top: 16px !important;
}

.q-field,
.q-select,
.q-input {
    color: var(--cb-text) !important;
}

.q-field__inner {
    min-height: 0 !important;
    padding: 0 !important;
}

.q-field__bottom {
    display: none !important;
    min-height: 0 !important;
    padding: 0 !important;
}

.q-field__control {
    height: 40px !important;
    min-height: 40px !important;
    max-height: 40px !important;
    color: var(--cb-text) !important;
    background: #0a0c0c !important;
    border-radius: var(--field-radius) !important;
    padding: 0 10px !important;
}

.q-field--dense .q-field__control {
    height: 40px !important;
    min-height: 40px !important;
    max-height: 40px !important;
}

.q-field__control-container,
.q-field__native,
.q-field__input {
    height: 38px !important;
    min-height: 38px !important;
    max-height: 38px !important;
    padding: 0 !important;
    line-height: 38px !important;
}

.q-field__append,
.q-field__prepend,
.q-field__marginal {
    height: 38px !important;
    min-height: 38px !important;
    max-height: 38px !important;
    padding: 0 !important;
    line-height: 38px !important;
}

.q-field__append .q-icon,
.q-field__prepend .q-icon,
.q-field__marginal .q-icon {
    font-size: 18px !important;
    line-height: 1 !important;
}

.q-field--outlined .q-field__control:before {
    border: 1px solid var(--cb-border-strong) !important;
    border-radius: var(--field-radius) !important;
}

.q-field--outlined .q-field__control:after,
.q-field--focused .q-field__control:before,
.q-field--focused .q-field__control:after {
    border: 1px solid var(--cb-red) !important;
    border-radius: var(--field-radius) !important;
    box-shadow: var(--cb-focus);
}

.q-field__native,
.q-field__input,
.q-field__prefix,
.q-field__suffix,
.q-field__marginal,
.q-field__label {
    color: var(--cb-text-soft) !important;
}

.q-field__label {
    top: -16px !important;
    left: 0 !important;
    height: 12px !important;
    max-width: 100% !important;
    overflow: hidden;
    color: var(--cb-muted) !important;
    font-size: 11px !important;
    font-weight: 800 !important;
    letter-spacing: .08em !important;
    line-height: 12px !important;
    text-overflow: ellipsis;
    text-transform: uppercase;
    transform: none !important;
    transform-origin: left center !important;
}

.q-field--focused .q-field__label,
.q-field--float .q-field__label {
    color: var(--cb-red) !important;
    font-weight: 800;
}

.q-field--labeled .q-field__native,
.q-field--labeled .q-field__input {
    padding-top: 0 !important;
}

.q-field__native::placeholder,
.q-field__input::placeholder {
    color: rgba(184,196,216,.5) !important;
}

.q-menu,
.q-dialog__inner > div,
.q-card {
    color: var(--cb-text) !important;
    background: var(--cb-surface-2) !important;
    border: 1px solid var(--cb-border) !important;
    border-radius: var(--cb-radius) !important;
    box-shadow: var(--cb-shadow) !important;
}

.q-list,
.q-item {
    color: var(--cb-text) !important;
    background: transparent !important;
}

.q-item:hover {
    background: rgba(242,239,232,.055) !important;
}

.q-toggle__inner,
.q-checkbox__inner,
.q-radio__inner {
    color: var(--cb-red) !important;
}

.q-toggle__track {
    opacity: 1 !important;
    background: var(--cb-border-strong) !important;
}

.q-toggle__inner--truthy .q-toggle__track {
    background: rgba(200,111,74,.48) !important;
}

.q-toggle__thumb,
.q-toggle__track,
.q-checkbox__bg,
.q-radio__bg {
    border-radius: 0 !important;
}

.q-btn-toggle,
.q-btn-group {
    background: #0a0c0c !important;
    border: 1px solid var(--cb-border-strong) !important;
    border-radius: var(--cb-radius-sm) !important;
    overflow: hidden;
}

.q-btn-toggle .q-btn {
    color: var(--cb-text-soft) !important;
    background: transparent !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}

.q-btn-toggle .q-btn.bg-primary,
.q-btn-toggle .q-btn[aria-pressed="true"] {
    color: #fff !important;
    background: var(--cb-red-dark) !important;
}

.q-notification {
    color: var(--cb-text) !important;
    background: var(--cb-surface-2) !important;
    border: 1px solid var(--cb-border) !important;
    border-radius: var(--cb-radius) !important;
    box-shadow: var(--cb-shadow-sm) !important;
}

.pin-pul .q-field__label {
    color: var(--cb-blue) !important;
}

.pin-dir .q-field__label {
    color: var(--cb-danger) !important;
}

.pin-ena .q-field__label {
    color: var(--cb-green) !important;
}

.style-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
}

.style-swatch {
    min-height: 88px;
    padding: 14px;
    border: 1px solid var(--cb-border);
    border-radius: var(--cb-radius-sm);
    background: var(--cb-surface-2);
}

.style-token {
    width: 100%;
    height: 42px;
    margin-bottom: 10px;
    border: 1px solid rgba(255,255,255,.12);
    border-radius: var(--cb-radius-sm);
}

.alert {
    padding: 12px 13px;
    color: var(--cb-text-soft);
    background: #0a0c0c;
    border: 1px solid var(--cb-border);
    border-left-width: 4px;
    border-radius: var(--cb-radius-sm);
}

.alert-info {
    border-left-color: var(--cb-blue);
}

.alert-success {
    border-left-color: var(--cb-green);
}

.alert-warning {
    border-left-color: var(--cb-gold);
}

.alert-danger {
    border-left-color: var(--cb-danger);
}

.table {
    width: 100%;
    overflow: hidden;
    color: var(--cb-text);
    background: var(--cb-surface-2);
    border: 1px solid var(--cb-border);
    border-collapse: collapse;
    border-radius: var(--cb-radius-sm);
}

.table th,
.table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--cb-border);
}

.table th {
    color: var(--cb-muted);
    font-size: .75rem;
    letter-spacing: .08em;
    text-align: left;
    text-transform: uppercase;
}

@media (max-width: 900px) {
    .container {
        padding: 18px;
    }

    .q-drawer {
        width: 230px !important;
    }
}

@media (max-width: 640px) {
    .container {
        padding: 14px;
    }

    .panel,
    .q-card.panel {
        padding: 16px !important;
    }

    .section-row {
        align-items: flex-start !important;
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
    return ui.button(label, on_click=on_click, icon=icon).props('unelevated no-caps dense').classes(' '.join(classes))


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


def station_hw_provider():
    """Provide each station's limit-switch / encoder config to the engine."""
    result = {}
    for name in STATION_ORDER:
        station = get_saved_station(name)
        result[name] = {
            'limit_switches': station.get('limit_switches', []),
            'encoders': station.get('encoders', []),
        }
    return result


def ensure_sequence_engine():
    global sequence_engine
    if sequence_engine is None:
        sequence_engine = SequenceEngine(lambda: serials, station_hw_provider, add_log)
    return sequence_engine


def homing_config_provider():
    """Snapshot of homing-relevant config for the HomingController."""
    return {
        'global_speed_us': get_global_settings().get('global_speed_us', program_model.DEFAULT_GLOBAL_SPEED_US),
        'stations': {
            name: {
                'motors': [m['name'] for m in get_saved_station(name).get('motors', [])],
                'home_config': get_saved_station(name).get('home_config', {}),
            }
            for name in STATION_ORDER
        },
    }


def ensure_homing_controller():
    global homing_controller
    if homing_controller is None:
        homing_controller = HomingController(lambda: serials, homing_config_provider, add_log)
    return homing_controller


def get_global_settings():
    settings = persisted_state.setdefault('global_settings', {})
    settings.setdefault('global_speed_us', program_model.DEFAULT_GLOBAL_SPEED_US)
    return settings


def get_saved_station(name):
    stations = persisted_state.setdefault('stations', {})
    station = stations.setdefault(name, {
        'port_hint': None,
        'firmware': None,
        'motors': [],
        'limits': [],
        'triggers': [],
        'limit_switches': [],
        'encoders': [],
        'home_config': {},
        'verification': {'motors': {}, 'limit_switches': {}, 'encoders': {}},
    })
    station.setdefault('port_hint', None)
    station.setdefault('firmware', None)
    station.setdefault('motors', [])
    station.setdefault('limits', [])
    station.setdefault('triggers', [])
    station.setdefault('limit_switches', [])
    station.setdefault('encoders', [])
    station.setdefault('home_config', {})
    station.setdefault('verification', {'motors': {}, 'limit_switches': {}, 'encoders': {}})
    return station


def get_station_verification(name):
    verification = get_saved_station(name).setdefault(
        'verification', {'motors': {}, 'limit_switches': {}, 'encoders': {}})
    verification.setdefault('motors', {})
    verification.setdefault('limit_switches', {})
    verification.setdefault('encoders', {})
    return verification


def set_device_verified(name, kind, device, value):
    """kind in {'motors','limit_switches','encoders'}."""
    verification = get_station_verification(name)
    verification.setdefault(kind, {})[device] = bool(value)
    persist_state()


def is_motor_verified(name, motor):
    return bool(get_station_verification(name)['motors'].get(motor))


def motor_config_report(name):
    """Compare the board's live motor wiring to the saved config.

    Returns {'connected': bool, 'motors': [{name, saved, live, status}]} where
    status is 'match' | 'mismatch' | 'missing_on_board' | 'extra_on_board' | 'offline'.
    """
    saved_motors = get_saved_station(name).get('motors', [])
    saved_by_name = {m['name']: m for m in saved_motors}
    ser = serials.get(name)

    if ser is None:
        return {
            'connected': False,
            'motors': [{'name': m['name'], 'saved': m, 'live': None, 'status': 'offline'} for m in saved_motors],
        }

    live = list_motors(ser)
    live_motors = live.get('motors', []) if (live and live.get('status') == 'ok') else []
    live_by_name = {m['name']: m for m in live_motors}

    def pins_match(a, b):
        return (int(a['pul_pin']) == int(b['pul_pin'])
                and int(a['dir_pin']) == int(b['dir_pin'])
                and int(a['ena_pin']) == int(b['ena_pin'])
                and bool(a.get('reversed', False)) == bool(b.get('reversed', False)))

    rows = []
    for m in saved_motors:
        live_m = live_by_name.get(m['name'])
        if live_m is None:
            status = 'missing_on_board'
        elif pins_match(m, live_m):
            status = 'match'
        else:
            status = 'mismatch'
        rows.append({'name': m['name'], 'saved': m, 'live': live_m, 'status': status})

    for m in live_motors:
        if m['name'] not in saved_by_name:
            rows.append({'name': m['name'], 'saved': None, 'live': m, 'status': 'extra_on_board'})

    return {'connected': True, 'motors': rows}


def get_routines():
    return persisted_state.setdefault('routines', [])


def get_routine(name):
    for routine in get_routines():
        if routine.get('name') == name:
            return routine
    return None


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


# ── Layout with sidebar ─────────────────────
def page_layout(active='dashboard'):
    theme()

    with ui.left_drawer(value=True, fixed=True).classes('p-0').props('width=220 bordered') as drawer:
        with ui.column().classes('w-full gap-1 p-3'):
            ui.label('Navigation').classes('eyebrow red')

            items = [
                ('Dashboard', 'grid_view', '/dashboard', 'dashboard'),
                ('Devices', 'usb', '/devices', 'devices'),
                ('Verify Setup', 'fact_check', '/verify', 'verify'),
                ('Startup / Homing', 'home', '/homing', 'homing'),
                ('Programs', 'playlist_play', '/routines', 'routines'),
                ('Settings', 'tune', '/settings', 'settings'),
                ('Style Guide', 'palette', '/styleguide', 'styleguide'),
            ]
            for label, icon, url, key in items:
                with ui.item(on_click=lambda u=url: ui.navigate.to(u)).classes(
                    'nav-item' + (' active' if active == key else '')
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
        action_button('', on_click=drawer.toggle, icon='menu', variant='neutral')
        ui.label('ChocolateBox Production').classes('app-shell__title ml-2')

    return ui.column().classes('container page content-grid')


# ── Style guide ───────────────────────────────
@ui.page('/styleguide')
def styleguide_page():
    content = page_layout('styleguide')

    with content:
        with ui.column().classes('w-full gap-2'):
            ui.label('Style Guide').classes('h5 page-title')
            ui.label('Dark production UI tokens and component examples.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            ui.label('Color Tokens').classes('eyebrow red')
            with ui.element('div').classes('style-grid'):
                tokens = [
                    ('Background', 'var(--cb-bg)', '--cb-bg'),
                    ('Surface', 'var(--cb-surface)', '--cb-surface'),
                    ('Raised', 'var(--cb-surface-2)', '--cb-surface-2'),
                    ('Border', 'var(--cb-border)', '--cb-border'),
                    ('Copper Action', 'var(--cb-red)', '--cb-red'),
                    ('Sage Success', 'var(--cb-green)', '--cb-green'),
                    ('Steel Info', 'var(--cb-blue)', '--cb-blue'),
                    ('Muted Amber', 'var(--cb-gold)', '--cb-gold'),
                ]
                for name, color, token in tokens:
                    with ui.element('div').classes('style-swatch'):
                        ui.element('div').classes('style-token').style(f'background: {color}')
                        ui.label(name).classes('text-sm font-medium')
                        ui.label(token).classes('mono muted')

        with ui.card().classes('panel w-full'):
            ui.label('Controls').classes('eyebrow red')
            with ui.row().classes('items-center gap-2 flex-wrap'):
                action_button('Primary', icon='play_arrow')
                action_button('Neutral', variant='neutral')
                action_button('Outline', variant='outline')
                action_button('Danger', variant='danger')
                tag('online', 'on')
                tag('idle', 'idle')
                tag('running', 'run')
                tag('error', 'err')

        with ui.card().classes('panel w-full'):
            ui.label('Form Fields').classes('eyebrow red')
            with ui.row().classes('items-end gap-3 flex-wrap w-full'):
                ui.input('Motor name', value='Motor 1').props('outlined').classes('w-40')
                ui.number('PUL', value=9, min=0, max=19).props('outlined').classes('w-24 pin-pul')
                ui.number('DIR', value=8, min=0, max=19).props('outlined').classes('w-24 pin-dir')
                ui.number('ENA', value=7, min=0, max=19).props('outlined').classes('w-24 pin-ena')
                ui.select(options={'forward': 'Forward', 'reverse': 'Reverse'}, value='forward').props('outlined').classes('w-40')
                ui.toggle(['forward', 'reverse'], value='forward')
                ui.switch('Auto')
                ui.checkbox('Reversed')

        with ui.card().classes('panel w-full'):
            ui.label('Panels & Readouts').classes('eyebrow red')
            with ui.element('div').classes('style-grid'):
                with ui.column().classes('gap-2'):
                    ui.label('Readout').classes('text-sm font-medium')
                    ui.label('{"status":"ok","motors":1,"state":"IDLE"}').classes('readout mono')
                with ui.column().classes('gap-2'):
                    ui.label('Alerts').classes('text-sm font-medium')
                    ui.html('<div class="alert alert-info">Info message with compact dark spacing.</div>')
                    ui.html('<div class="alert alert-success">Success message for completed actions.</div>')
                    ui.html('<div class="alert alert-warning">Warning message for recoverable issues.</div>')
                    ui.html('<div class="alert alert-danger">Danger message for stop or error states.</div>')

        with ui.card().classes('panel w-full'):
            ui.label('Rows').classes('eyebrow red')
            for name, state, pins in [
                ('Dispenser', 'online', '9/8/7'),
                ('Roller', 'idle', '12/11/10'),
                ('Punch', 'offline', '6/5/4'),
                ('Crease', 'offline', '9/8/7'),
            ]:
                with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                    ui.label(name).classes('text-sm font-medium w-24')
                    tag(state, 'on' if state == 'online' else 'idle' if state == 'idle' else 'off')
                    ui.label(pins).classes('mono muted')
                    ui.element('div').classes('flex-grow')
                    action_button('Details', variant='outline')
                    action_button('Stop', variant='danger')


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

                def auto_detect():
                    ports = scan_ports()
                    assigned_ports = {s.port for s in serials.values() if s}
                    matched = 0
                    skipped = []
                    for p in ports:
                        if p.device in assigned_ports:
                            continue
                        ser = None
                        try:
                            ser = connect_serial(p.device, 9600)
                            info = identify(ser)
                            board_id = info.get('id') if info else None
                            if board_id in STATION_ORDER and not serials.get(board_id):
                                if station_configs.get(board_id, {}).get('baud', 9600) != 9600:
                                    ser.close()
                                    ser = connect_serial(p.device, station_configs[board_id]['baud'])
                                    info = identify(ser)
                                serials[board_id] = ser
                                remember_port_hint(board_id, p.device)
                                remember_station_firmware(board_id, info)
                                restore = apply_saved_station_state(board_id, ser)
                                matched += 1
                                add_log(f'Auto-detect: {p.device} → {board_id} '
                                        f'({restore.get("count", 0)} motor(s) restored)')
                            else:
                                ser.close()
                                label = board_id or 'unknown'
                                skipped.append(f'{p.device} (id="{label}")')
                        except Exception as e:
                            if ser:
                                try:
                                    ser.close()
                                except Exception:
                                    pass
                            skipped.append(f'{p.device}: {e}')
                    rebuild_coordinator()
                    refresh()
                    if matched:
                        ui.notify(f'Auto-assigned {matched} board(s) by name', type='positive')
                    if skipped:
                        ui.notify('No match: ' + '; '.join(skipped), type='warning')
                    if not matched and not skipped:
                        ui.notify('No unassigned boards found', type='info')

                with ui.row().classes('gap-2'):
                    action_button('Auto-Detect & Assign', on_click=auto_detect, icon='auto_fix_high')
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
            ui.label('Board Identity').classes('eyebrow red')
            ui.label('Write a name into the board (saved in its EEPROM). Auto-Detect on the '
                     'Devices page uses this name to load the right config automatically.').classes('muted text-xs')
            if ser:
                current_id = '—'
                info0 = identify(ser)
                if info0:
                    current_id = info0.get('id', '—')
                ui.label(f'Current board name: {current_id}').classes('mono muted text-xs')
                with ui.row().classes('items-end gap-2 flex-wrap'):
                    id_in = ui.input('Board name', value=name).props('outlined').classes('w-48')

                    def write_id():
                        new_id = (id_in.value or '').strip()
                        if not new_id:
                            ui.notify('Name required', type='warning')
                            return
                        resp = set_station_id(ser, new_id)
                        if resp and resp.get('status') == 'id_set':
                            add_log(f'{name}: board name set to "{new_id}"')
                            ui.notify(f'Board name written: {new_id}', type='positive')
                            refresh_identity()
                        else:
                            ui.notify(f'Failed: {resp.get("error") if resp else "no response"}', type='negative')

                    action_button('Write Name', on_click=write_id, icon='badge')
                    action_button('Use Station Name', on_click=lambda: id_in.set_value(name), variant='neutral')
            else:
                ui.label('Connect first.').classes('muted text-sm')

        with ui.card().classes('panel w-full'):
            ui.label('Board Config (EEPROM)').classes('eyebrow red')
            ui.label('Burn the current motors, limit switches and encoders into the board so it '
                     'rebuilds them itself on every boot - no host needed to repopulate.').classes('muted text-xs')
            if ser:
                cfg_status = ui.label('').classes('mono muted text-xs')

                def save_to_board():
                    station = get_saved_station(name)
                    # Make sure every saved limit/encoder is live on the board first
                    for sw in station.get('limit_switches', []):
                        remove_limit(ser, sw['name'])
                        add_limit(ser, sw['name'], sw['pin'], sw.get('normally_open', True), sw.get('stops', []))
                    for enc in station.get('encoders', []):
                        remove_encoder(ser, enc['name'])
                        add_encoder(ser, enc['name'], enc['pin_a'], enc['pin_b'], enc.get('counts_per_rev', 0))
                    resp = save_config(ser)
                    if resp and resp.get('status') == 'config_saved':
                        cfg_status.text = (f"Saved to EEPROM: {resp.get('motors')} motor(s), "
                                           f"{resp.get('limits')} limit(s), {resp.get('encoders')} encoder(s) "
                                           f"({resp.get('bytes')} bytes)")
                        add_log(f'{name}: config saved to EEPROM ({resp.get("bytes")} bytes)')
                        ui.notify('Config written to board EEPROM', type='positive')
                    else:
                        err = resp.get('error') if resp else 'no response'
                        cfg_status.text = f'Save failed: {err}'
                        ui.notify(f'Save failed: {err}', type='negative')

                def clear_board_config():
                    resp = clear_config(ser)
                    if resp and resp.get('status') == 'config_cleared':
                        cfg_status.text = 'EEPROM config cleared (board will boot empty next time)'
                        add_log(f'{name}: EEPROM config cleared')
                        ui.notify('Board config cleared', type='warning')
                    else:
                        ui.notify('Clear failed', type='negative')

                with ui.row().classes('gap-2'):
                    action_button('Save to Board', on_click=save_to_board, icon='save')
                    action_button('Clear Board Config', on_click=clear_board_config, variant='danger')
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
            # Last-used test values, kept across refreshes so they don't reset.
            test_defaults = {
                'steps': 1000,
                'speed': int(get_global_settings().get('global_speed_us', 62)),
            }
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

                            s_w = ui.number(value=test_defaults['steps'], min=1, max=50000, step=100).props('outlined').classes('w-24').tooltip('Steps')
                            sp_w = ui.number(value=test_defaults['speed'], min=1, max=5000, step=1).props('outlined').classes('w-20').tooltip('μs/step')
                            s_w.on_value_change(lambda e: test_defaults.update(steps=int(e.value)) if e.value else None)
                            sp_w.on_value_change(lambda e: test_defaults.update(speed=int(e.value)) if e.value else None)

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
                        tester_steps = ui.number('Steps', value=test_defaults['steps'], min=1, max=50000, step=100).props('outlined').classes('w-28')
                        tester_speed = ui.number('Speed μs', value=test_defaults['speed'], min=1, max=5000, step=1).props('outlined').classes('w-28')
                        tester_steps.on_value_change(lambda e: test_defaults.update(steps=int(e.value)) if e.value else None)
                        tester_speed.on_value_change(lambda e: test_defaults.update(speed=int(e.value)) if e.value else None)
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

        def station_motor_names():
            names = [m['name'] for m in station_motor_cache.get(name, [])]
            if not names:
                names = [m['name'] for m in get_saved_station(name).get('motors', [])]
            return names

        # ── Limit Switches ──
        with ui.card().classes('panel w-full'):
            ui.label('Limit Switches').classes('eyebrow red')
            ui.label('Firmware stops the listed motors instantly when a switch trips.').classes('muted text-xs')
            limits_box = ui.column().classes('w-full gap-2')

            def render_limits():
                limits_box.clear()
                station = get_saved_station(name)
                switches = station.get('limit_switches', [])
                with limits_box:
                    if not switches:
                        ui.label('No limit switches configured.').classes('muted text-sm')
                    for sw in switches:
                        def mk_remove_limit(target):
                            def do():
                                station['limit_switches'] = [s for s in station['limit_switches'] if s is not target]
                                persist_state()
                                if ser:
                                    remove_limit(ser, target.get('name'))
                                render_limits()
                            return do

                        with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                            ui.label(sw.get('name')).classes('text-sm font-medium w-28')
                            tag('NO' if sw.get('normally_open', True) else 'NC', 'idle')
                            ui.label(f'pin {sw.get("pin")}').classes('mono muted text-xs')
                            stops = sw.get('stops', [])
                            ui.label('stops: ' + (', '.join(stops) if stops else 'ALL')).classes('mono muted text-xs')
                            ui.element('div').classes('flex-grow')
                            action_button('Remove', on_click=mk_remove_limit(sw), variant='danger')

            render_limits()

            with ui.row().classes('items-end gap-2 flex-wrap'):
                ls_name = ui.input('Name').props('outlined').classes('w-28')
                ls_pin = ui.number('Pin', value=2, min=0, max=21).props('outlined').classes('w-20')
                ls_no = ui.toggle({'no': 'Normally Open', 'nc': 'Normally Closed'}, value='no')
                ls_stops = ui.select(options=station_motor_names(), multiple=True, label='Stops (blank = all)') \
                    .props('outlined').classes('w-48')

                def add_limit_switch():
                    nm = (ls_name.value or '').strip()
                    if not nm:
                        ui.notify('Name required', type='warning')
                        return
                    station = get_saved_station(name)
                    if any(s.get('name') == nm for s in station['limit_switches']):
                        ui.notify('Limit switch name already exists', type='warning')
                        return
                    sw = program_model.normalize_limit_switch({
                        'name': nm,
                        'pin': int(ls_pin.value),
                        'normally_open': ls_no.value == 'no',
                        'stops': list(ls_stops.value or []),
                    })
                    station['limit_switches'].append(sw)
                    persist_state()
                    if ser:
                        resp = add_limit(ser, sw['name'], sw['pin'], sw['normally_open'], sw['stops'])
                        if not resp or resp.get('status') != 'limit_added':
                            ui.notify(f'Saved, but board rejected: {resp.get("error") if resp else "no response"}', type='warning')
                    ls_name.value = ''
                    render_limits()
                    ui.notify(f'Added limit switch {nm}', type='positive')

                action_button('Add Switch', on_click=add_limit_switch, icon='add')

                def sync_limits_to_board():
                    if not ser:
                        ui.notify('Connect first', type='warning')
                        return
                    for sw in get_saved_station(name).get('limit_switches', []):
                        remove_limit(ser, sw['name'])
                        add_limit(ser, sw['name'], sw['pin'], sw.get('normally_open', True), sw.get('stops', []))
                    ui.notify('Synced limit switches to board', type='positive')

                action_button('Sync To Board', on_click=sync_limits_to_board, variant='neutral')

        # ── Encoders ──
        with ui.card().classes('panel w-full'):
            ui.label('Encoders').classes('eyebrow red')
            ui.label('Optional rotary feedback for encoder-count step conditions.').classes('muted text-xs')
            encoders_box = ui.column().classes('w-full gap-2')

            def render_encoders():
                encoders_box.clear()
                station = get_saved_station(name)
                encs = station.get('encoders', [])
                with encoders_box:
                    if not encs:
                        ui.label('No encoders configured.').classes('muted text-sm')
                    for enc in encs:
                        def mk_remove_encoder(target):
                            def do():
                                station['encoders'] = [e for e in station['encoders'] if e is not target]
                                persist_state()
                                if ser:
                                    remove_encoder(ser, target.get('name'))
                                render_encoders()
                            return do

                        with ui.row().classes('items-center gap-3 section-row w-full flex-wrap'):
                            ui.label(enc.get('name')).classes('text-sm font-medium w-28')
                            ui.label(f'motor: {enc.get("motor") or "—"}').classes('mono muted text-xs')
                            ui.label(f'A{enc.get("pin_a")}/B{enc.get("pin_b")} cpr {enc.get("counts_per_rev")}').classes('mono muted text-xs')
                            ui.element('div').classes('flex-grow')
                            action_button('Remove', on_click=mk_remove_encoder(enc), variant='danger')

            render_encoders()

            with ui.row().classes('items-end gap-2 flex-wrap'):
                enc_name = ui.input('Name').props('outlined').classes('w-28')
                enc_motor = ui.select(options=station_motor_names(), label='Motor').props('outlined').classes('w-32')
                enc_a = ui.number('Pin A', value=2, min=0, max=21).props('outlined').classes('w-20')
                enc_b = ui.number('Pin B', value=3, min=-1, max=21).props('outlined').classes('w-20')
                enc_cpr = ui.number('Counts/rev', value=0, min=0, max=100000).props('outlined').classes('w-28')

                def add_encoder_cfg():
                    nm = (enc_name.value or '').strip()
                    if not nm:
                        ui.notify('Name required', type='warning')
                        return
                    station = get_saved_station(name)
                    if any(e.get('name') == nm for e in station['encoders']):
                        ui.notify('Encoder name already exists', type='warning')
                        return
                    enc = program_model.normalize_encoder({
                        'name': nm,
                        'motor': enc_motor.value or '',
                        'pin_a': int(enc_a.value),
                        'pin_b': int(enc_b.value),
                        'counts_per_rev': int(enc_cpr.value),
                    })
                    station['encoders'].append(enc)
                    persist_state()
                    if ser:
                        add_encoder(ser, enc['name'], enc['pin_a'], enc['pin_b'], enc['counts_per_rev'])
                    enc_name.value = ''
                    render_encoders()
                    ui.notify(f'Added encoder {nm}', type='positive')

                action_button('Add Encoder', on_click=add_encoder_cfg, icon='add')

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
    ensure_sequence_engine()
    ensure_homing_controller()
    logging.info("Web UI → http://localhost:8080")
    ui.run(title='ChocolateBox', port=8080, reload=False, favicon='🏭')


# Registers the additional pages (Programs editor, Settings, Startup/Homing).
# Imported last so web_ui is fully defined when these modules import it.
from src import program_ui  # noqa: E402,F401
from src import homing_ui  # noqa: E402,F401
from src import verify_ui  # noqa: E402,F401
