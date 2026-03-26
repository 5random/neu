from __future__ import annotations
from datetime import datetime, timedelta
import asyncio
from nicegui import ui

from src.notify import EMailSystem
from src.config import get_global_config, save_global_config, get_logger
from src.cam.camera import Camera
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row
from src.gui.util import schedule_bg
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from src.measurement import MeasurementController

logger = get_logger('gui.measurement')


def _derive_elapsed_duration(start_time: datetime) -> timedelta:
    """Return elapsed time using a current timestamp that matches start_time awareness."""
    if start_time.tzinfo is not None and start_time.utcoffset() is not None:
        return datetime.now(tz=start_time.tzinfo) - start_time
    return datetime.now() - start_time


def _calculate_session_progress_ratio(elapsed: timedelta, session_max: timedelta) -> float:
    """Return a clamped progress ratio for the current session timeout window."""
    max_seconds = session_max.total_seconds()
    if max_seconds <= 0:
        return 0.0
    return max(0.0, min(elapsed.total_seconds() / max_seconds, 1.0))


def create_measurement_card(
    measurement_controller: Optional['MeasurementController'] = None,
    camera: Camera | None = None,
    email_system: EMailSystem | None = None,
    show_recipients: bool = True,
    confirm_stop: bool = False,
    **kwargs: Any,
) -> None:
    # Back-compat
    if email_system is None and 'alert_system' in kwargs:
        email_system = kwargs.pop('alert_system')
 
    config = get_global_config()

    if not config:
        ui.label('⚠️ Configuration not available').classes('text-red')
        logger.error('Configuration not available - cannot create measurement card')
        return
    
    logger.info("Creating measurement card")
    
    if measurement_controller is None:
        logger.warning('Measurement card received no shared controller; creating a local controller instance')
        if email_system is None:
            email_system = EMailSystem(config.email, config.measurement, config)
        from src.measurement import MeasurementController as RuntimeMeasurementController

        measurement_controller = RuntimeMeasurementController(
            config.measurement,
            email_system,
            camera,
        )
    else:
        if email_system is not None and measurement_controller.email_system != email_system:
            measurement_controller.email_system = email_system
    runtime_camera = camera or getattr(measurement_controller, 'camera', None)
    logger.debug(
        'Measurement card wiring: controller_available=%s controller_id=%s camera_available=%s',
        measurement_controller is not None,
        id(measurement_controller) if measurement_controller is not None else 'none',
        runtime_camera is not None,
    )

    # ------------------------- Zustände -------------------------

    DURATION_UNIT_SECONDS = {'s': 1, 'min': 60, 'h': 3600, 'd': 86400}
    DURATION_UNIT_OPTIONS = {
        's': 'Seconds',
        'min': 'Minutes',
        'h': 'Hours',
        'd': 'Days',
    }
    DURATION_UNIT_SUFFIXES = {'s': 's', 'min': 'min', 'h': 'h', 'd': 'd'}
    DURATION_UNIT_STEP = {'s': 1.0, 'min': 0.1, 'h': 0.01, 'd': 0.001}
    DURATION_UNIT_PRECISION = {'s': 0, 'min': 1, 'h': 2, 'd': 3}
    DEFAULT_DURATION_SECONDS = 60
    MIN_DURATION_SECONDS = 1

    last_measurement: datetime | None = None
    status_error_logged = False
    refresh_error_logged = False

    # ------------------- Hilfsfunktionen ----------------------
    def fmt(td: timedelta) -> str:
        secs = max(0, int(td.total_seconds()))
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f'{h:02}:{m:02}:{s:02}'

    def _coerce_duration_value(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _get_config_session_timeout_seconds() -> int:
        measurement_config = measurement_controller.config if measurement_controller is not None else config.measurement
        if hasattr(measurement_config, 'get_session_timeout_seconds'):
            try:
                return max(0, int(measurement_config.get_session_timeout_seconds()))
            except Exception as e:
                logger.warning('Invalid measurement config timeout; falling back to legacy minutes: %s', e)

        raw_seconds = getattr(measurement_config, 'session_timeout_seconds', 0)
        try:
            timeout_seconds = max(0, int(raw_seconds or 0))
        except (TypeError, ValueError):
            timeout_seconds = 0

        if timeout_seconds > 0:
            return timeout_seconds

        raw_minutes = getattr(measurement_config, 'session_timeout_minutes', 0)
        try:
            return max(0, int(raw_minutes or 0) * 60)
        except (TypeError, ValueError):
            logger.warning('Invalid legacy measurement timeout minutes: %r', raw_minutes)
            return 0

    def _get_session_timeout_seconds(status: dict[str, Any]) -> int:
        raw_value = status.get('session_timeout_seconds', _get_config_session_timeout_seconds())
        try:
            timeout_seconds = max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            logger.warning('Invalid session_timeout_seconds in measurement status: %r', raw_value)
            timeout_seconds = 0

        if timeout_seconds > 0:
            return timeout_seconds

        raw_minutes = status.get('session_timeout_minutes', 0)
        try:
            return max(0, int(raw_minutes or 0) * 60)
        except (TypeError, ValueError):
            logger.warning('Invalid legacy session_timeout_minutes in measurement status: %r', raw_minutes)
            return 0

    def _get_duration_unit(unit: Any) -> str:
        return str(unit) if unit in DURATION_UNIT_SECONDS else 'min'

    def _get_duration_step(unit: str) -> float:
        return DURATION_UNIT_STEP[_get_duration_unit(unit)]

    def _round_duration_value(value: float, unit: str) -> float:
        precision = DURATION_UNIT_PRECISION[_get_duration_unit(unit)]
        return round(value, precision)

    def _get_unit_min_value(unit: str) -> float:
        normalized_unit = _get_duration_unit(unit)
        return max(
            MIN_DURATION_SECONDS / DURATION_UNIT_SECONDS[normalized_unit],
            _get_duration_step(normalized_unit),
        )

    def _seconds_to_duration_value(total_seconds: int, unit: str) -> float:
        normalized_unit = _get_duration_unit(unit)
        value = max(0, int(total_seconds or 0)) / DURATION_UNIT_SECONDS[normalized_unit]
        return _round_duration_value(value, normalized_unit)

    def _pick_duration_unit(total_seconds: int) -> str:
        if total_seconds <= 0:
            return 'min'
        for unit in ('d', 'h', 'min', 's'):
            divisor = DURATION_UNIT_SECONDS[unit]
            if total_seconds % divisor == 0:
                return unit
        return 's'

    def _get_elapsed_duration(status: dict[str, Any]) -> timedelta | None:
        elapsed = status.get('duration')
        if elapsed is None:
            return None
        if isinstance(elapsed, timedelta):
            return elapsed
        logger.warning('Invalid duration type in measurement status: %s', type(elapsed).__name__)
        return None

    def _resolve_elapsed_duration(status: dict[str, Any]) -> timedelta | None:
        elapsed = _get_elapsed_duration(status)
        if elapsed is not None:
            return elapsed

        start_time = _get_session_start_time(status)
        if bool(status.get('is_active', False)) and start_time is not None:
            derived = _derive_elapsed_duration(start_time)
            return max(derived, timedelta(0))
        return None

    def _get_session_start_time(status: dict[str, Any]) -> datetime | None:
        start_time = status.get('session_start_time')
        if start_time is None or isinstance(start_time, datetime):
            return start_time
        logger.warning('Invalid session_start_time type in measurement status: %s', type(start_time).__name__)
        return None

    def _safe_get_status() -> dict[str, Any]:
        nonlocal status_error_logged

        timeout_seconds = _get_config_session_timeout_seconds()
        fallback_status = {
            'is_active': False,
            'session_id': None,
            'session_start_time': None,
            'duration': None,
            'alert_triggered': False,
            'session_timeout_seconds': timeout_seconds,
            'session_timeout_minutes': getattr(measurement_controller.config, 'session_timeout_minutes', 0)
            if measurement_controller is not None
            else 0,
            'recent_motion_detected': False,
            'time_since_motion': 0.0,
            'alert_countdown': None,
        }

        if measurement_controller is None:
            return fallback_status

        try:
            status = measurement_controller.get_session_status()
        except Exception:
            if not status_error_logged:
                logger.exception('Failed to read measurement status for dashboard card')
                status_error_logged = True
            return fallback_status

        if status_error_logged:
            logger.info('Measurement status retrieval recovered')
            status_error_logged = False
        return status

    def _request_view_refresh(status: dict[str, Any] | None = None) -> None:
        nonlocal refresh_error_logged

        try:
            _update_view(status)
        except Exception:
            if not refresh_error_logged:
                logger.exception('Measurement card refresh failed')
                refresh_error_logged = True
            return

        if refresh_error_logged:
            logger.info('Measurement card refresh recovered')
            refresh_error_logged = False

    def _apply_duration_controls_from_seconds(total_seconds: int, session_active: bool) -> None:
        normalized_seconds = max(0, int(total_seconds or 0))
        selected_unit = _pick_duration_unit(normalized_seconds or DEFAULT_DURATION_SECONDS)
        enable_limit.value = normalized_seconds > 0
        duration_unit.value = selected_unit
        duration_input.value = _seconds_to_duration_value(
            normalized_seconds or DEFAULT_DURATION_SECONDS,
            selected_unit,
        )
        update_duration_ui()
        sync_duration_controls(session_active)

    initial_status = _safe_get_status()
    initial_start_time = _get_session_start_time(initial_status)
    if initial_start_time is not None:
        last_measurement = initial_start_time

    def _update_view(status: dict[str, Any] | None = None) -> None:
        """Aktualisiert Laufzeit, Fortschritt und Status-Labels."""
        nonlocal last_measurement
        if status is None:
            status = _safe_get_status()
        elapsed = _resolve_elapsed_duration(status)
        session_active = bool(status.get('is_active', False))
        session_timeout_seconds = _get_session_timeout_seconds(status)
        session_max = (
            timedelta(seconds=session_timeout_seconds)
            if session_timeout_seconds > 0
            else None
        )
        session_start_time = _get_session_start_time(status)

        if session_start_time is not None:
            last_measurement = session_start_time

        if session_active and elapsed is not None:
            if session_max:
                remaining = max(session_max - elapsed, timedelta(0))
                timer_label.text = f'{fmt(elapsed)} / {fmt(session_max)}'
                ratio = _calculate_session_progress_ratio(elapsed, session_max)
                progress.value = ratio
                elapsed_label.text = fmt(elapsed)
                remaining_label.text = fmt(remaining)
                progress_row.visible = True
            else:
                timer_label.text = fmt(elapsed)
                progress.value = 0.0
                elapsed_label.text = fmt(elapsed)
                remaining_label.text = '-'
                progress_row.visible = False
        else:
            timer_label.text = '-'
            progress.value = 0.0
            elapsed_label.text = '-'
            remaining_label.text = '-'
            progress_row.visible = False

        sync_duration_controls(session_active)

        camera_status = runtime_camera.is_camera_available() if runtime_camera else False

        if camera_status:
            motion = status.get('recent_motion_detected', False)
            motion_label.text = 'Motion detected' if motion else 'No motion'
            motion_label.classes(remove='text-negative text-warning text-grey', add='text-primary' if motion else 'text-grey')
        else:
            motion_label.text = 'Camera unavailable'
            motion_label.classes(remove='text-grey text-primary', add='text-warning')

        if not session_active:
            if not camera_status:
                alert_label.text = 'Check Camera'
                alert_label.classes(remove='text-negative text-positive text-grey', add='text-warning')
            else:
                alert_label.text = 'Idle'
                alert_label.classes(remove='text-negative text-positive text-warning', add='text-grey')
        elif camera_status and status.get('recent_motion_detected'):
            alert_label.text = 'Safe (Motion)'
            alert_label.classes(remove='text-negative text-grey text-warning', add='text-positive')
        else:
            countdown = status.get('alert_countdown')
            if status.get('alert_triggered'):
                alert_label.text = 'Alert triggered'
                alert_label.classes(remove='text-positive text-grey text-warning', add='text-negative')
            elif countdown is not None and countdown > 0:
                alert_label.text = f'Alert in {fmt(timedelta(seconds=countdown))}'
                alert_label.classes(remove='text-positive text-grey text-warning', add='text-negative')
            elif not camera_status:
                alert_label.text = 'Check Camera'
                alert_label.classes(remove='text-negative text-positive text-grey', add='text-warning')
            else:
                alert_label.text = 'Monitoring...'
                alert_label.classes(remove='text-negative text-positive text-warning', add='text-grey')

        last_label.text = (
            last_measurement.strftime('%H:%M:%S')
            if last_measurement else '-'
        )

        timer_label.update()
        progress.update()
        elapsed_label.update()
        remaining_label.update()
        progress_row.update()
        motion_label.update()
        alert_label.update()
        last_label.update()


    configured_timeout_seconds = _get_config_session_timeout_seconds()
    initial_duration_unit = _pick_duration_unit(configured_timeout_seconds or DEFAULT_DURATION_SECONDS)

    def sync_duration_controls(session_active: bool) -> None:
        if session_active:
            enable_limit.disable()
            duration_input.disable()
            duration_unit.disable()
            return

        enable_limit.enable()
        if enable_limit.value:
            duration_input.enable()
            duration_unit.enable()
        else:
            duration_input.disable()
            duration_unit.disable()

    def style_start_button(status: dict[str, Any] | None = None) -> None:
        current_status = _safe_get_status() if status is None else status
        if current_status['is_active']:
            start_stop_btn.icon = 'stop'
            start_stop_btn.props('color=negative')
            start_stop_btn.tooltip('Stop Session')
        else:
            start_stop_btn.icon = 'play_arrow'
            start_stop_btn.props('color=positive')
            start_stop_btn.tooltip('Start Session')
        sync_duration_controls(bool(current_status.get('is_active', False)))
        start_stop_btn.update()

    # ---------------- UI-Update -----------------

    def update_duration_ui(_: Any = None) -> None:
        """Aktualisiert die UI-Elemente für die Dauer."""
        unit = _get_duration_unit(duration_unit.value)
        min_val = _get_unit_min_value(unit)
        step = _get_duration_step(unit)
        suffix = DURATION_UNIT_SUFFIXES[unit]
        current_value = _coerce_duration_value(duration_input.value, default=min_val)
        normalized_value = _round_duration_value(max(current_value, min_val), unit)

        duration_input.label = 'Duration'
        duration_input.min = float(min_val)
        duration_input.suffix = suffix
        duration_input._props['step'] = step
        if duration_input.value != normalized_value:
            duration_input.value = normalized_value
        duration_input.update()

    def persist_settings() -> None:
        """Persist measurement duration settings to the config."""
        runtime_config = get_global_config()
        if not runtime_config:
            return
        cfg = runtime_config.measurement
        previous_timeout_seconds = max(0, int(getattr(cfg, 'session_timeout_seconds', 0) or 0))
        previous_timeout_minutes = max(0, int(getattr(cfg, 'session_timeout_minutes', 0) or 0))

        def _restore_previous_timeout() -> None:
            cfg.session_timeout_seconds = previous_timeout_seconds
            cfg.session_timeout_minutes = previous_timeout_minutes
            restored_seconds = (
                max(0, int(cfg.get_session_timeout_seconds()))
                if hasattr(cfg, 'get_session_timeout_seconds')
                else max(0, previous_timeout_minutes * 60)
            )
            current_status = _safe_get_status()
            _apply_duration_controls_from_seconds(
                restored_seconds,
                bool(current_status.get('is_active', False)),
            )
            _request_view_refresh(current_status)
            ui.notify('Failed to save measurement duration', color='negative')

        if not enable_limit.value:
            # Limit deaktiviert ⇒ 0 Minuten speichern
            if hasattr(cfg, 'set_session_timeout_seconds'):
                cfg.set_session_timeout_seconds(0)
            else:
                cfg.session_timeout_minutes = 0
                cfg.session_timeout_seconds = 0
            if not save_global_config():
                _restore_previous_timeout()
                return
            if measurement_controller is not None:
                measurement_controller.update_config(cfg)
            _request_view_refresh()
            return

        unit = _get_duration_unit(duration_unit.value)
        raw_value = _coerce_duration_value(duration_input.value, default=_get_unit_min_value(unit))
        seconds = max(MIN_DURATION_SECONDS, int(round(raw_value * DURATION_UNIT_SECONDS[unit])))

        if hasattr(cfg, 'set_session_timeout_seconds'):
            cfg.set_session_timeout_seconds(seconds)
        else:
            cfg.session_timeout_seconds = seconds
            cfg.session_timeout_minutes = (seconds + 59) // 60

        if hasattr(cfg, 'get_session_timeout_seconds'):
            normalized_seconds = max(0, int(cfg.get_session_timeout_seconds()))
        else:
            normalized_seconds = max(0, int(getattr(cfg, 'session_timeout_minutes', 0) or 0) * 60)
        duration_input.value = _seconds_to_duration_value(normalized_seconds, unit)
        if not save_global_config():
            _restore_previous_timeout()
            return
        if measurement_controller is not None:
            measurement_controller.update_config(cfg)
        _request_view_refresh()


    # -------------------------- UI ------------------------------
    # Make the measurement card expand to use available vertical space in its column
    with ui.card().classes('w-full flex-1 p-4').style('align-self:stretch; min-height:0;'):
        # Header
        with ui.row().classes('items-center justify-between w-full mb-2'):
            create_heading_row(
                'Measurement',
                icon=SECTION_ICONS['measurement'],
                title_classes='text-h6 font-semibold',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-xl shrink-0',
            )
            ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#measurement')) \
                .props('flat round dense').tooltip('Open measurement settings')

        # Main Controls (Start/Stop + Duration)
        with ui.row().classes('items-center w-full gap-4 mb-4 no-wrap'):
            # Big Start Button
            start_stop_btn = ui.button(icon='play_arrow', color='positive').props('round size=lg') \
                .classes('shadow-lg')
            
            # Duration Controls Group
            with ui.column().classes('gap-1 flex-1'):
                with ui.row().classes('items-center gap-2'):
                    enable_limit = ui.checkbox(
                        'Max Duration', value=configured_timeout_seconds > 0
                    ).props('dense').tooltip('Enable automatic session timeout')
                
                with ui.row().classes('items-center gap-2 no-wrap'):
                    duration_input = ui.number(
                        value=_seconds_to_duration_value(
                            configured_timeout_seconds or DEFAULT_DURATION_SECONDS,
                            initial_duration_unit,
                        ),
                        min=_get_unit_min_value(initial_duration_unit),
                        step=_get_duration_step(initial_duration_unit),
                    ).props('dense outlined hide-bottom-space').classes('w-24')

                    duration_unit = ui.select(
                        options=DURATION_UNIT_OPTIONS,
                        value=initial_duration_unit,
                    ).props('dense outlined options-dense').classes('w-20')

            update_duration_ui()

        ui.separator().classes('mb-4')

        # Status Display (Timer & Progress)
        with ui.column().classes('w-full items-center gap-1 mb-4'):
            timer_label = ui.label('-').classes('text-h4 font-mono font-bold text-primary')
            
            with ui.row().classes('w-full items-center gap-3 no-wrap') as progress_row:
                elapsed_label = ui.label('-').classes('text-caption font-mono min-w-[5.5rem] text-grey-7')
                with ui.element('div').classes('flex-1 w-full min-w-0'):
                    progress = (
                        ui.linear_progress(value=0.0, size='12px', color='primary', show_value=False)
                        .props('rounded track-color=grey-4')
                        .classes('w-full')
                    )
                remaining_label = ui.label('-').classes('text-caption font-mono min-w-[5.5rem] text-right text-grey-7')
            progress_row.visible = False

        ui.separator().classes('mb-4')

        # Info Grid (Motion, Alert, Last)
        with ui.grid(columns=2).classes('w-full gap-x-4 gap-y-2'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('sensors').classes('text-grey-7 text-sm shrink-0')
                ui.label('Motion:').classes('text-caption font-bold text-grey-7')
            motion_label = ui.label('No motion').classes('text-caption text-grey')
            
            with ui.row().classes('items-center gap-2'):
                ui.icon('notifications_active').classes('text-grey-7 text-sm shrink-0')
                ui.label('Status:').classes('text-caption font-bold text-grey-7')
            alert_label = ui.label('Monitoring...').classes('text-caption text-grey')
            
            with ui.row().classes('items-center gap-2'):
                ui.icon('history').classes('text-grey-7 text-sm shrink-0')
                ui.label('Last Run:').classes('text-caption font-bold text-grey-7')
            last_label = ui.label('-').classes('text-caption text-grey')

        if show_recipients:
            ui.separator().classes('my-4')

            # Recipient Groups (Async Load)
            groups_select: Optional[ui.select] = None
            _last_groups_opts: list[str] = []
            groups_build_lock = asyncio.Lock()
            apply_btn: Optional[ui.button] = None

            def _update_apply_groups_state() -> None:
                nonlocal groups_select, apply_btn
                try:
                    if apply_btn is None or groups_select is None:
                        return
                    conf = get_global_config()
                    if not conf or not getattr(conf, 'email', None):
                        apply_btn.disable()
                        return
                    raw_val = getattr(groups_select, 'value', [])
                    selected = set((raw_val or [])) if isinstance(raw_val, (list, tuple, set)) else {raw_val}
                    current = set(getattr(conf.email, 'active_groups', []) or [])
                    if selected == current:
                        apply_btn.disable()
                    else:
                        apply_btn.enable()
                except Exception as e:
                    logger.error(f"Error updating apply groups state: {e}")

            with ui.column().classes('w-full gap-2') as groups_container:
                create_heading_row(
                    'Active Recipients',
                    icon='groups',
                    title_classes='text-caption font-bold text-grey-7',
                    row_classes='items-center gap-2',
                    icon_classes='text-grey-7 text-sm shrink-0',
                )
                loading_lbl = ui.label('Loading...').classes('text-caption text-grey italic')

                async def _build_groups_ui() -> None:
                    nonlocal groups_select, _last_groups_opts, apply_btn
                    async with groups_build_lock:
                        cfg = await asyncio.to_thread(get_global_config)
                        opts = list(getattr(cfg.email, 'groups', {}).keys()) if cfg and cfg.email else []
                        vals = list(getattr(cfg.email, 'active_groups', [])) if cfg and cfg.email else []
                        _last_groups_opts = list(opts)
                        
                        try:
                            with groups_container:
                                loading_lbl.delete()
                                with ui.row().classes('w-full items-center gap-2 no-wrap'):
                                    groups_select = ui.select(
                                        options=opts,
                                        value=vals,
                                        multiple=True,
                                        label='Select Groups'
                                    ).props('dense outlined use-chips').classes('flex-1')

                                    apply_btn = ui.button(icon='check', on_click=lambda: apply_groups()).props('round dense flat color=primary').tooltip('Apply Changes')

                                def _on_groups_change(_: Any = None) -> None:
                                    _update_apply_groups_state()
                                groups_select.on('update:model-value', _on_groups_change)

                                def apply_groups() -> None:
                                    nonlocal apply_btn, groups_select
                                    try:
                                        button = apply_btn
                                        select = groups_select
                                        if button is None or select is None:
                                            return

                                        button.disable()
                                        conf = get_global_config()
                                        if not conf or not getattr(conf, 'email', None):
                                            return
                                        
                                        raw_val = getattr(select, 'value', [])
                                        selected = list(raw_val) if isinstance(raw_val, (list, tuple, set)) else []
                                         
                                        conf.email.active_groups = selected
                                        if not save_global_config():
                                            logger.error('Failed to save recipient group selection')
                                            ui.notify('Failed to update recipients', color='negative')
                                            return
                                        if email_system:
                                            email_system.refresh_config()
                                         
                                        ui.notify('Recipients updated', color='positive', position='bottom-right')
                                        _update_apply_groups_state()
                                    except Exception as e:
                                        logger.error(f"Failed to apply groups: {e}")
                                        ui.notify('Failed to update recipients', color='negative')
                                    finally:
                                        _update_apply_groups_state()

                                _update_apply_groups_state()
                                
                        except Exception as e:
                            logger.error(f"Error building groups UI: {e}")

                ui.timer(0.0, lambda: schedule_bg(_build_groups_ui(), name='build_groups_ui'), once=True)

            # Periodically refresh groups options
            def _refresh_groups_ui() -> Any:
                nonlocal _last_groups_opts, groups_select
                try:
                    if groups_select is None:
                        return
                    conf = get_global_config()
                    if not conf or not getattr(conf, 'email', None):
                        return

                    new_opts = list(getattr(conf.email, 'groups', {}).keys())
                    if new_opts != _last_groups_opts:
                        configured_active = list(getattr(conf.email, 'active_groups', []) or [])
                        filtered_active = [g for g in configured_active if g in new_opts]
                        groups_select.value = filtered_active
                        groups_select.options = new_opts
                        groups_select.update()
                        _last_groups_opts = list(new_opts)
                    _update_apply_groups_state()
                except Exception:
                    logger.debug('Groups refresh check failed', exc_info=True)
                    return True
            ui.timer(5.0, _refresh_groups_ui)


    # ----------------------- Event-Logik ------------------------

    is_updating = False
    prev_unit: str = _get_duration_unit(duration_unit.value)

    def on_duration_input_change(_: Any) -> None:
        if is_updating:
            return
        if enable_limit.value:
            persist_settings()

    def on_duration_unit_change(e: Any) -> None:
        nonlocal is_updating, prev_unit
        old_unit = _get_duration_unit(prev_unit)
        new_unit = _get_duration_unit(duration_unit.value)

        current_value = _coerce_duration_value(duration_input.value, default=_get_unit_min_value(old_unit))
        seconds = max(MIN_DURATION_SECONDS, int(round(current_value * DURATION_UNIT_SECONDS[old_unit])))
        min_val = _get_unit_min_value(new_unit)
        new_value = max(_seconds_to_duration_value(seconds, new_unit), min_val)

        is_updating = True
        prev_unit = new_unit
        duration_input.value = _round_duration_value(new_value, new_unit)
        update_duration_ui(e)
        is_updating = False

        if enable_limit.value:
            persist_settings()

    def toggle_duration(_: Any) -> None:
        sync_duration_controls(bool(_safe_get_status().get('is_active', False)))
        update_duration_ui()
        persist_settings()

    def _stop_session() -> None:
        if measurement_controller is None:
            return
        current_status = _safe_get_status()
        if not bool(current_status.get('is_active', False)):
            _request_view_refresh(current_status)
            style_start_button(current_status)
            return
        stopped = measurement_controller.stop_session(reason='manual')
        if not stopped:
            logger.warning('Measurement stop request returned False for controller_id=%s', id(measurement_controller))
        current_status = _safe_get_status()
        _request_view_refresh(current_status)
        style_start_button(current_status)

    def _confirm_stop_session() -> None:
        stop_confirm_dialog.close()
        _stop_session()

    def start_stop(_: Any) -> None:
        nonlocal last_measurement
        if measurement_controller is None:
            logger.warning('Measurement start/stop requested but no controller is available')
            ui.notify('Measurement controller unavailable', color='negative')
            return
        status = _safe_get_status()
        session_active = bool(status.get('is_active', False))
        logger.info(
            'Measurement button clicked: action=%s controller_id=%s active=%s',
            'stop' if session_active else 'start',
            id(measurement_controller),
            session_active,
        )
        if status['is_active']:
            if confirm_stop:
                stop_confirm_dialog.open()
                return
            _stop_session()
        else:
            started = measurement_controller.start_session()
            if started:
                logger.info('Measurement start request succeeded for controller_id=%s', id(measurement_controller))
                started_status = _safe_get_status()
                last_measurement = _get_session_start_time(started_status) or datetime.now()
            else:
                logger.warning('Measurement start request returned False for controller_id=%s', id(measurement_controller))
        current_status = _safe_get_status()
        _request_view_refresh(current_status)
        style_start_button(current_status)


    def tick() -> None:
        """Per-client UI refresh. Session timeout is checked centrally by the controller."""
        if measurement_controller is None:
            return
        current_status = _safe_get_status()
        _request_view_refresh(current_status)
        style_start_button(current_status)


    # --------------------- Handler registrieren -----------------
    stop_confirm_dialog = ui.dialog()
    with stop_confirm_dialog:
        with ui.card().classes('items-start gap-3'):
            ui.label('Stop measurement?').classes('text-h6')
            ui.label('The current measurement session will be ended immediately.').classes('text-body2')
            with ui.row().classes('gap-2'):
                ui.button('Stop', on_click=_confirm_stop_session).props('color=primary')
                ui.button('Cancel', on_click=stop_confirm_dialog.close).props('color=negative')

    start_stop_btn.on('click', start_stop)
    enable_limit.on('update:model-value', toggle_duration)
     
    duration_input.on('blur', lambda e: persist_settings() if enable_limit.value else None)
    duration_input.on('keydown.enter', lambda e: persist_settings() if enable_limit.value else None)
    duration_unit.on('update:model-value', on_duration_unit_change)
    duration_input.on('update:model-value', on_duration_input_change)

    ui.timer(1.0, tick)

    sync_duration_controls(bool(initial_status.get('is_active', False)))
    update_duration_ui()
    _request_view_refresh(initial_status)
    style_start_button(initial_status)
