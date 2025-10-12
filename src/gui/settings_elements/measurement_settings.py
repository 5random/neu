from typing import Optional, Callable, Any
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

    # Compact field wrapper: rely on control tooltips, avoid extra help labels to save space
    def _field(label: Optional[str], controls: list[Any], help_text: str = '') -> None:
        with ui.column().classes('gap-1 min-w-[220px]'):
            if label:
                ui.label(label).classes('text-caption text-grey-8')
            with ui.row().classes('items-center gap-2 w-full'):
                for idx, ctrl in enumerate(controls):
                    if idx == 0:
                        ctrl.classes('min-w-[140px]')
                    else:
                        ctrl.classes('flex-1')
            # Intentionally omit extra help text label for a more compact layout

    def _set_control_value(ctrl: Any, value: object) -> None:
        try:
            if hasattr(ctrl, 'set_value'):
                ctrl.set_value(value)
            else:
                setattr(ctrl, 'value', value)
                if hasattr(ctrl, 'update'):
                    ctrl.update()
        except Exception:
            pass

    def _bind_number_slider(
        number_ctrl: Any,
        slider_ctrl: Any,
        *,
        min_value: float,
        max_value: float,
        caster: Callable[[Any, float], Optional[float]],
        fallback: float,
        as_int: bool,
        notify_change: Callable[[], None],
    ) -> None:
        syncing = {'active': False}

        def _clamp(value: Any) -> Optional[float]:
            v = caster(value, fallback)
            if v is None:
                return None
            v = max(min_value, v)
            v = min(max_value, v)
            return v

        def _format_value(v: float) -> Any:
            return int(round(v)) if as_int else v

        def _from_slider(event: Optional[object], commit: bool = False) -> None:
            v = _clamp(getattr(event, 'value', getattr(slider_ctrl, 'value', None)))
            if v is None:
                return
            if syncing['active']:
                return
            syncing['active'] = True
            _set_control_value(number_ctrl, _format_value(v))
            syncing['active'] = False
            if commit:
                notify_change()

        def _from_number(event: Optional[object]) -> None:
            if syncing['active']:
                return
            raw = getattr(event, 'value', getattr(number_ctrl, 'value', None)) if event is not None else getattr(number_ctrl, 'value', None)
            v = _clamp(raw)
            if v is None:
                return
            syncing['active'] = True
            _set_control_value(slider_ctrl, _format_value(v))
            syncing['active'] = False
            notify_change()

        slider_ctrl.on('update:model-value', lambda e: _from_slider(e, False))
        slider_ctrl.on('change', lambda e: _from_slider(e, True))
        number_ctrl.on('update:model-value', _from_number)
        number_ctrl.on('blur', _from_number)

    def _cast_to_int(value: Any, fallback: float) -> Optional[float]:
        try:
            return float(int(float(value)))
        except Exception:
            return fallback

    def _cast_to_float(value: Any, fallback: float) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return fallback

    def _get_control_value(ctrl: Any, default: Any) -> Any:
        try:
            value = getattr(ctrl, 'value', default)
        except Exception:
            return default
        return default if value is None else value

    def _make_numeric_field(
        key: str,
        *,
        label: str,
        tooltip: str,
        min_value: float,
        max_value: float,
        step: float,
        fmt: str,
        suffix: str,
        caster: Callable[[Any, float], Optional[float]],
        as_int: bool,
    ) -> tuple[ui.element, ui.element]:
        initial = state[key]
        fallback = float(initial)
        number_ctrl = (
            ui.number(value=initial, min=min_value, max=max_value, step=step, format=fmt)
            .props(f'dense outlined stack-label hide-bottom-space label="{label}" suffix="{suffix}"')
            .tooltip(tooltip)
        )
        slider_ctrl = (
            ui.slider(min=min_value, max=max_value, step=step, value=initial)
            .tooltip(tooltip)
            .classes('flex-1')
        )
        _bind_number_slider(
            number_ctrl,
            slider_ctrl,
            min_value=min_value,
            max_value=max_value,
            caster=caster,
            fallback=fallback,
            as_int=as_int,
            notify_change=_on_change,
        )
        _field(None, [number_ctrl, slider_ctrl])
        return number_ctrl, slider_ctrl

    def _on_change(_=None) -> None:
        # Enable apply if any value differs from config
        try:
            current = {
                'alert_delay_seconds': int(_get_control_value(alert_delay_inp, state['alert_delay_seconds']) or 0),
                'max_alerts_per_session': int(_get_control_value(max_alerts_inp, state['max_alerts_per_session']) or 0),
                'alert_check_interval': float(_get_control_value(check_interval_inp, state['alert_check_interval']) or 0),
                'alert_cooldown_seconds': int(_get_control_value(cooldown_inp, state['alert_cooldown_seconds']) or 0),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, state['alert_include_snapshot'])),
                'inactivity_timeout_minutes': int(_get_control_value(inactivity_inp, state['inactivity_timeout_minutes']) or 0),
                'motion_summary_interval_seconds': int(_get_control_value(summary_interval_inp, state['motion_summary_interval_seconds']) or 0),
                'enable_motion_summary_logs': bool(_get_control_value(enable_summary_cb, state['enable_motion_summary_logs'])),
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
                'alert_delay_seconds': max(30, int(_get_control_value(alert_delay_inp, 0) or 0)),
                'max_alerts_per_session': max(1, int(_get_control_value(max_alerts_inp, 1) or 1)),
                'alert_check_interval': max(0.5, float(_get_control_value(check_interval_inp, 0.5) or 0.5)),
                'alert_cooldown_seconds': max(0, int(_get_control_value(cooldown_inp, 0) or 0)),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, False)),
                'inactivity_timeout_minutes': max(0, int(_get_control_value(inactivity_inp, 0) or 0)),
                'motion_summary_interval_seconds': max(5, int(_get_control_value(summary_interval_inp, 5) or 5)),
                'enable_motion_summary_logs': bool(_get_control_value(enable_summary_cb, True)),
            }
            
            if int(_get_control_value(alert_delay_inp, 0) or 0) < 30:
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
    alert_delay_inp, alert_delay_slider = _make_numeric_field(
        'alert_delay_seconds',
        label='Alert delay',
        tooltip='Seconds without motion before the first alert is sent',
        min_value=30,
        max_value=3600,
        step=5,
        fmt='%.0f',
        suffix='s',
        caster=_cast_to_int,
        as_int=True,
    )
    max_alerts_inp, max_alerts_slider = _make_numeric_field(
        'max_alerts_per_session',
        label='Max alerts per session',
        tooltip='Upper bound on alerts within a single session (spam protection)',
        min_value=1,
        max_value=50,
        step=1,
        fmt='%.0f',
        suffix='',
        caster=_cast_to_int,
        as_int=True,
    )
    check_interval_inp, check_interval_slider = _make_numeric_field(
        'alert_check_interval',
        label='Check interval',
        tooltip='How often the controller evaluates alert conditions',
        min_value=0.5,
        max_value=120.0,
        step=0.5,
        fmt='%.1f',
        suffix='s',
        caster=_cast_to_float,
        as_int=False,
    )
    cooldown_inp, cooldown_slider = _make_numeric_field(
        'alert_cooldown_seconds',
        label='Alert cooldown',
        tooltip='Minimum time between two alerts',
        min_value=0,
        max_value=3600,
        step=5,
        fmt='%.0f',
        suffix='s',
        caster=_cast_to_int,
        as_int=True,
    )
    include_snapshot_cb = ui.checkbox('Include snapshot in alert', value=state['alert_include_snapshot'])
    inactivity_inp, inactivity_slider = _make_numeric_field(
        'inactivity_timeout_minutes',
        label='Inactivity timeout',
        tooltip='Stop session after prolonged inactivity (0 = disabled)',
        min_value=0,
        max_value=720,
        step=5,
        fmt='%.0f',
        suffix='min',
        caster=_cast_to_int,
        as_int=True,
    )
    summary_interval_inp, summary_interval_slider = _make_numeric_field(
        'motion_summary_interval_seconds',
        label='Summary interval',
        tooltip='Period for motion summary logs (>= 5 seconds)',
        min_value=5,
        max_value=3600,
        step=5,
        fmt='%.0f',
        suffix='s',
        caster=_cast_to_int,
        as_int=True,
    )
    enable_summary_cb = ui.checkbox('Enable motion summary logs', value=state['enable_motion_summary_logs'])

    with ui.grid(columns=2).classes('w-full gap-3'):
        _field(None, [alert_delay_inp, alert_delay_slider], 'Time without motion before sending an alert')
        _field(None, [max_alerts_inp, max_alerts_slider], 'Spam protection; minimum 1')
        _field(None, [check_interval_inp, check_interval_slider], 'How often to evaluate alert conditions')
        _field(None, [cooldown_inp, cooldown_slider], 'Minimum time between two alerts')
        _field(None, [inactivity_inp, inactivity_slider], 'Stop session after prolonged inactivity (0 = disabled)')
        _field(None, [summary_interval_inp, summary_interval_slider], 'Period for motion summary logs (>= 5 seconds)')
        _field(None, [include_snapshot_cb], 'Attach a snapshot to alert emails (if camera available)')
        _field(None, [enable_summary_cb], 'Master switch for motion summary logging')

    # Apply bar
    with ui.row().classes('items-center q-gutter-sm q-mt-sm justify-end'):
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
