from typing import Optional
from nicegui import ui
from src.notify import EMailSystem
from src.config import get_global_config, save_global_config, get_logger
from src.measurement import MeasurementController

logger = get_logger('gui.measurement')

def create_measurement_card(
    measurement_controller: Optional[MeasurementController] = None,
    **_: object,
) -> None:
    """Render measurement-related settings not exposed on the default page/card.

    Editable parameters (persisted to config.measurement):
    - alert_delay_seconds
    - max_alerts_per_session
    - alert_check_interval
    - alert_cooldown_seconds
    - alert_include_snapshot
    - inactivity_timeout_minutes
    - motion_summary_interval_seconds
    - enable_motion_summary_logs

    Note: session_timeout_minutes is intentionally omitted here; it's configurable via the measurement card on the default page.
    """
    cfg = get_global_config()
    if not cfg:
        ui.label('⚠️ Configuration not available').classes('text-red')
        logger.error('Configuration not available - cannot create measurement settings')
        return

    m = cfg.measurement

    # Local state snapshot for change detection
    state = {
        'alert_delay_seconds': int(getattr(m, 'alert_delay_seconds', 300)),
        'max_alerts_per_session': int(getattr(m, 'max_alerts_per_session', 5)),
        'alert_check_interval': float(getattr(m, 'alert_check_interval', 5.0)),
        'alert_cooldown_seconds': int(getattr(m, 'alert_cooldown_seconds', 300)),
        'alert_include_snapshot': bool(getattr(m, 'alert_include_snapshot', True)),
        'inactivity_timeout_minutes': int(getattr(m, 'inactivity_timeout_minutes', 60)),
        'motion_summary_interval_seconds': int(getattr(m, 'motion_summary_interval_seconds', 60)),
        'enable_motion_summary_logs': bool(getattr(m, 'enable_motion_summary_logs', True)),
    }

    ui.label('Measurement Settings').classes('text-h6 font-semibold mb-2')

    # Inputs in a responsive grid (optional header label + help)
    def _field(label: Optional[str], control: ui.element, help_text: str = '') -> None:
        with ui.column().classes('gap-1 min-w-[220px]'):
            if label:
                ui.label(label).classes('text-caption text-grey-8')
            control.classes('w-full')
            if help_text:
                ui.label(help_text).classes('text-caption text-grey')

    def _on_change(_=None) -> None:
        # Enable apply if any value differs from config
        try:
            current = {
                'alert_delay_seconds': int(getattr(alert_delay_inp, 'value', state['alert_delay_seconds']) or 0),
                'max_alerts_per_session': int(getattr(max_alerts_inp, 'value', state['max_alerts_per_session']) or 0),
                'alert_check_interval': float(getattr(check_interval_inp, 'value', state['alert_check_interval']) or 0),
                'alert_cooldown_seconds': int(getattr(cooldown_inp, 'value', state['alert_cooldown_seconds']) or 0),
                'alert_include_snapshot': bool(getattr(include_snapshot_cb, 'value', state['alert_include_snapshot'])),
                'inactivity_timeout_minutes': int(getattr(inactivity_inp, 'value', state['inactivity_timeout_minutes']) or 0),
                'motion_summary_interval_seconds': int(getattr(summary_interval_inp, 'value', state['motion_summary_interval_seconds']) or 0),
                'enable_motion_summary_logs': bool(getattr(enable_summary_cb, 'value', state['enable_motion_summary_logs'])),
            }
            if current != state:
                apply_btn.enable()
            else:
                apply_btn.disable()
        except Exception:
            # be conservative
            apply_btn.enable()

    def _persist() -> None:
        nonlocal state
        cfg = get_global_config()
        if not cfg:
            ui.notify('Configuration not available', type='warning', position='bottom-right')
            return
        try:
            # Validate and provide feedback for constraint violations
            violations = []
            
            # Read with basic constraints
            new_state = {
                'alert_delay_seconds': max(30, int(alert_delay_inp.value or 0)),
                'max_alerts_per_session': max(1, int(max_alerts_inp.value or 1)),
                'alert_check_interval': max(0.5, float(check_interval_inp.value or 0.5)),
                'alert_cooldown_seconds': max(0, int(cooldown_inp.value or 0)),
                'alert_include_snapshot': bool(include_snapshot_cb.value),
                'inactivity_timeout_minutes': max(0, int(inactivity_inp.value or 0)),
                'motion_summary_interval_seconds': max(5, int(summary_interval_inp.value or 5)),
                'enable_motion_summary_logs': bool(enable_summary_cb.value),
            }
            
            if int(alert_delay_inp.value or 0) < 30:
                violations.append('Alert delay minimum is 30 seconds')
            # Add similar checks for other constraints...
            
            if violations:
                ui.notify('; '.join(violations), type='warning', position='bottom-right')            # Persist to config
            mc = cfg.measurement
            mc.alert_delay_seconds = new_state['alert_delay_seconds']
            mc.max_alerts_per_session = new_state['max_alerts_per_session']
            mc.alert_check_interval = new_state['alert_check_interval']
            mc.alert_cooldown_seconds = new_state['alert_cooldown_seconds']
            mc.alert_include_snapshot = new_state['alert_include_snapshot']
            mc.inactivity_timeout_minutes = new_state['inactivity_timeout_minutes']
            mc.motion_summary_interval_seconds = new_state['motion_summary_interval_seconds']
            mc.enable_motion_summary_logs = new_state['enable_motion_summary_logs']

            if save_global_config():
                # Live-apply to running controller if provided
                if measurement_controller is not None:
                    measurement_controller.config = mc
                state = new_state
                apply_btn.disable()
                ui.notify('Measurement settings saved', type='positive', position='bottom-right')
            else:
                ui.notify('Failed to save settings', type='negative', position='bottom-right')
        except Exception as exc:
            logger.error('Failed to save measurement settings: %s', exc, exc_info=True)
            ui.notify(f'Error saving settings: {exc}', type='negative', position='bottom-right')

    # Build controls with inline labels and tooltips for clarity
    alert_delay_inp = (
        ui.number(value=state['alert_delay_seconds'], min=30, format='%.0f')
        .props('dense outlined stack-label label="Alert delay" suffix="s"')
        .tooltip('Seconds without motion before the first alert is sent')
    )
    max_alerts_inp = (
        ui.number(value=state['max_alerts_per_session'], min=1, format='%.0f')
        .props('dense outlined stack-label label="Max alerts per session"')
        .tooltip('Upper bound on alerts within a single session (spam protection)')
    )
    check_interval_inp = (
        ui.number(value=state['alert_check_interval'], min=0.5, step=0.5)
        .props('dense outlined stack-label label="Check interval" suffix="s"')
        .tooltip('How often the controller evaluates alert conditions')
    )
    cooldown_inp = (
        ui.number(value=state['alert_cooldown_seconds'], min=0, format='%.0f')
        .props('dense outlined stack-label label="Alert cooldown" suffix="s"')
        .tooltip('Minimum time between two alerts')
    )
    include_snapshot_cb = ui.checkbox('Include snapshot in alert', value=state['alert_include_snapshot'])
    inactivity_inp = (
        ui.number(value=state['inactivity_timeout_minutes'], min=0, format='%.0f')
        .props('dense outlined stack-label label="Inactivity timeout" suffix="min"')
        .tooltip('Stop session after prolonged inactivity (0 = disabled)')
    )
    summary_interval_inp = (
        ui.number(value=state['motion_summary_interval_seconds'], min=5, format='%.0f')
        .props('dense outlined stack-label label="Summary interval" suffix="s"')
        .tooltip('Period for motion summary logs (>= 5 seconds)')
    )
    enable_summary_cb = ui.checkbox('Enable motion summary logs', value=state['enable_motion_summary_logs'])

    with ui.grid(columns=2).classes('w-full gap-4'):
        _field(None, alert_delay_inp, 'Time without motion before sending an alert')
        _field(None, max_alerts_inp, 'Spam protection; minimum 1')
        _field(None, check_interval_inp, 'How often to evaluate alert conditions')
        _field(None, cooldown_inp, 'Minimum time between two alerts')
        _field(None, inactivity_inp, 'Stop session after prolonged inactivity (0 = disabled)')
        _field(None, summary_interval_inp, 'Period for motion summary logs (>= 5 seconds)')
        _field(None, include_snapshot_cb, 'Attach a snapshot to alert emails (if camera available)')
        _field(None, enable_summary_cb, 'Master switch for motion summary logging')

    # Apply bar
    with ui.row().classes('items-center q-gutter-sm q-mt-md justify-end'):
        apply_btn = ui.button('Apply', on_click=lambda _: _persist()).props('color=primary')
        apply_btn.disable()

    # Wire change handlers
    for ctrl in [
        alert_delay_inp,
        max_alerts_inp,
        check_interval_inp,
        cooldown_inp,
        include_snapshot_cb,
        inactivity_inp,
        summary_interval_inp,
        enable_summary_cb,
    ]:
        ctrl.on('update:model-value', _on_change)
        # Also blur for numbers to catch manual edits
        if hasattr(ctrl, 'on') and ctrl is not include_snapshot_cb and ctrl is not enable_summary_cb:
            ctrl.on('blur', _on_change)

    # Initial state
    _on_change()
