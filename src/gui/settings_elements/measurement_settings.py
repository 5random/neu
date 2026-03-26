from typing import Optional, Callable, Any
from nicegui import ui
from src.config import get_global_config, save_global_config, get_logger
from src.measurement import MeasurementController
from src.gui import instances
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row

logger = get_logger('gui.measurement')

def create_measurement_settings_card(
    measurement_controller: Optional[MeasurementController] = None,
    show_header: bool = True,
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

    if show_header:
        create_heading_row(
            'Measurement Settings',
            icon='straighten',
            title_classes='text-h6 font-semibold mb-2',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
    value_classes = 'w-[7rem] min-w-[7rem] max-w-[7rem] shrink-0'
    slider_classes = 'flex-1 min-w-[10rem] max-w-full'

    from src.gui.bindings import bind_number_slider

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

    def _build_numeric_card(
        key: str,
        *,
        title: str,
        tooltip: str,
        min_value: float,
        max_value: float,
        step: float,
        fmt: str,
        suffix: str,
        caster: Callable[[Any, float], Optional[float]],
        as_int: bool,
    ) -> tuple[Any, Any]:
        initial = state[key]
        fallback = float(initial)
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            ui.label(f'{title}:').classes('font-semibold mb-1 self-start')
            with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                number_ctrl = (
                    ui.number(value=initial, min=min_value, max=max_value, step=step, format=fmt)
                    .props(f'dense outlined hide-bottom-space suffix="{suffix}"')
                    .tooltip(tooltip)
                    .classes(value_classes)
                )
                slider_ctrl = (
                    ui.slider(min=min_value, max=max_value, step=step, value=initial)
                    .tooltip(tooltip)
                    .classes(slider_classes)
                )
        bind_number_slider(
            number_ctrl,
            slider_ctrl,
            min_value=min_value,
            max_value=max_value,
            caster=caster,
            fallback_value=fallback,
            as_int=as_int,
            on_change=lambda _: _on_change(),
        )
        return number_ctrl, slider_ctrl

    def _build_toggle_card(title: str, checkbox_label: str, value: bool, tooltip: str) -> Any:
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            ui.label(title).classes('font-semibold mb-1 self-start')
            checkbox = (
                ui.checkbox(checkbox_label, value=value)
                .tooltip(tooltip)
                .classes('self-start')
            )
        return checkbox

    def _on_change(_: Any = None) -> None:
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
            mc.alert_delay_seconds = int(new_state['alert_delay_seconds'])
            mc.max_alerts_per_session = int(new_state['max_alerts_per_session'])
            mc.alert_check_interval = float(new_state['alert_check_interval'])
            mc.alert_cooldown_seconds = int(new_state['alert_cooldown_seconds'])
            mc.alert_include_snapshot = bool(new_state['alert_include_snapshot'])
            mc.inactivity_timeout_minutes = int(new_state['inactivity_timeout_minutes'])
            mc.motion_summary_interval_seconds = int(new_state['motion_summary_interval_seconds'])
            mc.enable_motion_summary_logs = bool(new_state['enable_motion_summary_logs'])

            if save_global_config():
                # Live-apply to running controller if provided
                if measurement_controller is not None:
                    measurement_controller.update_config(mc)
                email_system = instances.get_email_system()
                if email_system is not None:
                    email_system.refresh_config()
                state = new_state
                apply_btn.disable()
                ui.notify('Measurement settings saved', type='positive', position='bottom-right')
            else:
                ui.notify('Failed to save settings', type='negative', position='bottom-right')
        except Exception as exc:
            logger.error('Failed to save measurement settings: %s', exc, exc_info=True)
            ui.notify(f'Error saving settings: {exc}', type='negative', position='bottom-right')

    with ui.grid(columns=2).classes('w-full gap-3 mb-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        alert_delay_inp, alert_delay_slider = _build_numeric_card(
            'alert_delay_seconds',
            title='Alert Delay',
            tooltip='Seconds without motion before the first alert is sent',
            min_value=30,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )
        max_alerts_inp, max_alerts_slider = _build_numeric_card(
            'max_alerts_per_session',
            title='Max Alerts Per Session',
            tooltip='Upper bound on alerts within a single session (spam protection)',
            min_value=1,
            max_value=50,
            step=1,
            fmt='%.0f',
            suffix='',
            caster=_cast_to_int,
            as_int=True,
        )
        check_interval_inp, check_interval_slider = _build_numeric_card(
            'alert_check_interval',
            title='Check Interval',
            tooltip='How often the controller evaluates alert conditions',
            min_value=0.5,
            max_value=120.0,
            step=0.5,
            fmt='%.1f',
            suffix='s',
            caster=_cast_to_float,
            as_int=False,
        )
        cooldown_inp, cooldown_slider = _build_numeric_card(
            'alert_cooldown_seconds',
            title='Alert Cooldown',
            tooltip='Minimum time between two alerts',
            min_value=0,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )
        inactivity_inp, inactivity_slider = _build_numeric_card(
            'inactivity_timeout_minutes',
            title='Inactivity Timeout',
            tooltip='Stop session after prolonged inactivity (0 = disabled)',
            min_value=0,
            max_value=720,
            step=5,
            fmt='%.0f',
            suffix='min',
            caster=_cast_to_int,
            as_int=True,
        )
        summary_interval_inp, summary_interval_slider = _build_numeric_card(
            'motion_summary_interval_seconds',
            title='Summary Interval',
            tooltip='Period for motion summary logs (>= 5 seconds)',
            min_value=5,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )

    with ui.grid(columns=2).classes('w-full gap-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        include_snapshot_cb = _build_toggle_card(
            'Alert Snapshot',
            'Include snapshot in alert',
            bool(state['alert_include_snapshot']),
            'Attach a snapshot to alert emails when an alert is sent',
        )
        enable_summary_cb = _build_toggle_card(
            'Motion Summary Logs',
            'Enable motion summary logs',
            bool(state['enable_motion_summary_logs']),
            'Master switch for periodic motion summary logging',
        )

    # Apply bar
    with ui.row().classes('items-center q-gutter-sm q-mt-sm justify-end'):
        apply_btn = create_action_button('apply', on_click=lambda _: _persist())
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
