from nicegui import ui
from fastapi import Request
from datetime import datetime

from src.measurement import MeasurementController

def create_motion_status_element(camera, measurement_controller: MeasurementController | None = None):
    
    # ---------- interne Statusvariablen ----------
    motion_detected: bool = False           # Start: keine Bewegung
    last_changed: datetime = datetime.now() # Zeitstempel der letzten Änderung

    # ---------- UI ----------
    with ui.column().classes('items-start q-gutter-y-md'):

        # Karte mit fester, breiterem Layout ------------------------------
        with ui.card().style('width: 360px; min-height: 140px')\
                    .classes('shadow-2 q-pa-md'):
            with ui.row().classes('items-center q-gutter-x-md')\
                        .style('white-space: nowrap'):
                icon = ui.icon('highlight_off', color='red', size='2rem')
                status_label = ui.label('Keine Bewegung erkannt')\
                                .classes('text-h6')
            timestamp_label = ui.label('').classes('text-body2')\
                                .style('white-space: nowrap')

    def refresh_view() -> None:
        """Icon, Text und Zeitstempel aktualisieren."""
        if motion_detected:
            icon.props('name=check_circle color=green')
            status_label.text = 'Bewegung erkannt'
        else:
            icon.props('name=highlight_off color=red')
            status_label.text = 'Keine Bewegung erkannt'
        timestamp_label.text = f'Letzte Änderung: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'
    
    def _motion_callback(frame, result):
        nonlocal motion_detected, last_changed

        if result.motion_detected != motion_detected:
            motion_detected = result.motion_detected
            last_changed = datetime.fromtimestamp(result.timestamp)
            refresh_view()
        if measurement_controller is not None:
            measurement_controller.on_motion_detected(result)

    camera.enable_motion_detection(_motion_callback)
    # ---------- REST-Endpunkt für Dein Analyse-Skript -------------------
    @ui.page('/update')
    async def update(request: Request):
        nonlocal motion_detected, last_changed

        new_motion = request.query_params.get('motion', '0') == '1'
        if new_motion != motion_detected:
            motion_detected = new_motion
            last_changed = datetime.now()
            refresh_view()
        return 'ok'
