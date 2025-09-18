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
from src.config import get_global_config, save_global_config, get_logger

logger = get_logger('gui.motion')

# ─────────────────────────────────────────────────────────────────────────────
def create_motiondetection_card(camera:Optional[Camera] = None) -> None:
    """Card mit Slider, ROI-Editor und Live-Koordinaten-Anzeige."""
    IMG_SRC = 'https://picsum.photos/id/325/720/405'
    IMG_W, IMG_H = 720, 405
    config = get_global_config()


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
    roi_enabled_checkbox: Optional[ui.checkbox] = None
    label_roi: Optional[Label] = None
    coords_label: Optional[Label] = None
    roi_editor_container: Optional[Element] = None
    button_roi_edit: Optional[Element] = None
    sensitivity_knob: Knob | None = None

    debounce_task = None

    # ---------- ROI Enable/Disenable ----------------------------------------

    def update_roi_enabled(enabled: bool) -> None:
        """Aktiviert/Deaktiviert ROI zur Laufzeit"""
        if camera is None or camera.motion_detector is None:
            return
            
        cam = cast(Camera, camera)
        md = cast(MotionDetector, cam.motion_detector)
        
        # ROI-Status setzen
        md.roi.enabled = enabled
        
        # Config aktualisieren
        if config:
            config.motion_detection.region_of_interest['enabled'] = enabled
            save_global_config()
            
        # Camera config aktualisieren
        if cam.app_config:
            cam.app_config.motion_detection.region_of_interest['enabled'] = enabled
            cam.save_uvc_config()
            
        # Background Model zurücksetzen für sofortige Wirkung
        md.reset_background_model()
        
        # UI aktualisieren
        refresh_ui()
        
        status = "enabled" if enabled else "disabled"
        ui.notify(f'ROI {status}', type='positive', position='bottom-right')
        logger.info(f'ROI {status}')

    # ---------- SVG-Hilfsfunktionen -----------------------------------------
    def svg_cross(x: int, y: int, s: int = 14, col: str = 'deepskyblue') -> str:
        dis_scale = 300 / IMG_H if IMG_H > 300 else 1.0
        h = int(s / dis_scale) // 2 # Höhe des Kreuzes skaliert

        return (
            f'<line x1="{x-h}" y1="{y}" x2="{x+h}" y2="{y}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" '
             'pointer-events="none" vector-effect="non-scaling-stroke" />'
            f'<line x1="{x}" y1="{y-h}" x2="{x}" y2="{y+h}" '
            f'stroke="{col}" stroke-width="3" stroke-linecap="round" '
             'pointer-events="none" vector-effect="non-scaling-stroke" />'
        )

    def svg_circle(x: int, y: int, r: int = 8, col: str = 'gold') -> str:
        dis_scale = 300 / IMG_H if IMG_H > 300 else 1.0
        r = int(r / dis_scale)
        return (
            f'<circle cx="{x}" cy="{y}" r="{r}" '
            f'stroke="{col}" stroke-width="3" fill="none" '
             'pointer-events="none" vector-effect="non-scaling-stroke" />'
        )
    


    # ---------- ROI-Logik ----------------------------------------------------
    def roi_bounds() -> Optional[Tuple[int, int, int, int]]:
        if state['p1'] and state['p2']:
            x0, y0 = map(min, zip(state['p1'], state['p2']))
            x1, y1 = map(max, zip(state['p1'], state['p2']))
            return x0, y0, x1, y1
        return None

    def roi_text() -> str:
        if roi_enabled_checkbox is not None:
            enabled = roi_enabled_checkbox.value
        elif camera and camera.motion_detector:
            enabled = getattr(camera.motion_detector.roi, 'enabled', False)

            if not enabled:
                return 'ROI: not active (disabled)'
            
        b = roi_bounds()
        if b:
            x0, y0, x1, y1 = b
            return f'ROI: ({x0}, {y0}) - ({x1}, {y1})'
        elif state['p1']:
            x, y = state['p1']
            return f'ROI: ({x}, {y}) - (…)'
        return 'ROI: enabled, no Area selected'

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
                'stroke="lime" stroke-width="3" fill="none" pointer-events="none" vector-effect="non-scaling-stroke" />'
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
        
        if roi_enabled_checkbox is not None:
            roi_enabled_checkbox.set_value(getattr(roi, 'enabled', False))

        if roi and getattr(roi, 'enabled', False):
            x0, y0 = roi.x, roi.y
            x1, y1 = roi.x + roi.width, roi.y + roi.height
            state['p1'] = (x0, y0)
            state['p2'] = (x1, y1)
            logger.debug(f"ROI initialized from config: {state['p1']} - {state['p2']}")
        else:
            state['p1'] = state['p2'] = None
            logger.debug("No ROI found")
        
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
            if cam.motion_detector and config:
                config.motion_detection.sensitivity = sens_value
                cam.app_config.motion_detection.sensitivity = sens_value
                cam.save_uvc_config()
                save_global_config()
                logger.info(f'Saved sensitivity: {sens_value}')

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
                coords_label.text = '(-, -)'
            else:
                coords_label.text = f'({int(e.image_x)}, {int(e.image_y)})'

        if e.type == 'click':
            _handle_click(e)

    def save_roi() -> None:
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            roi_enabled = roi_enabled_checkbox.value if roi_enabled_checkbox else True
            # Ensure ROI is within image bounds
            x0 = max(0, min(x0, IMG_W - 1))
            y0 = max(0, min(y0, IMG_H - 1))
            x1 = max(x0 + 1, min(x1, IMG_W))
            y1 = max(y0 + 1, min(y1, IMG_H))
            roi_width = x1 - x0
            roi_height = y1 - y0

            if camera is None:
                ui.notify('Camera not available!', type='warning',
                          position='bottom-right')
                return
            
            # Check minimum size and if needed, adjust ROI to ensure minimum size
            min_size = 30
            if roi_width < min_size or roi_height < min_size:
                logger.warning(f"ROI too small: {roi_width}x{roi_height}, expanding for stability")

                # Zentrum der aktuellen ROI beibehalten
                center_x = (x0 + x1) // 2
                center_y = (y0 + y1) // 2
                
                # Neue ROI um Zentrum berechnen
                new_x0 = max(0, center_x - min_size // 2)
                new_y0 = max(0, center_y - min_size // 2)
                new_x1 = min(IMG_W, new_x0 + min_size)
                new_y1 = min(IMG_H, new_y0 + min_size)

                # Falls an Bildrand: ROI entsprechend verschieben
                if new_x1 - new_x0 < min_size and new_x0 > 0:
                    new_x0 = max(0, new_x1 - min_size)
                if new_y1 - new_y0 < min_size and new_y0 > 0:
                    new_y0 = max(0, new_y1 - min_size)

                x0, y0, x1, y1 = new_x0, new_y0, new_x1, new_y1
                roi_width = x1 - x0
                roi_height = y1 - y0
                logger.info(f"ROI expanded to: ({x0}, {y0}) - ({x1}, {y1})")
                ui.notify(
                    f'ROI expanded to minimum size: ({x0}, {y0}) - ({x1}, {y1})',
                    type='warning', position='bottom-right'
                )
                
            cam = cast(Camera, camera)
            if cam is not None and cam.motion_detector is not None:
                md = cast(MotionDetector, cam.motion_detector)
                roi = md.roi
                roi.x = x0
                roi.y = y0
                roi.width = roi_width
                roi.height = roi_height
                roi.enabled = roi_enabled
                md.reset_background_model()

            
                roi_data = {
                    'enabled': roi_enabled,
                    'x': x0,
                    'y': y0,
                    'width': roi_width,
                    'height': roi_height
                }

                try:
                    if config:
                        config.motion_detection.region_of_interest = roi_data
                        save_global_config()
                        logger.info('ROI config saved to config')
                    else:
                        ui.notify('ROI config could not be saved to config!', type='warning',
                                  position='bottom-right')
                        logger.warning('ROI config not be saved to config!')
                except Exception as e:
                    ui.notify(f'Error saving config: {e}', type='warning',
                              position='bottom-right')
                    logger.error(f'Error saving config: {e}')
                    
                try:
                    if cam:
                        cam.app_config.motion_detection.region_of_interest = roi_data
                        cam.save_uvc_config()
                        logger.info('Camera ROI config saved and applied')
                    else:
                        ui.notify('Camera ROI config could not be saved!', type='warning',
                                  position='bottom-right')
                        logger.warning('Camera ROI config could not be saved!')
                except Exception as e:
                    ui.notify(f'Error saving camera config: {e}', type='warning',
                              position='bottom-right')
                    logger.error(f'Error saving camera config: {e}')
                    return
                
                state['p1'] = (x0, y0)
                state['p2'] = (x1, y1)
                refresh_ui()
                ui.notify(f'ROI saved and applied: {roi_data}', type='positive',
                        position='bottom-right')
                logger.info(f'ROI saved: {roi_data}')
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
                    IMG_H, IMG_W = h, w
                    ratio_style = (
                        f"aspect-ratio:{w}/{h};"
                        "width:100%;height:auto;")
                    image.set_source(src)
                    image.style(ratio_style)
        except Exception as e:
            ui.notify(f"Snapshot error: {e}", type="warning")

    def toggle_roi_editor() -> None:
        """Öffnet/Schließt ROI-Editor und blendet zugehörige Elemente um."""
        if roi_editor_container is None or button_roi_edit is None or label_roi is None:
            return
        vis_editor = not roi_editor_container.visible
        roi_editor_container.set_visibility(vis_editor)
        button_roi_edit.set_visibility(not vis_editor)
        label_roi.set_visibility(not vis_editor)
        if vis_editor:
            initialize_from_config()
            refresh_snapshot()
            refresh_ui()

    # ---------- UI-Aufbau ----------------------------------------------------
    logger.info("Creating motion detection card")

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
                    roi_enabled_checkbox = ui.checkbox('ROI enabled', value=True, on_change=lambda e: update_roi_enabled(e.value)).tooltip('Enable/disable Region of Interest')
                    ui.separator()
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
                        
                    button_roi_edit.bind_enabled_from(roi_enabled_checkbox, 'value')

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
                                cross='#19bfd2',
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

    initialize_from_config()
    refresh_ui()