# gui.py  (NEU)
from nicegui import ui
from elements import (
    uvc_knobs,
    motion_detection_setting_card,
    emailcard,
    measurementcard,
    motion_status_element,
)

def main() -> None:
    # ---------- Kopfzeile ----------
    with ui.header(elevated=True).classes('items-center q-pr-md'):
        ui.label('CVD-Tracker').classes('text-h5 text-bold')
        ui.space()
        ui.label('v1.0.0').classes('text-caption')

    # ---------- Hauptraster ----------
    with ui.row().classes('w-full q-col-gutter-lg q-mt-md'):
        # linke 8/12-Spalte ---------------------------------------------------
        with ui.column().classes('col-8 q-gutter-md'):
            video = ui.interactive_image()\
                .classes('w-full h-[460px] bg-black')
            # TODO: Quelle setzen, z. B. via timer
            with ui.row().classes('q-gutter-md'):
                motion_status_element.create_motion_status_element()
                measurementcard.create_measurement_card()

        # rechte 4/12-Spalte --------------------------------------------------
        with ui.column().classes('col q-gutter-md'):
            with ui.expansion('Kameraeinstellungen', icon='settings',
                              dense=True, expanded=True):
                uvc_knobs.create_uvc_content()
            with ui.expansion('Bewegungserkennung', icon='running_with_errors',
                              dense=True):
                motion_detection_setting_card.create_motiondetection_card()
            with ui.expansion('E-Mail-Alerts', icon='mail', dense=True):
                emailcard.create_emailcard()

    # ---------- Footer ----------
    with ui.footer().classes('justify-center q-pt-sm'):
        ui.label('© 2025 CVD-Tracker')

if __name__ in {'__main__', '__mp_main__'}:
    main()
    ui.run(title='CVD-Tracker', favicon='📷')
