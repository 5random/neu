from datetime import datetime, timedelta
from nicegui import ui, background_tasks

from src.alert import AlertSystem
from src.config import load_config, save_config
from src.measurement import MeasurementController
from src.cam.camera import Camera

def create_measurement_card(measurement_controller: MeasurementController | None = None, camera: Camera | None = None):
    
    config = load_config()
    if measurement_controller is None:
        alert_system = AlertSystem(config.email, config.measurement, config)
        measurement_controller = MeasurementController(config.measurement, alert_system)

    if camera and hasattr(camera, 'enable_motion_detection'):
        camera.enable_motion_detection(lambda frame, motion_result: measurement_controller.on_motion_detected(motion_result))
    # ------------------------- Zustände -------------------------

    last_measurement: datetime | None = None

    def on_motion(_):
        async def _refresh():
            update_view.refresh()
        background_tasks.create_lazy(_refresh(), name='refresh_view')

    measurement_controller.register_motion_callback(on_motion)
    
    # --------------------- Hilfsfunktionen ----------------------
    def fmt(td: timedelta) -> str:
        secs = int(td.total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f'{h:02}:{m:02}:{s:02}'

    @ui.refreshable
    def update_view() -> None:
        """Aktualisiert Laufzeit, Fortschritt, Labels."""
        status = measurement_controller.get_session_status()

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
        
        # Motion-Status anzeigen
        motion = status.get('recent_motion_detected', False)
        motion_label.text = 'motion detected' if motion else 'no motion detected'

        # Alert-Info anzeigen
        if status.get('recent_motion_detected'):
            alert_label.text = 'No alarm necessary'
            alert_label.classes(remove='text-negative text-grey', add='text-positive')
        else:
            countdown = status.get('alert_countdown')
            if countdown is not None:
                alert_label.text = f'Alarm triggerd in {fmt(timedelta(seconds=countdown))}'
                alert_label.classes(remove='text-positive text-grey', add='text-negative')
            else:
                alert_label.text = ''
                alert_label.classes(remove='text-negative text-positive', add='text-grey')

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
        if duration_input.value is None:
            return
        
        if not enable_limit.value:
            # Limit deaktiviert ⇒ 0 Minuten speichern
            config.measurement.session_timeout_minutes = 0
            save_config(config)
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
        save_config(config)
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

            min_alert_sec = config.measurement.alert_delay_seconds          # z. B. 300 s
            min_alert_min = (min_alert_sec + 59) // 60 

            duration_input = ui.number(
                label='Duration',
                value=(
                    config.measurement.session_timeout_minutes * 60
                    if config.measurement.session_timeout_minutes > 0
                    else 60
                ),
                min=MIN_BASE_SEC,  # Minimum 5 Minuten
                format='%.0f',
            ).props('dense outlined').style('min-width:80').tooltip(
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
            duration_unit.on('update:model-value', update_duration_ui)


        timer_label = ui.label('-').classes('text-subtitle1 q-mb-xs')

        # Fortschrittsbalken und Prozent
        with ui.row().classes('w-full flex-nowrap').style("align-self:flex-start; flex-direction:row; display:flex; flex-wrap:nowrap; gap:8px;") as progress_row:
            progress = ui.linear_progress(value=0.0, color='accent', show_value=False).classes('w-8/12 h-4')
            percent_label = ui.label('0 %').classes('text-caption text-right').style('flex-wrap:nowrap;')
        progress_row.visible = False

        with ui.row().classes('w-full items-center q-gutter-sm').style("flex-wrap:wrap; gap:8px; align-self:flex-start; flex-direction:row; justify-content:start;"):
            motion_label = ui.label('No motion detected').classes('text-caption text-grey q-mb-xs').style('width: 140px;')
            alert_label = ui.label('').classes('text-caption q-mb-xs').style('width: 160px;')
            last_label = ui.label('Last measurement: –').classes('text-caption text-grey')


    # ----------------------- Event-Logik ------------------------


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
            measurement_controller.stop_session()
        else:
            measurement_controller.start_session()
            last_measurement = datetime.now()
        update_view()
        style_start_button()


    def tick():
        """Sekündlicher Takt: Auto-Stopp & Live-Update."""
        measurement_controller.check_session_timeout()
        status = measurement_controller.get_session_status()
        update_view.refresh()
        style_start_button()


    # --------------------- Handler registrieren -----------------
    start_stop_btn.on('click', start_stop)
    enable_limit.on('update:model-value', toggle_duration)
    enable_limit.on('update:model-value', lambda e: persist_settings())
     
    duration_input.on('blur', lambda e:
    persist_settings() if enable_limit.value else None)
    duration_input.on('keydown.enter', lambda e:
    persist_settings() if enable_limit.value else None)
    ui.timer(1.0, tick)

    persist_settings()
    style_start_button()
