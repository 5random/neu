from __future__ import annotations
from datetime import datetime, timedelta
import asyncio
from nicegui import ui, background_tasks

from src.notify import EMailSystem
from src.config import get_global_config, save_global_config, get_logger
from src.cam.camera import Camera
from src.gui.util import schedule_bg
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from src.measurement import MeasurementController

logger = get_logger('gui.measurement')

def create_measurement_card(
    measurement_controller: Optional['MeasurementController'] = None,
    camera: Camera | None = None,
    email_system: EMailSystem | None = None,
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
        if email_system is None:
            email_system = EMailSystem(config.email, config.measurement, config)
        measurement_controller = MeasurementController(config.measurement, email_system, camera)
    else:
        if email_system is not None and measurement_controller.email_system != email_system:
            measurement_controller.email_system = email_system

    # ------------------------- Zustände -------------------------

    last_measurement: datetime | None = None

    def on_motion(_: Any) -> None:
        async def _refresh() -> None:
            update_view.refresh()
        schedule_bg(_refresh(), name='refresh_view')

    if measurement_controller is not None:
        measurement_controller.register_motion_callback(on_motion)

    # ------------------- Hilfsfunktionen ----------------------
    def fmt(td: timedelta) -> str:
        secs = int(td.total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f'{h:02}:{m:02}:{s:02}'

    @ui.refreshable
    def update_view() -> None:
        """Aktualisiert Laufzeit, Fortschritt, Labels."""
        if measurement_controller is None:
            return
        status = measurement_controller.get_session_status()
        config = get_global_config()

        elapsed = status['duration']
        session_active = status['is_active']
        session_max = (timedelta(minutes=status['session_timeout_minutes']) if status['session_timeout_minutes'] > 0
                       else None)

        if session_active and elapsed:
            if session_max:
                timer_label.text = f'{fmt(elapsed)} / {fmt(session_max)}'
                ratio = min(elapsed.total_seconds() / session_max.total_seconds(), 1.0)
                progress.value = ratio
                percent_label.text = f'{ratio*100:5.1f} %'
                progress_row.visible = True
            else:
                timer_label.text = fmt(elapsed)
                progress_row.visible = False
        else:
            timer_label.text = '-'
            progress_row.visible = False
        
        camera_status = camera.is_camera_available() if camera else False

        if camera_status:
            # Motion-Status anzeigen
            motion = status.get('recent_motion_detected', False)
            motion_label.text = 'Motion detected' if motion else 'No motion'
            motion_label.classes(remove='text-negative text-warning text-grey', add='text-primary' if motion else 'text-grey')
        else:
            motion_label.text = 'Camera unavailable'
            motion_label.classes(remove='text-grey text-primary', add='text-warning')

        # Alert-Info anzeigen
        if camera_status and status.get('recent_motion_detected'):
            alert_label.text = 'Safe (Motion)'
            alert_label.classes(remove='text-negative text-grey text-warning', add='text-positive')
        else:
            countdown = status.get('alert_countdown')
            if countdown is not None and countdown > 0:
                alert_label.text = f'Alert in {fmt(timedelta(seconds=countdown))}'
                alert_label.classes(remove='text-positive text-grey text-warning', add='text-negative')
            elif not camera_status:
                alert_label.text = 'Check Camera'
                alert_label.classes(remove='text-negative text-positive text-grey', add='text-warning')
            else:
                alert_label.text = 'Monitoring...'
                alert_label.classes(remove='text-negative text-positive text-warning', add='text-grey')

        # --- letzte Messung ------------
        last_label.text = (
            f'Last: {last_measurement.strftime("%H:%M:%S")}'
            if last_measurement else 'Last: -'
        )


    def style_start_button() -> None:
        if measurement_controller is None:
            return
        if measurement_controller.get_session_status()['is_active']:
            start_stop_btn.icon = 'stop'
            start_stop_btn.props('color=negative')
            start_stop_btn.tooltip('Stop Session')
        else:
            start_stop_btn.icon = 'play_arrow'
            start_stop_btn.props('color=positive')
            start_stop_btn.tooltip('Start Session')

    
    # ---------------- Konstanten ----------------
    MIN_BASE_SEC = max(config.measurement.alert_delay_seconds, 5 * 60)  # >= 5 min
    MIN_HOUR_SEC = 3600  # 1 Stunde in Sekunden

    # ---------------- UI-Update -----------------

    def update_duration_ui(_: Any = None) -> None:
        """Aktualisiert die UI-Elemente für die Dauer."""
        unit = duration_unit.value if duration_unit.value in {'s', 'min', 'h'} else 's'
        mult = {'s': 1, 'min': 60, 'h': 3600}[unit]
        min_val = MIN_BASE_SEC / mult
        if unit == 'h':
            min_val = 1

        duration_input.label = f'Duration'
        duration_input.props(f'suffix="{unit}" min={min_val}')
        duration_input.min = float(min_val)
        if duration_input.value is not None and duration_input.value < min_val:
            duration_input.value = min_val

    def persist_settings() -> None:
        """Persist measurement duration settings to the config."""
        config = get_global_config()
        if not config or duration_input.value is None:
            return
        
        if not enable_limit.value:
            # Limit deaktiviert ⇒ 0 Minuten speichern
            config.measurement.session_timeout_minutes = 0
            save_global_config()
            if measurement_controller is not None:
                measurement_controller.config = config.measurement
            return

        unit = duration_unit.value if duration_unit.value in {'s', 'min', 'h'} else 's'
        mult = {'s': 1, 'min': 60, 'h': 3600}[unit]
        seconds = int(duration_input.value * mult)
        if unit == 'h':
            seconds = max(seconds, MIN_HOUR_SEC)
        seconds = max(seconds, MIN_BASE_SEC)  # Minimum Dauer einhalten

        cfg = config.measurement
        cfg.session_timeout_minutes = max(5, seconds // 60)  # Minimum 5 Minuten
        save_global_config()
        if measurement_controller is not None:
            measurement_controller.config = cfg


    # -------------------------- UI ------------------------------
    # Make the measurement card expand to use available vertical space in its column
    with ui.card().classes('w-full flex-1 p-4').style('align-self:stretch; min-height:0;'):
        # Header
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label('Measurement').classes('text-h6 font-semibold')
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
                        'Max Duration', value=config.measurement.session_timeout_minutes > 0
                    ).props('dense').tooltip('Enable automatic session timeout')
                
                with ui.row().classes('items-center gap-2 no-wrap'):
                    duration_input = ui.number(
                        value=(
                            config.measurement.session_timeout_minutes * 60
                            if config.measurement.session_timeout_minutes > 0
                            else 60
                        ),
                        min=MIN_BASE_SEC,
                        format='%.0f',
                    ).props('dense outlined hide-bottom-space').classes('w-24')

                    duration_unit = ui.select(
                        options=['s', 'min', 'h'],
                        value='s',
                    ).props('dense outlined options-dense').classes('w-20')

            # Respect enable/disable state
            if enable_limit.value:
                duration_input.enable()
                duration_unit.enable()
            else:
                duration_input.disable()
                duration_unit.disable()

            update_duration_ui()

        ui.separator().classes('mb-4')

        # Status Display (Timer & Progress)
        with ui.column().classes('w-full items-center gap-1 mb-4'):
            timer_label = ui.label('-').classes('text-h4 font-mono font-bold text-primary')
            
            with ui.row().classes('w-full items-center gap-2 no-wrap') as progress_row:
                progress = ui.linear_progress(value=0.0, color='primary', show_value=False).classes('flex-1 h-2 rounded')
                percent_label = ui.label('0 %').classes('text-caption font-mono min-w-[3rem] text-right')
            progress_row.visible = False

        ui.separator().classes('mb-4')

        # Info Grid (Motion, Alert, Last)
        with ui.grid(columns=2).classes('w-full gap-x-4 gap-y-2'):
            ui.label('Motion:').classes('text-caption font-bold text-grey-7')
            motion_label = ui.label('No motion').classes('text-caption text-grey')
            
            ui.label('Status:').classes('text-caption font-bold text-grey-7')
            alert_label = ui.label('Monitoring...').classes('text-caption text-grey')
            
            ui.label('Last Run:').classes('text-caption font-bold text-grey-7')
            last_label = ui.label('-').classes('text-caption text-grey')

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
            ui.label('Active Recipients').classes('text-caption font-bold text-grey-7')
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
                                    save_global_config()
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
                if groups_select is None: return
                conf = get_global_config()
                if not conf or not getattr(conf, 'email', None): return

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
                return True
        ui.timer(5.0, _refresh_groups_ui)


    # ----------------------- Event-Logik ------------------------

    is_updating = False
    prev_unit: str = duration_unit.value if duration_unit.value in {'s', 'min', 'h'} else 's'

    def on_duration_input_change(_: Any) -> None:
        if is_updating:
            return
        if enable_limit.value:
            persist_settings()

    def on_duration_unit_change(e: Any) -> None:
        nonlocal is_updating, prev_unit
        units = {'s': 1, 'min': 60, 'h': 3600}
        old_unit = prev_unit if prev_unit in units else 's'
        new_unit = duration_unit.value if duration_unit.value in units else 's'

        seconds = 0.0
        if duration_input.value is not None:
            try:
                seconds = float(duration_input.value) * units[old_unit]
            except Exception:
                seconds = 0.0

        min_val = (MIN_BASE_SEC / units[new_unit])
        if new_unit == 'h':
            min_val = 1

        new_value = seconds / units[new_unit] if seconds > 0 else min_val
        if new_value < min_val:
            new_value = min_val

        is_updating = True
        prev_unit = new_unit
        duration_input.value = new_value
        update_duration_ui(e)
        is_updating = False

        if enable_limit.value:
            persist_settings()

    def toggle_duration(_: Any) -> None:
        if not measurement_controller.get_session_status()['is_active']:
            if enable_limit.value:
                duration_input.enable()
                duration_unit.enable()
            else:
                duration_input.disable()
                duration_unit.disable()


    def start_stop(_: Any) -> None:
        nonlocal last_measurement
        status = measurement_controller.get_session_status()
        if status['is_active']:
            measurement_controller.stop_session(reason='manual')
        else:
            measurement_controller.start_session()
            last_measurement = datetime.now()
        update_view()
        style_start_button()


    def tick() -> None:
        try:
            measurement_controller.check_session_timeout()
        except Exception:
            logger.exception('measurement tick failed')
        update_view.refresh()
        style_start_button()


    # --------------------- Handler registrieren -----------------
    start_stop_btn.on('click', start_stop)
    enable_limit.on('update:model-value', toggle_duration)
    enable_limit.on('update:model-value', lambda e: persist_settings())
     
    duration_input.on('blur', lambda e: persist_settings() if enable_limit.value else None)
    duration_input.on('keydown.enter', lambda e: persist_settings() if enable_limit.value else None)
    duration_unit.on('update:model-value', on_duration_unit_change)
    duration_input.on('update:model-value', on_duration_input_change)

    ui.timer(1.0, tick)

    persist_settings()
    style_start_button()
