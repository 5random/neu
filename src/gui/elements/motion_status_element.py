from nicegui import ui
from fastapi import Request
from datetime import datetime

from src.measurement import MeasurementController

def create_motion_status_element(camera, measurement_controller: MeasurementController | None = None):
    if camera is None:
        # Fallback-UI ohne Kamera-Integration
        with ui.card().classes('w-full h-full shadow-2 q-pa-md').style('align-self:stretch;'):
            ui.label('Motion Detection Status').classes('text-h6 font-semibold mb-2')
            ui.label('Camera not available - motion detection disabled').classes('text-warning')
        return
    
    # ---------- interne Statusvariablen ----------
    motion_detected: bool = False           # Start: keine Bewegung
    last_changed: datetime = datetime.now() # Zeitstempel der letzten Änderung

    # ---------- UI ----------
    with ui.card().classes('w-full h-full shadow-2 q-pa-md').style('align-self:stretch;'):
        ui.label('Motion Detection Status').classes('text-h6 font-semibold mb-2')
        # Karte mit fester, breiterem Layout ------------------------------
        with ui.column().classes('w-full items-start q-gutter-y-md'):
            with ui.row().classes('items-center q-gutter-x-md')\
                        .style('white-space: nowrap'):
                icon = ui.icon('highlight_off', color='red', size='2rem')
                status_label = ui.label('No motion detected')\
                                .classes('text-h6')
            timestamp_label = ui.label('').classes('text-body2')\
                                .style('white-space: nowrap')

    def refresh_view() -> None:
        """Icon, Text und Zeitstempel aktualisieren."""
        if motion_detected:
            icon.props('name=check_circle color=green')
            status_label.text = 'Motion detected'
        else:
            icon.props('name=highlight_off color=red')
            status_label.text = 'No motion detected'
        timestamp_label.text = f'Last changed: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'

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
