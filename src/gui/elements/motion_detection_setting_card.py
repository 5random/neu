from __future__ import annotations
from typing import Optional, Tuple, TypedDict, cast
import cv2
import base64
import numpy as np
import asyncio


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
            'Camera not available, ROI editor in demo mode.', type='warning')
    else:
        res = camera.get_camera_status()
        if res and res.get('resolution'):
            IMG_W, IMG_H = res['resolution']['width'], res['resolution']['height']

    # ---------- Zustand ------------------------------------------------------
    class ROIState(TypedDict):
        p1: Optional[Tuple[int, int]]
        p2: Optional[Tuple[int, int]]

    state: ROIState = {'p1': None, 'p2': None}

    # UI-Referenzen
    image: Optional[InteractiveImage] = None
    tl_label: Optional[Label] = None
    br_label: Optional[Label] = None
    label_roi: Optional[Label] = None
    coords_label: Optional[Label] = None
    roi_editor_container: Optional[Element] = None
    button_roi_edit: Optional[Element] = None  # Referenz auf Öffnen-Button
    sensitivity_knob: Knob | None = None

    debounce_task = None

    # ---------- SVG-Hilfsfunktionen -----------------------------------------
    def svg_cross(x: int, y: int, s: int = 14, col: str = 'deepskyblue') -> str:
        h = s // 2
        return (
            f'<line x1="{x-h}" y1="{y}" x2="{x+h}" y2="{y}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" '
             'pointer-events="none" />'
            f'<line x1="{x}" y1="{y-h}" x2="{x}" y2="{y+h}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" '
             'pointer-events="none" />'
        )

    def svg_circle(x: int, y: int, r: int = 8, col: str = 'gold') -> str:
        return (
            f'<circle cx="{x}" cy="{y}" r="{r}" '
            f'stroke="{col}" stroke-width="3" fill="none" '
             'pointer-events="none" />'
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
            return f'ROI: ({x0}, {y0}) - ({x1}, {y1})'
        elif state['p1']:
            x, y = state['p1']
            return f'ROI: ({x}, {y}) - (…)'
        return 'ROI: not active'

    # ---------- UI-Updates ---------------------------------------------------
    def update_overlay() -> None:
        """Aktualisiert das SVG-Overlay im Bild."""
        if image is None:
            return

        original_h, original_w = (IMG_H, IMG_W)

        if original_h > 300:
            scale = 300 / original_h
            display_w = original_w * scale
            display_h = 300
        else:
            scale = 1.0
            display_w = original_w
            display_h = original_h

        parts: list[str] = []
        if state['p1']:
            scaled_x = state['p1'][0] * scale
            scaled_y = state['p1'][1] * scale
            parts.append(svg_cross(int(scaled_x), int(scaled_y)))
            #parts.append(svg_cross(*state['p1']))
        if state['p2']:
            scaled_x = state['p2'][0] * scale
            scaled_y = state['p2'][1] * scale
            parts.append(svg_cross(int(scaled_x), int(scaled_y)))
            #parts.append(svg_cross(*state['p2']))
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            scaled_x0 = int(x0 * scale)
            scaled_y0 = int(y0 * scale)
            scaled_x1 = int(x1 * scale)
            scaled_y1 = int(y1 * scale)
            parts.append(
                f'<rect x="{scaled_x0}" y="{scaled_y0}" width="{scaled_x1-scaled_x0}" height="{scaled_y1-scaled_y0}" '
                'stroke="lime" stroke-width="3" fill="none" pointer-events="none" />'
            )
            parts.extend([svg_circle(scaled_x0, scaled_y0), svg_circle(scaled_x1, scaled_y1)])

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
            br_label.text = '-'
        else:
            tl_label.text = br_label.text = '-'

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
        nonlocal debounce_task

        if camera is None or camera.motion_detector is None:
            return
        cam = cast(Camera, camera)
        md = cast(MotionDetector, cam.motion_detector)
        # Sensitivität in Prozent (0-100)
        sens_value = max(0.01, value / 100.0)  # mind. 1%
        md.update_sensitivity(sens_value)
        async def save_config_delayed():
            await asyncio.sleep(0.5)
            if cam.motion_detector:
                cam.app_config.motion_detection.sensitivity = sens_value
                if hasattr(cam, 'save_uvc_config'):
                    cam.save_uvc_config()  # Speichert die Konfiguration

        ui.notify(f'Sensitivity saved', type='info',
                position='bottom-right')
        
        if debounce_task is not None and not debounce_task.done():
            debounce_task.cancel()
        
        debounce_task = asyncio.create_task(save_config_delayed())

    # ---------- Event-Handler ------------------------------------------------
    def reset_roi() -> None:
        state['p1'] = state['p2'] = None
        refresh_snapshot()
        refresh_ui()

    def _handle_click(e: MouseEventArguments) -> None:
        
        original_h, original_w = (IMG_H, IMG_W)

        if original_h > 300:
            scale = 300 / original_h
        else:
            scale = 1.0
        
        original_x, original_y = int(e.image_x/scale), int(e.image_y/scale)

        target = (
            'p1' if state['p1'] is None
            else ('p2' if state['p2'] is None else None)
        )
        if target:
            state[target] = (original_x, original_y)
        else:  # dritter Klick → neue Auswahl
            reset_roi()
            _handle_click(e)
            return
        refresh_ui()

    def handle_mouse(e: MouseEventArguments) -> None:
        """Versorgt Live-Koordinaten & leitet Klicks weiter."""
        if coords_label:
            if e.type == 'mouseleave':
                coords_label.text = '(-, -)'
            else:  # 'move' oder 'click'
                original_h, original_w = (IMG_H, IMG_W)
                scale = 300 / original_h if original_h > 300 else 1.0
                coords_label.text = f'({int(e.image_x/scale)}, {int(e.image_y/scale)})'

        if e.type == 'click':
            _handle_click(e)

    def save_roi() -> None:
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            roi_enabled = True
            roi_width = x1 - x0
            roi_height = y1 - y0

            if camera is None:
                ui.notify('Camera not available!', type='warning',
                          position='bottom-right')
                return
            elif x0 < 0 or y0 < 0 or x1 > IMG_W or y1 > IMG_H:
                ui.notify('Invalid ROI position!', type='warning',
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
                md.reset_background_model()  # Reset background model after ROI change

            
                cam.app_config.motion_detection.region_of_interest = {
                    'enabled': roi_enabled,
                    'x': x0,
                    'y': y0,
                    'width': roi_width,
                    'height': roi_height
                }

            if hasattr(cam, 'save_uvc_config'):
                cam.save_uvc_config()  # Speichert die Konfiguration

            ui.notify('ROI saved', type='positive',
                      position='bottom-right')
        else:
            ui.notify('Please select both corners first!',
                      type='warning', position='bottom-right')
    
    def refresh_snapshot() -> None:
        """Aktualisiert das Snapshot-Bild im Editor."""
        nonlocal IMG_H, IMG_W
        if image is None or camera is None:
            return
        try:
            frame = camera.take_snapshot()
            if frame is not None:
                success, buffer = cv2.imencode('.jpg', frame)
                if success:
                    src = f"data:image/jpeg;base64,{base64.b64encode(buffer).decode()}"
                    h, w = (frame.shape[:2] if frame is not None else (IMG_H, IMG_W))
                    IMG_H, IMG_W = h, w  # Update global dimensions
                    ratio_style = (
                        f"aspect-ratio:{w}/{h};"
                        "width:100%;height:auto;")
                    image.set_source(src)          # <-- Bild austauschen
                    image.style(ratio_style)       # <-- Style anpassen
        except Exception as e:
            ui.notify(f"Snapshot error: {e}", type="warning")

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
    with ui.card().style("align-self:stretch; flex-direction:column; justify-content:center; align-items:start; display:flex;"):
        ui.label('Motion Detection Settings').classes('text-h6 font-semibold mb-2')
        with ui.column().style("align-self:stretch;").classes('gap-4'):

            # ── Sensitivität in eigener Card ────────────────────────────────
            with ui.card().classes('w-full').style("align-items:center;"):
                ui.label('Sensitivity:').classes('font-semibold mb-2 self-start')
                with ui.row().classes('justify-center gap-4'):
                    sensitivity_knob = ui.knob(
                        min=0, max=100, value=10, step=1, show_value=True, on_change=lambda e: update_sensitivity(e.value)
                    ).tooltip('Adjust motion detection sensitivity (0-100%)')

            ui.separator()

            # ── ROI-Bereich jetzt ebenfalls in eigener Card ─────────────────
            with ui.card().classes('w-full').style("align-self:stretch;"):
                with ui.column().classes('w-full gap-2'):
                    ui.label('Region of Interest (ROI)').classes('font-semibold mb-2 self-start')
                    # Kopfzeile mit Titel und Edit-Button
                    with ui.row().classes('items-center gap-2 justify-center'):
                        button_roi_edit = ui.button(
                            icon='crop',
                            color='primary',
                            on_click=toggle_roi_editor
                        ).props('round').tooltip('edit ROI').classes('text-gray-500 ml-auto')

                        # ROI-Status-Text
                        label_roi = ui.label(roi_text())\
                            .style("align-self:stretch; display:flex; justify-content:center;"
                                "align-items:center;")\
                            .classes('text-sm font-mono mb-2')

                    # ── ROI-Editor (initial versteckt) ────────────────────
                    roi_editor_container = ui.column().classes('gap-4')
                    with roi_editor_container:

                        ui.label('ROI Editor:').classes('font-semibold mb-2 self-start')

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
                                ui.notify(f"Snapshot error: {e}", type="warning")

                        h, w = (frame.shape[:2] if frame is not None else (IMG_H, IMG_W))
                        ratio_style = (
                            f"aspect-ratio:{w}/{h};"
                            "width:100%;height:auto;")

                        image = (
                            ui.interactive_image(
                                image_src,
                                on_mouse=handle_mouse,
                                events=['click', 'move', 'mouseleave'],
                                cross='blue',
                            )
                            .style(ratio_style)
                            .classes('rounded-borders')
                        )

                        # ROI-Koordinaten-Labels
                        with ui.row().classes('items-center gap-4 text-sm'):
                            ui.label('upper left corner:')
                            tl_label = ui.label('-').classes('font-mono')
                            ui.label('bottom right corner:')
                            br_label = ui.label('-').classes('font-mono')

                        # Live-Mauskoordinaten (rechtsbündig, grau)
                        with ui.row().classes('justify-end'):
                            coords_label = ui.label('(-, -)')\
                                .classes('text-sm font-mono text-gray-500')

                        # Aktions-Buttons
                        with ui.row().classes('gap-2'):
                            ui.button(icon='save', color='primary', on_click=save_roi)\
                                .classes('flex-grow').props('round').tooltip('save')
                            ui.button(icon='restart_alt', color='secondary', on_click=reset_roi)\
                                .classes('flex-grow').props('round').tooltip('reset ROI')
                            ui.button(icon='close', color='negative', on_click=toggle_roi_editor)\
                                .classes('flex-grow').props('round').tooltip('close')

                    # Editor initial versteckt
                    roi_editor_container.set_visibility(False)
