from nicegui import ui
from fastapi import Request
from datetime import datetime

# ---------- interne Statusvariablen ----------
motion_detected: bool = False           # Start: keine Bewegung
last_changed: datetime = datetime.now() # Zeitstempel der letzten Änderung

# ---------- UI ----------
with ui.column().classes('items-start q-gutter-y-md'):

    # Karte mit fester, breiterem Layout ------------------------------
    with ui.card().style('width: 360px; min-height: 140px')\
                  .classes('shadow-2 q-pa-md'):
        # 1) Status-Zeile: Icon + Text, kein Zeilenumbruch
        with ui.row().classes('items-center q-gutter-x-md')\
                     .style('white-space: nowrap'):
            icon = ui.icon('highlight_off', color='red', size='2rem')
            status_label = ui.label('Keine Bewegung erkannt')\
                             .classes('text-h6')
        # 2) Zeitstempel-Zeile, ebenfalls kein Umbruch
        timestamp_label = ui.label('').classes('text-body2')\
                             .style('white-space: nowrap')

    # Test-Button zum Umschalten ---------------------------------------
    def toggle():
        global motion_detected, last_changed
        motion_detected = not motion_detected
        last_changed = datetime.now()
        refresh_view()

    ui.button('Status umschalten (Test)', on_click=toggle)

def refresh_view() -> None:
    """Icon, Text und Zeitstempel aktualisieren."""
    if motion_detected:
        icon.props('name=check_circle color=green')
        status_label.text = 'Bewegung erkannt'
    else:
        icon.props('name=highlight_off color=red')
        status_label.text = 'Keine Bewegung erkannt'
    timestamp_label.text = f'Letzte Änderung: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'

# ---------- REST-Endpunkt für Dein Analyse-Skript -------------------
@ui.page('/update')
async def update(request: Request):
    """
    GET http://localhost:8080/update?motion=1   # Bewegung erkannt
    GET http://localhost:8080/update?motion=0   # Keine Bewegung
    """
    global motion_detected, last_changed
    new_motion = request.query_params.get('motion', '0') == '1'
    if new_motion != motion_detected:
        motion_detected = new_motion
        last_changed = datetime.now()
    refresh_view()
    return 'ok'

# ---------- App starten ------------------------------------------------
if __name__ in {'__main__', '__mp_main__'}:
    refresh_view()
    ui.run(title='Bewegungsstatus', native=False)
