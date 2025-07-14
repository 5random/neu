from __future__ import annotations
from typing import Optional, Tuple, TypedDict, cast
import cv2
import base64
import numpy as np


from nicegui import ui
from nicegui.events import MouseEventArguments
from nicegui.element import Element
from nicegui.elements.label import Label
from nicegui.elements.interactive_image import InteractiveImage
from nicegui.elements.knob import Knob

from src.cam.camera import Camera, MotionDetector

# ─────────────────────────────────────────────────────────────────────────────
def create_motiondetection_card(camera:Optional[Camera] = None) -> None:
    """Card mit Slider, ROI-Editor und Live-Koordinaten-Anzeige."""
    IMG_SRC = 'https://picsum.photos/id/325/720/405'
    IMG_W, IMG_H = 720, 405

    if camera is None:
        ui.notify(
            'Kamera nicht verfügbar! ROI-Editor im Demo-Modus.', type='warning')

    # ---------- Zustand ------------------------------------------------------
    class ROIState(TypedDict):
        p1: Optional[Tuple[int, int]]
        p2: Optional[Tuple[int, int]]

    state: ROIState = {'p1': None, 'p2': None}
    #ROI_FILE = Path('roi_config.json')

    # UI-Referenzen
    image: Optional[InteractiveImage] = None
    tl_label: Optional[Label] = None
    br_label: Optional[Label] = None
    label_roi: Optional[Label] = None
    coords_label: Optional[Label] = None
    roi_editor_container: Optional[Element] = None
    button_roi_edit: Optional[Element] = None  # Referenz auf Öffnen-Button
    sensitivity_knob: Knob | None = None

    # ---------- SVG-Hilfsfunktionen -----------------------------------------
    def svg_cross(x: int, y: int, s: int = 14, col: str = 'deepskyblue') -> str:
        h = s // 2
        return (
            f'<line x1="{x-h}" y1="{y}" x2="{x+h}" y2="{y}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" />'
            f'<line x1="{x}" y1="{y-h}" x2="{x}" y2="{y+h}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" />'
        )

    def svg_circle(x: int, y: int, r: int = 8, col: str = 'gold') -> str:
        return (
            f'<circle cx="{x}" cy="{y}" r="{r}" '
            f'stroke="{col}" stroke-width="3" fill="none" />'
        )

    # ---------- ROI-Logik ----------------------------------------------------
    def roi_bounds() -> Optional[Tuple[int, int, int, int]]:
        if state['p1'] and state['p2']:
            x0, y0 = map(min, zip(state['p1'], state['p2']))
            x1, y1 = map(max, zip(state['p1'], state['p2']))
            return x0, y0, x1, y1
        return None

    def roi_text() -> str:
        b = roi_bounds()
        if b:
            x0, y0, x1, y1 = b
            return f'ROI: ({x0}, {y0}) – ({x1}, {y1})'
        elif state['p1']:
            x, y = state['p1']
            return f'ROI: ({x}, {y}) – (…)'
        return 'ROI: nicht aktiv'

    # ---------- UI-Updates ---------------------------------------------------
    def update_overlay() -> None:
        """Aktualisiert das SVG-Overlay im Bild."""
        if image is None:
            return

        parts: list[str] = []
        if state['p1']:
            parts.append(svg_cross(*state['p1']))
        if state['p2']:
            parts.append(svg_cross(*state['p2']))
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            parts.append(
                f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" '
                'stroke="lime" stroke-width="3" fill="none" />'
            )
            parts.extend([svg_circle(x0, y0), svg_circle(x1, y1)])

        image.content = ''.join(parts)

    def update_labels() -> None:
        if tl_label is None or br_label is None:
            return
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            tl_label.text = f'({x0}, {y0})'
            br_label.text = f'({x1}, {y1})'
        elif state['p1']:
            tl_label.text = f'({state["p1"][0]}, {state["p1"][1]})'
            br_label.text = '–'
        else:
            tl_label.text = br_label.text = '–'

    def refresh_ui() -> None:
        update_overlay()
        update_labels()
        if label_roi:
            label_roi.text = roi_text()
    
    # ---------- Integration Motion Detecotion --------------------------------

    def initialize_from_config() -> None:
        if camera is None or camera.motion_detector is None:
            return
        cam = cast(Camera, camera)
        md = cast(MotionDetector, cam.motion_detector)
        roi = md.roi

        if roi and getattr(roi, 'enabled', False):
            x0, y0 = roi.x, roi.y
            x1, y1 = roi.x + roi.width, roi.y + roi.height
            state['p1'] = (x0, y0)
            state['p2'] = (x1, y1)
        
        if sensitivity_knob is not None and hasattr(cam.motion_detector, 'sensitivity'):
            sens_value = int(md.sensitivity * 100)
            sensitivity_knob.set_value(sens_value)

    def update_sensitivity(value: int) -> None:
        """Aktualisiert die Sensitivität des Motion Detectors."""
        if camera is None or camera.motion_detector is None:
            return
        cam = cast(Camera, camera)
        md = cast(MotionDetector, cam.motion_detector)
        # Sensitivität in Prozent (0-100)
        sens_value = max(0.01, value / 100.0)  # mind. 1%
        md.update_sensitivity(sens_value)
        if cam.motion_detector:
            cam.app_config.motion_detection.sensitivity = sens_value
            if hasattr(cam, 'save_uvc_config'):
                cam.save_uvc_config()  # Speichert die Konfiguration

        ui.notify(f'Sensitivität auf {sens_value:.2f} gesetzt', type='info',
                  position='bottom-right')

    # ---------- Event-Handler ------------------------------------------------
    def reset_roi() -> None:
        state['p1'] = state['p2'] = None
        refresh_ui()

    def _handle_click(e: MouseEventArguments) -> None:
        x, y = int(e.image_x), int(e.image_y)
        target = (
            'p1' if state['p1'] is None
            else ('p2' if state['p2'] is None else None)
        )
        if target:
            state[target] = (x, y)
        else:  # dritter Klick → neue Auswahl
            reset_roi()
            _handle_click(e)
            return
        refresh_ui()

    def handle_mouse(e: MouseEventArguments) -> None:
        """Versorgt Live-Koordinaten & leitet Klicks weiter."""
        if coords_label:
            if e.type == 'mouseleave':
                coords_label.text = '(–, –)'
            else:  # 'move' oder 'click'
                coords_label.text = f'({int(e.image_x)}, {int(e.image_y)})'

        if e.type == 'click':
            _handle_click(e)

    def save_roi() -> None:
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            roi_enabled = True
            roi_width = x1 - x0
            roi_height = y1 - y0

            if camera is None:
                ui.notify('Kamera nicht verfügbar!', type='warning',
                          position='bottom-right')
                return
            
            cam = cast(Camera, camera)
            if cam is not None and cam.motion_detector is not None:
                md = cast(MotionDetector, cam.motion_detector)
                roi = md.roi
                roi.x = x0
                roi.y = y0
                roi.width = roi_width
                roi.height = roi_height
                roi.enabled = roi_enabled

            
                cam.app_config.motion_detection.region_of_interest = {
                    'enabled': roi_enabled,
                    'x': x0,
                    'y': y0,
                    'width': roi_width,
                    'height': roi_height
                }

            if hasattr(cam, 'save_uvc_config'):
                cam.save_uvc_config()  # Speichert die Konfiguration

            ui.notify('ROI gespeichert', type='positive',
                      position='bottom-right')
        else:
            ui.notify('Bitte zuerst beide Ecken wählen!',
                      type='warning', position='bottom-right')
    
    def refresh_snapshot() -> None:
        """Aktualisiert das Snapshot-Bild im Editor."""
        if image is None or camera is None:
            return
        try:
            frame = camera.take_snapshot()
            if frame is not None:
                success, buffer = cv2.imencode('.jpg', frame)
                if success:
                    src = f"data:image/jpeg;base64,{base64.b64encode(buffer).decode()}"
                    image.set_source(src)          # <-- Bild austauschen
        except Exception as e:
            ui.notify(f"Snapshot-Fehler: {e}", type="warning")

    def toggle_roi_editor() -> None:
        """Öffnet/Schließt ROI-Editor und blendet zugehörige Elemente um."""
        if roi_editor_container is None or button_roi_edit is None or label_roi is None:
            return
        vis_editor = not roi_editor_container.visible            # neuer Zustand
        roi_editor_container.set_visibility(vis_editor)          # Editor an/aus
        button_roi_edit.set_visibility(not vis_editor)           # Button umgekehrt
        label_roi.set_visibility(not vis_editor)                 # ROI-Text umgekehrt
        if vis_editor:
            initialize_from_config()
            refresh_snapshot()  # Aktualisiert das Snapshot-Bild
            refresh_ui()

    # ---------- UI-Aufbau ----------------------------------------------------
    with ui.card().style("align-self:stretch; flex-direction:column; justify-content:center; align-items:start; display:flex; min-height:px;"):
        ui.label('Motion Detection Settings').classes(
            'text-h6 font-semibold mb-2'
        )
        with ui.column().classes('gap-4'):

            # ── Sensitivität in eigener Card ────────────────────────────────
            with ui.card().style("align-self:stretch; justify-content:center; align-items:center; min-height:60px;"):
                ui.label('Sensitivität').classes('text-h6 font-semibold mb-2')
                with ui.row().classes('items-center gap-4'):
                    sensitivity_knob = ui.knob(
                        min=0, max=100, value=10, step=1, show_value=True, on_change=lambda e: update_sensitivity(e.value)
                    ).style("align-self:stretch; display:flex; justify-content:center; align-items:center; flex-direction:column; flex-wrap:nowrap;").classes('flex-grow')

            ui.separator()

            # ── ROI-Bereich jetzt ebenfalls in eigener Card ─────────────────
            with ui.card().style("align-self:stretch;"):
                with ui.column().classes('w-full gap-2'):

                    # Kopfzeile mit Titel und Edit-Button
                    with ui.row().classes('items-center justify-between w-full'):
                        ui.label('Region of Interest (ROI)')\
                            .classes('text-h6 font-semibold mb-2')
                        button_roi_edit = ui.button(
                            icon='crop',
                            color='secondary',
                            on_click=toggle_roi_editor
                        ).tooltip('edit ROI').classes('text-gray-500 ml-auto')

                    # ROI-Status-Text
                    label_roi = ui.label(roi_text())\
                        .style("align-self:stretch; display:flex; justify-content:center;"
                               "align-items:center;")\
                        .classes('text-sm font-mono mb-2')

                    # ── ROI-Editor (initial versteckt) ────────────────────
                    roi_editor_container = ui.column().classes('gap-4')
                    with roi_editor_container:

                        ui.label('Editor').classes('text-h6 font-semibold')

                        frame: np.ndarray | None = None
                        image_src = IMG_SRC
                        if camera is not None:
                            try:
                                frame = camera.take_snapshot()
                                if frame is not None:
                                    success, buffer = cv2.imencode('.jpg', frame)
                                    if success:
                                        image_src = (
                                            f"data:image/jpeg;base64,{base64.b64encode(buffer).decode('utf-8')}"
                                        )
                            except Exception as e:
                                ui.notify(f"Snapshot-Fehler: {e}", type="warning")

                        h, w = (frame.shape[:2] if frame is not None else (IMG_H, IMG_W))
                        ratio_style = (
                            f"aspect-ratio:{w}/{h};"
                            "width:100%;height:auto;"
                            "object-fit:contain;max-height:300px;"
    )

                        image = (
                            ui.interactive_image(
                                image_src,
                                on_mouse=handle_mouse,
                                events=['click', 'move', 'mouseleave'],
                                cross=True,
                            )
                            .style(ratio_style)
                            .classes('rounded-borders')
                        )

                        # ROI-Koordinaten-Labels
                        with ui.row().classes('items-center gap-4 text-sm'):
                            ui.label('Oben-Links:')
                            tl_label = ui.label('–').classes('font-mono')
                            ui.label('Unten-Rechts:')
                            br_label = ui.label('–').classes('font-mono')

                        # Live-Mauskoordinaten (rechtsbündig, grau)
                        with ui.row().classes('justify-end'):
                            coords_label = ui.label('(–, –)')\
                                .classes('text-sm font-mono text-gray-500')

                        # Aktions-Buttons
                        with ui.row().classes('gap-2'):
                            ui.button(icon='restart_alt', color='secondary', on_click=reset_roi)\
                                .classes('flex-grow').tooltip('reset ROI')
                            ui.button(icon='save', color='primary', on_click=save_roi)\
                                .classes('flex-grow').tooltip('save')
                            ui.button(icon='close', color='negative', on_click=toggle_roi_editor)\
                                .classes('flex-grow').tooltip('close')

                    # Editor initial versteckt
                    roi_editor_container.set_visibility(False)
