from datetime import datetime, timedelta
import asyncio
from nicegui import ui, background_tasks

from src.notify import EMailSystem
from src.config import get_global_config, save_global_config, get_logger
from src.measurement import MeasurementController
from src.cam.camera import Camera
from src.gui.util import schedule_bg

logger = get_logger('gui.measurement')

def create_measurement_card(
    measurement_controller: MeasurementController | None = None,
    camera: Camera | None = None,
    email_system: EMailSystem | None = None,
    **kwargs,
) -> None:
    # Back-compat: accept old keyword 'alert_system'
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

    def on_motion(_):
        async def _refresh():
            update_view.refresh()
        schedule_bg(_refresh(), name='refresh_view')

    measurement_controller.register_motion_callback(on_motion)

    # ------------------- Hilfsfunktionen ----------------------
    def fmt(td: timedelta) -> str:
        secs = int(td.total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f'{h:02}:{m:02}:{s:02}'

    @ui.refreshable
    def update_view() -> None:
        """Aktualisiert Laufzeit, Fortschritt, Labels."""
        status = measurement_controller.get_session_status()
        config = get_global_config()

        elapsed = status['duration']
        session_active = status['is_active']
        session_max = (timedelta(minutes=status['session_timeout_minutes']) if status['session_timeout_minutes'] > 0
                       else None)

        if session_active and elapsed:
            if session_max:
                timer_label.text = f'{fmt(elapsed)}/{fmt(session_max)}'
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
            motion_label.text = 'motion detected' if motion else 'no motion detected'
            motion_label.classes(remove='text-negative text-warning', add='text-grey')
        else:
            motion_label.text = 'camera not available'
            motion_label.classes(remove='text-grey', add='text-warning')

        # Alert-Info anzeigen
        if camera_status and status.get('recent_motion_detected'):
            alert_label.text = 'No alarm necessary'
            alert_label.classes(remove='text-negative text-grey', add='text-positive')
        else:
            countdown = status.get('alert_countdown')
            if countdown is not None and countdown > 0:
                alert_label.text = f'Alarm triggered in {fmt(timedelta(seconds=countdown))}'
                alert_label.classes(remove='text-positive text-grey', add='text-negative')
            elif not camera_status:
                alert_label.text = 'Camera not available'
                alert_label.classes(remove='text-negative text-positive text-grey', add='text-warning')
            else:
                alert_label.text = ''
                alert_label.classes(remove='text-negative text-positive text-warning', add='text-grey')

        # --- letzte Messung ------------
        last_label.text = (
            f'last measurement: {last_measurement.strftime("%d.%m.%Y %H:%M:%S")}'
            if last_measurement else 'Last measurement: -'
        )


    def style_start_button() -> None:
        if measurement_controller.get_session_status()['is_active']:
            start_stop_btn.icon = 'stop'
            start_stop_btn.props('color=negative')
        else:
            start_stop_btn.icon = 'play_arrow'
            start_stop_btn.props('color=positive')

    
    # ---------------- Konstanten ----------------
    MIN_BASE_SEC = max(config.measurement.alert_delay_seconds, 5 * 60)  # >= 5 min
    MIN_HOUR_SEC = 3600  # 1 Stunde in Sekunden

    # ---------------- UI-Update -----------------

    def update_duration_ui(_=None):
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
        measurement_controller.config = cfg


    # -------------------------- UI ------------------------------
    with ui.card().classes('w-full h-full').style('align-self:stretch;'):
        ui.label('Measurement Monitoring').classes('text-h6 font-semibold mb-2')

        with ui.row().classes('items-center q-gutter-sm q-mb-sm'):
            start_stop_btn = ui.button(icon='play_arrow', color='positive').props('round') \
                .tooltip('Start or stop the measurement session')
            
            ui.element('div').classes('w-px h-8 bg-gray-300 mx-2')

            enable_limit = ui.checkbox(
                'max. Duration', value=config.measurement.session_timeout_minutes > 0
            ).tooltip('toggle maximum measurement duration')

            duration_input = ui.number(
                label='Duration',
                value=(
                    config.measurement.session_timeout_minutes * 60
                    if config.measurement.session_timeout_minutes > 0
                    else 60
                ),
                min=MIN_BASE_SEC,  # Minimum 5 Minuten
                format='%.0f',
            ).props('dense outlined').style('min-width:80px;').tooltip(
                'Min. duration of the measurement is 5 minutes (300 seconds).'
            )

            if enable_limit.value:
                duration_input.enable()
            else:
                duration_input.disable()
            
            duration_unit = ui.select(
                options=['s', 'min', 'h'],
                value='s',
                label='Unit',
            ).props('dense outlined').style('min-width:80px;').tooltip(
                'Select the unit for the duration with seconds (s), minutes (min), or hours (h).'
            )

            update_duration_ui()

            if enable_limit.value:
                duration_unit.enable()
            else:
                duration_unit.disable()

        timer_label = ui.label('-').classes('text-subtitle1 q-mb-xs')

        # --- Active recipient groups selection ---
        groups_select = None  # will be created after async fetch
        _last_groups_opts: list[str] = []  # initial empty snapshot
        groups_build_lock = asyncio.Lock()  # prevent concurrent UI builds
        apply_btn = None  # apply button reference for enable/disable state

        def _update_apply_groups_state() -> None:
            """Enable Apply only if selection differs from currently active groups.
            If selection equals the active groups from config, disable the button."""
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
            except Exception as exc:
                logger.error('Failed to update apply button state: %s', exc, exc_info=True)
                try:
                    if apply_btn is not None:
                        apply_btn.disable()
                except Exception:
                    pass

        with ui.row().classes('items-center q-gutter-sm q-mb-sm') as groups_row:
            ui.label('Active recipient groups:').classes('text-caption text-grey')
            loading_lbl = ui.label('Loading groups...').classes('text-caption text-grey')

            async def _build_groups_ui():
                nonlocal groups_select, _last_groups_opts, apply_btn
                async with groups_build_lock:
                    # 1) fetch config off the UI thread
                    cfg = await asyncio.to_thread(get_global_config)
                    opts = list(getattr(cfg.email, 'groups', {}).keys()) if cfg and cfg.email else []
                    vals = list(getattr(cfg.email, 'active_groups', [])) if cfg and cfg.email else []
                    # snapshot for change detection
                    _last_groups_opts = list(opts)
                    # 2) apply UI changes in one protected block
                    try:
                        with groups_row:
                            try:
                                loading_lbl.delete()
                            except Exception:
                                pass
                            # create or replace the select
                            groups_select = ui.select(
                                options=opts,
                                value=vals,
                                multiple=True,
                                label='Groups'
                            ).props('dense outlined').classes('min-w-[260px]')

                            # Apply button with robust handler (validation, errors, and UI feedback)
                            apply_btn = ui.button(icon='done', color='primary').props('round').tooltip('Apply active groups for sending')

                            # Update Apply button state on selection changes
                            def _on_groups_change(_=None):
                                _update_apply_groups_state()
                            groups_select.on('update:model-value', _on_groups_change)

                            def apply_groups(_=None):
                                nonlocal apply_btn
                                # Disable button during processing for clear user feedback
                                try:
                                    if apply_btn is not None:
                                        apply_btn.disable()
                                except Exception:
                                    pass
                                try:
                                    conf = get_global_config()
                                    if not conf or not getattr(conf, 'email', None):
                                        ui.notify('Configuration not available', color='warning', position='bottom-right')
                                        return
                                    if groups_select is None:
                                        ui.notify('Groups not ready yet', color='warning', position='bottom-right')
                                        return

                                    raw_val = getattr(groups_select, 'value', [])
                                    if raw_val is None:
                                        selected: list[str] = []
                                    elif isinstance(raw_val, (list, tuple, set)):
                                        selected = list(raw_val)
                                    else:
                                        raise TypeError(f'Unexpected selection type: {type(raw_val).__name__}')

                                    # Only persist after validation succeeds
                                    conf.email.active_groups = selected
                                    save_global_config()
                                    if email_system:
                                        email_system.refresh_config()

                                    ui.notify('Active groups applied', color='positive', position='bottom-right')
                                    # After applying, selection equals active groups; update button state
                                    _update_apply_groups_state()
                                except Exception as exc:
                                    logger.error('Failed to apply active groups: %s', exc, exc_info=True)
                                    ui.notify(f'Failed to apply active groups: {exc}', color='negative', position='bottom-right')
                                finally:
                                    # Recompute state so button remains disabled when selection == active groups
                                    try:
                                        _update_apply_groups_state()
                                    except Exception:
                                        pass

                            apply_btn.on('click', apply_groups)
                            # Initialize button state after building controls
                            _update_apply_groups_state()
                    except Exception as exc:
                        # Surface errors to logs and UI, but don't crash the page
                        logger.error('Failed to build groups UI: %s', exc, exc_info=True)
                        ui.notify('Error loading groups UI', color='negative', position='bottom-right')

            # Use create_lazy to schedule building UI groups safely on the NiceGUI loop
            ui.timer(0.0, lambda: schedule_bg(_build_groups_ui(), name='build_groups_ui'), once=True)

        # Periodically refresh groups options if the config changes elsewhere
        def _refresh_groups_ui():
            nonlocal _last_groups_opts, groups_select
            try:
                # Ensure the select exists and has required attributes
                if groups_select is None:
                    return
                if not all(hasattr(groups_select, attr) for attr in ('value', 'options', 'update')):
                    return

                conf = get_global_config()
                if not conf or not getattr(conf, 'email', None):
                    return

                new_opts = list(getattr(conf.email, 'groups', {}).keys())
                if new_opts != _last_groups_opts:
                    # Compute intersection of configured active groups and available options
                    configured_active = list(getattr(conf.email, 'active_groups', []) or [])
                    filtered_active = [g for g in configured_active if g in new_opts]
                    # Apply filtered value first, then options, then update
                    groups_select.value = filtered_active
                    groups_select.options = new_opts
                    groups_select.update()
                    # Update the last options snapshot after successful UI apply
                    _last_groups_opts = list(new_opts)
                # Keep Apply button state in sync with current selection vs active config
                _update_apply_groups_state()
            except Exception as exc:
                logger.error('Groups UI refresh failed: %s', exc, exc_info=True)
                # Return True to indicate handled error and allow timer to continue
                return True
        ui.timer(5.0, _refresh_groups_ui)

        # Fortschrittsbalken und Prozent
        with ui.row().classes('w-full flex-nowrap').style("align-self:flex-start; flex-direction:row; display:flex; flex-wrap:nowrap; gap:8px;") as progress_row:
            progress = ui.linear_progress(value=0.0, color='accent', show_value=False).classes('w-8/12 h-4')
            percent_label = ui.label('0 %').classes('text-caption text-right').style('flex-wrap:nowrap;')
        progress_row.visible = False

        with ui.row().classes('w-full items-center q-gutter-sm').style("flex-wrap:wrap; gap:8px; align-self:flex-start; flex-direction:row; justify-content:start;"):
            motion_label = ui.label('No motion detected').classes('text-caption text-grey q-mb-xs').style('width: 140px;')
            alert_label = ui.label('').classes('text-caption q-mb-xs').style('width: 160px;')
            last_label = ui.label('Last measurement: -').classes('text-caption text-grey')


    # ----------------------- Event-Logik ------------------------

    is_updating = False
    prev_unit: str = duration_unit.value if duration_unit.value in {'s', 'min', 'h'} else 's'

    def on_duration_input_change(_):
        if is_updating:
            return
        if enable_limit.value:
            persist_settings()

    def on_duration_unit_change(e):
        nonlocal is_updating, prev_unit

        # Alte und neue Einheit bestimmen
        units = {'s': 1, 'min': 60, 'h': 3600}
        old_unit = prev_unit if prev_unit in units else 's'
        new_unit = duration_unit.value if duration_unit.value in units else 's'

        # Numerischen Wert in Sekunden umrechnen
        seconds = 0.0
        if duration_input.value is not None:
            try:
                seconds = float(duration_input.value) * units[old_unit]
            except Exception:
                seconds = 0.0

        # Min-Wert der neuen Einheit berechnen (wie in update_duration_ui)
        min_val = (MIN_BASE_SEC / units[new_unit])
        if new_unit == 'h':
            min_val = 1

        # Wert in die neue Einheit konvertieren und Mindestwert beachten
        new_value = seconds / units[new_unit] if seconds > 0 else min_val
        if new_value < min_val:
            new_value = min_val

        # UI aktualisieren ohne doppeltes Persistieren
        is_updating = True
        prev_unit = new_unit
        duration_input.value = new_value
        update_duration_ui(e)
        is_updating = False

        if enable_limit.value:
            persist_settings()

    def toggle_duration(_):
        if not measurement_controller.get_session_status()['is_active']:
            if enable_limit.value:
                duration_input.enable()
                duration_unit.enable()
            else:
                duration_input.disable()
                duration_unit.disable()


    def start_stop(_):
        """Startet oder stoppt die Messung manuell."""
        nonlocal last_measurement
        status = measurement_controller.get_session_status()
        if status['is_active']:
            # Messung läuft, also stoppen
            measurement_controller.stop_session(reason='manual')
        else:
            measurement_controller.start_session()
            last_measurement = datetime.now()
        update_view()
        style_start_button()


    def tick():
        """Sekündlicher Takt: Auto-Stopp & Live-Update."""
        measurement_controller.check_session_timeout()
        if measurement_controller.is_session_active:
            measurement_controller._check_alert_trigger()
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
