from datetime import datetime, timedelta
from nicegui import ui

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
        update_view.refresh()

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
        motion_label.text = 'Bewegung erkannt' if motion else 'Keine Bewegung'

        # Alert-Info anzeigen
        if status.get('recent_motion_detected'):
            alert_label.text = 'Kein Alarm notwendig'
            alert_label.classes(remove='text-negative text-grey', add='text-positive')
        else:
            countdown = status.get('alert_countdown')
            if countdown is not None:
                alert_label.text = f'Alarm in {fmt(timedelta(seconds=countdown))}'
                alert_label.classes(remove='text-positive text-grey', add='text-negative')
            else:
                alert_label.text = ''
                alert_label.classes(remove='text-negative text-positive', add='text-grey')

        # --- letzte Messung ------------
        last_label.text = (
            f'Letzte Messung: {last_measurement.strftime("%d.%m.%Y %H:%M:%S")}'
            if last_measurement else 'Letzte Messung: –'
        )


    def style_start_button() -> None:
        if measurement_controller.get_session_status()['is_active']:
            start_stop_btn.text = 'Stopp'
            start_stop_btn.icon = 'stop'
            start_stop_btn.props('color=negative')
        else:
            start_stop_btn.text = 'Start'
            start_stop_btn.icon = 'play_arrow'
            start_stop_btn.props('color=positive')

    
    def persist_settings() -> None:
        """Persist measurement duration settings to the config."""
        cfg = config.measurement
        cfg.session_timeout_minutes = (
            max(1, int(duration_input.value / 60)) if enable_limit.value else 0
        )
        save_config(config)
        measurement_controller.config = cfg



    # -------------------------- UI ------------------------------
    with ui.card().style('width: 380px'):
        ui.label('Messungs-Überwachung').classes('text-h5 text-bold mb-2')

        start_stop_btn = ui.button('Start', icon='play_arrow', color='positive') \
            .classes('q-mb-md')

        with ui.row().classes('items-center q-gutter-sm q-mb-sm'):
            enable_limit = ui.checkbox(
                'max. Dauer', value=config.measurement.session_timeout_minutes > 0
            )
            duration_input = ui.number(
                label='Dauer [s]',
                value=(
                    config.measurement.session_timeout_minutes * 60
                    if config.measurement.session_timeout_minutes > 0
                    else 60
                ),
                min=1,
                format='%.0f',
            ).props('dense outlined').style('width:120px')
            if enable_limit.value:
                duration_input.enable()
            else:
                duration_input.disable()

        timer_label = ui.label('-').classes('text-subtitle1 q-mb-xs')

        # Fortschritts-Balken + Prozent
        with ui.row().classes('items-center q-mb-xs') as progress_row:
            progress = ui.linear_progress(value=0.0, color='accent').style('flex:1')
            percent_label = ui.label('0 %').classes('text-caption')
        progress_row.visible = False

        motion_label = ui.label('Keine Bewegung').classes('text-caption text-grey q-mb-xs')
        alert_label = ui.label('').classes('text-caption q-mb-xs')
        last_label = ui.label('Letzte Messung: –').classes('text-caption text-grey')


    # ----------------------- Event-Logik ------------------------
    def toggle_duration(_):
        if not measurement_controller.get_session_status()['is_active']:
            duration_input.enable() if enable_limit.value else duration_input.disable()


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
        update_view()
        style_start_button()


    # --------------------- Handler registrieren -----------------
    start_stop_btn.on('click', start_stop)
    enable_limit.on('update:model-value', toggle_duration)
    enable_limit.on('update:model-value', lambda e: persist_settings())
    duration_input.on('update:model-value', lambda e: persist_settings() if enable_limit.value else None)
    ui.timer(1.0, tick)

    persist_settings()
    style_start_button()
