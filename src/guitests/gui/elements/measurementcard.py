from datetime import datetime, timedelta
from nicegui import ui

def create_measurement_card():
    # ------------------------- Zustände -------------------------
    running = False
    start_time: datetime | None = None
    max_duration: timedelta | None = None
    last_measurement: datetime | None = None


    # --------------------- Hilfsfunktionen ----------------------
    def fmt(td: timedelta) -> str:
        secs = int(td.total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f'{h:02}:{m:02}:{s:02}'


    def lock_limit_controls(lock: bool) -> None:
        (enable_limit.disable() if lock else enable_limit.enable())
        (duration_input.disable() if lock else
        (duration_input.enable() if enable_limit.value else duration_input.disable()))


    def update_view() -> None:
        """Aktualisiert Laufzeit, Fortschritt, Labels."""
        if running and start_time:
            elapsed = datetime.now() - start_time

            # --- Timer-Label -------------
            if max_duration:
                timer_label.text = f'{fmt(elapsed)}/{fmt(max_duration)}'
                ratio = min(
                    elapsed.total_seconds() / max_duration.total_seconds(), 1.0
                )
                progress.value = ratio
                percent_label.text = f'{ratio*100:5.1f} %'   # schmale Leerstelle
                progress_row.visible = True
            else:
                timer_label.text = fmt(elapsed)
                progress_row.visible = False
        else:
            timer_label.text = '–'
            progress_row.visible = False

        # --- letzte Messung ------------
        last_label.text = (
            f'Letzte Messung: {last_measurement.strftime("%d.%m.%Y %H:%M:%S")}'
            if last_measurement else 'Letzte Messung: –'
        )


    def style_start_button() -> None:
        if running:
            start_stop_btn.text = 'Stopp'
            start_stop_btn.icon = 'stop'
            start_stop_btn.props('color=negative')
        else:
            start_stop_btn.text = 'Start'
            start_stop_btn.icon = 'play_arrow'
            start_stop_btn.props('color=positive')


    def finish_measurement() -> None:
        """Beendet die Messung (Auto- oder manuell)."""
        global running, start_time, max_duration
        running = False
        start_time = None
        max_duration = None
        lock_limit_controls(False)
        style_start_button()
        update_view()


    # -------------------------- UI ------------------------------
    with ui.card().style('width: 380px'):
        ui.label('Messungs-Überwachung').classes('text-h5 text-bold mb-2')

        start_stop_btn = ui.button('Start', icon='play_arrow', color='positive') \
            .classes('q-mb-md')

        with ui.row().classes('items-center q-gutter-sm q-mb-sm'):
            enable_limit = ui.checkbox('max. Dauer')
            duration_input = ui.number(
                label='Dauer [s]', value=60, min=1, format='%.0f'
            ).props('dense outlined').style('width:120px')
            duration_input.disable()

        timer_label = ui.label('-').classes('text-subtitle1 q-mb-xs')

        # Fortschritts-Balken + Prozent
        with ui.row().classes('items-center q-mb-xs') as progress_row:
            progress = ui.linear_progress(value=0.0, color='accent').style('flex:1')
            percent_label = ui.label('0 %').classes('text-caption')
        progress_row.visible = False

        last_label = ui.label('Letzte Messung: –').classes('text-caption text-grey')


    # ----------------------- Event-Logik ------------------------
    def toggle_duration(_):
        if not running:
            duration_input.enable() if enable_limit.value else duration_input.disable()


    def start_stop(_):
        """Startet oder stoppt die Messung manuell."""
        global running, start_time, max_duration, last_measurement
        if running:
            finish_measurement()
        else:
            running = True
            start_time = datetime.now()
            last_measurement = start_time
            max_duration = (
                timedelta(seconds=duration_input.value) if enable_limit.value else None
            )
            lock_limit_controls(True)
            style_start_button()
            update_view()


    def tick():
        """Sekündlicher Takt: Auto-Stopp & Live-Update."""
        if running and start_time and max_duration:
            if datetime.now() - start_time >= max_duration:
                finish_measurement()
                return
        if running:
            update_view()


    # --------------------- Handler registrieren -----------------
    start_stop_btn.on('click', start_stop)
    enable_limit.on('update:model-value', toggle_duration)
    ui.timer(1.0, tick)

    style_start_button()
