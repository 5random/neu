from __future__ import annotations
from typing import Optional, Tuple, TypedDict, cast, Any
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
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row
from src.gui.util import schedule_bg
from src.config import get_global_config, save_global_config, get_logger

logger = get_logger('gui.motion')

# Use centralized schedule_bg from src.gui.util for safe background scheduling


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
        points: list[Tuple[int, int]]

    state: ROIState = {'points': []}

    # UI-Referenzen
    image: Optional[InteractiveImage] = None
    points_label: Optional[Label] = None
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
            
        cam = camera
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
    def svg_circle(x: int, y: int, r: int = 4, col: str = 'gold') -> str:
        dis_scale = 300 / IMG_H if IMG_H > 300 else 1.0
        r = int(r / dis_scale)
        return (
            f'<circle cx="{x}" cy="{y}" r="{r}" '
            f'stroke="{col}" stroke-width="2" fill="{col}" fill-opacity="0.5" '
            'pointer-events="none" vector-effect="non-scaling-stroke" />'
        )
    
    def svg_polygon(points: list[Tuple[int, int]], col: str = 'lime') -> str:
        if not points:
            return ""
        pts_str = " ".join([f"{x},{y}" for x, y in points])
        return (
            f'<polygon points="{pts_str}" '
            f'stroke="{col}" stroke-width="2" fill="{col}" fill-opacity="0.2" '
            'pointer-events="none" vector-effect="non-scaling-stroke" />'
        )

    def svg_line(p1: Tuple[int, int], p2: Tuple[int, int], col: str = 'cyan') -> str:
        return (
            f'<line x1="{p1[0]}" y1="{p1[1]}" x2="{p2[0]}" y2="{p2[1]}" '
            f'stroke="{col}" stroke-width="2" stroke-dasharray="5,5" '
            'pointer-events="none" vector-effect="non-scaling-stroke" />'
        )

    # ---------- ROI-Logik ----------------------------------------------------
    def roi_text() -> str:
        if roi_enabled_checkbox is not None:
            enabled = roi_enabled_checkbox.value
        elif camera and camera.motion_detector:
            enabled = getattr(camera.motion_detector.roi, 'enabled', False)

            if not enabled:
                return 'ROI: not active (disabled)'
            
        if state['points']:
            return f'ROI: {len(state["points"])} points defined'
        return 'ROI: enabled, no Area selected'

    # ---------- UI-Updates ---------------------------------------------------
    def update_overlay() -> None:
        """Aktualisiert das SVG-Overlay im Bild."""
        if image is None:
            return

        parts: list[str] = []
        points = state['points']
        
        # Draw Polygon if 3+ points
        if len(points) >= 3:
            parts.append(svg_polygon(points))
        
        # Draw lines between points
        if len(points) > 1:
            for i in range(len(points) - 1):
                parts.append(svg_line(points[i], points[i+1]))
            # Close loop preview if > 2 points
            if len(points) > 2:
                parts.append(svg_line(points[-1], points[0], col='lime'))

        # Draw vertices
        for pt in points:
            parts.append(svg_circle(pt[0], pt[1]))

        image.content = ''.join(parts)

    def update_labels() -> None:
        if points_label is None:
            return
        points = state['points']
        if points:
            points_label.text = f'{len(points)} Points: {points}'
        else:
            points_label.text = 'No points selected'

    def refresh_ui() -> None:
        update_overlay()
        update_labels()
        if label_roi:
            label_roi.text = roi_text()
    
    # ---------- Integration Motion Detecotion --------------------------------

    def initialize_from_config() -> None:
        if camera is None or camera.motion_detector is None:
            return
        cam = camera
        md = cast(MotionDetector, cam.motion_detector)
        roi = md.roi
        
        if roi_enabled_checkbox is not None:
            roi_enabled_checkbox.set_value(getattr(roi, 'enabled', False))

        # Load points from config if available
        if hasattr(roi, 'points') and roi.points:
             # Convert list of lists to list of tuples
            state['points'] = [tuple(pt) for pt in roi.points] # type: ignore
            logger.debug(f"ROI initialized from config with {len(state['points'])} points")
        elif roi and getattr(roi, 'enabled', False):
            # Fallback: Convert rectangle to 4 points
            x, y, w, h = roi.x, roi.y, roi.width, roi.height
            state['points'] = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
            logger.debug(f"ROI initialized from rectangle: {state['points']}")
        else:
            state['points'] = []
            logger.debug("No ROI found")
        
        if sensitivity_knob is not None and hasattr(cam.motion_detector, 'sensitivity'):
            sens_value = int(md.sensitivity * 100)
            sensitivity_knob.set_value(sens_value)

    def update_sensitivity(value: int) -> None:
        """Aktualisiert die Sensitivität des Motion Detectors."""
        nonlocal debounce_task

        if camera is None or camera.motion_detector is None:
            return
        cam = camera
        md = cast(MotionDetector, cam.motion_detector)
        # Sensitivität in Prozent (0-100)
        sens_value = max(0.01, value / 100.0)  # mind. 1%
        md.update_sensitivity(sens_value)
        async def save_config_delayed() -> None:
            await asyncio.sleep(0.5)
            if cam.motion_detector and config:
                config.motion_detection.sensitivity = sens_value
                cam.app_config.motion_detection.sensitivity = sens_value
                cam.save_uvc_config()
                save_global_config()
                logger.info(f'Saved sensitivity: {sens_value}')

        ui.notify(f'Sensitivity saved', type='info',
                position='bottom-right')
        
        # Robust cancel: supports asyncio.Task and NiceGUI TaskProxy
        if debounce_task is not None:
            try:
                if hasattr(debounce_task, 'done'):
                    if not debounce_task.done():
                        debounce_task.cancel()
                else:
                    # no done(): best-effort cancel
                    debounce_task.cancel()
            except Exception:
                pass
         

        # Defer scheduling until NiceGUI event loop is available
        debounce_task = schedule_bg(save_config_delayed(), name='save_sensitivity')

    # ---------- Event-Handler ------------------------------------------------
    def reset_roi() -> None:
        state['points'] = []
        refresh_snapshot()
        refresh_ui()

    def undo_last_point() -> None:
        if state['points']:
            state['points'].pop()
            refresh_ui()

    def _handle_click(e: MouseEventArguments) -> None:
        x, y = int(e.image_x), int(e.image_y)
        state['points'].append((x, y))
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
        points = state['points']
        if len(points) < 3:
            ui.notify('Please select at least 3 points for a polygon!', type='warning', position='bottom-right')
            return

        roi_enabled = roi_enabled_checkbox.value if roi_enabled_checkbox else True
        
        # Calculate bounding box for backward compatibility
        pts_np = np.array(points, dtype=np.int32)
        x, y, w, h = cv2.boundingRect(pts_np)
        
        if camera is None:
            ui.notify('Camera not available!', type='warning', position='bottom-right')
            return
            
        cam = camera
        if cam is not None and cam.motion_detector is not None:
            md = cam.motion_detector
            roi = md.roi
            
            # Update runtime object
            roi.x = x
            roi.y = y
            roi.width = w
            roi.height = h
            roi.enabled = roi_enabled
            if hasattr(roi, 'points'):
                roi.points = [list(p) for p in points] # Store as list of lists
            
            md.reset_background_model()

            roi_data = {
                'enabled': roi_enabled,
                'x': x,
                'y': y,
                'width': w,
                'height': h,
                'points': [list(p) for p in points]
            }

            try:
                if config:
                    config.motion_detection.region_of_interest = roi_data
                    save_global_config()
                    logger.info('ROI config saved to config')
                else:
                    ui.notify('ROI config could not be saved to config!', type='warning',
                                position='bottom-right')
                    logger.warning('ROI config could not be saved to config!')
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
            
            refresh_ui()
            ui.notify(f'Polygon ROI saved: {len(points)} points', type='positive',
                    position='bottom-right')
            logger.info(f'ROI saved: {roi_data}')
    
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
                    src = f"data:image/jpeg;base64,{base64.b64encode(buffer.tobytes()).decode()}"
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
        create_heading_row(
            'Motion Detection Settings',
            icon=SECTION_ICONS['motion'],
            title_classes='text-h6 font-semibold mb-2',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
        with ui.column().style("align-self:stretch;").classes('gap-4'):

            # ── Sensitivität in eigener Card ────────────────────────────────
            with ui.card().classes('w-full').style("align-items:center;"):
                create_heading_row(
                    'Sensitivity',
                    icon='tune',
                    title_classes='font-semibold mb-2',
                    row_classes='items-center gap-2 self-start',
                    icon_classes='text-primary text-lg shrink-0',
                )
                with ui.row().classes('justify-center gap-4'):
                    # Bind change handler after loop is ready to prevent early invocation during build
                    sensitivity_knob = ui.knob(
                        min=0, max=100, value=10, step=1, show_value=True
                    ).tooltip('Adjust motion detection sensitivity (0-100%)')
                    def _bind_sensitivity() -> None:
                        if sensitivity_knob:
                            def _on_change(e: Any) -> None:
                                # Read value only from the generic args dict to satisfy type checker
                                val = None
                                try:
                                    args = getattr(e, 'args', None)
                                    if isinstance(args, dict):
                                        val = args.get('value')
                                except Exception:
                                    val = None
                                if val is None:
                                    return
                                try:
                                    update_sensitivity(int(val))
                                except Exception:
                                    # ignore non-integer values gracefully
                                    return
                            sensitivity_knob.on('change', _on_change)
                    ui.timer(0.0, _bind_sensitivity, once=True)

            ui.separator()

            # ── ROI-Bereich jetzt ebenfalls in eigener Card ─────────────────
            with ui.card().classes('w-full').style("align-self:stretch;"):
                with ui.column().classes('w-full gap-2'):
                    create_heading_row(
                        'Region of Interest (ROI)',
                        icon='crop_free',
                        title_classes='font-semibold mb-2',
                        row_classes='items-center gap-2 self-start',
                        icon_classes='text-primary text-lg shrink-0',
                    )
                    # Kopfzeile mit Titel und Edit-Button
                    # Bind ROI toggle handler after loop is ready to prevent early invocation
                    roi_enabled_checkbox = ui.checkbox('ROI enabled', value=True).tooltip('Enable/disable Region of Interest')
                    def _bind_roi_toggle() -> None:
                        if roi_enabled_checkbox:
                            def _on_roi_change(e: Any) -> None:
                                val = None
                                try:
                                    args = getattr(e, 'args', None)
                                    if isinstance(args, dict):
                                        val = args.get('value')
                                    elif hasattr(e, 'value'):
                                        val = e.value
                                except Exception:
                                    val = None
                                if val is None:
                                    return
                                try:
                                    update_roi_enabled(bool(val))
                                except Exception:
                                    return
                            roi_enabled_checkbox.on('change', _on_roi_change)
                    ui.timer(0.0, _bind_roi_toggle, once=True)
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

                        create_heading_row(
                            'ROI Editor (Polygon)',
                            icon='polyline',
                            title_classes='font-semibold mb-2',
                            row_classes='items-center gap-2 self-start',
                            icon_classes='text-primary text-lg shrink-0',
                        )
                        ui.label('Click to add points. At least 3 points required.').classes('text-sm text-gray-500')

                        frame: np.ndarray | None = None
                        image_src = IMG_SRC
                        if camera is not None:
                            try:
                                frame = camera.take_snapshot()
                                if frame is not None:
                                    success, buffer = cv2.imencode('.jpg', frame)
                                    if success:
                                        image_src = (
                                            f"data:image/jpeg;base64,{base64.b64encode(buffer.tobytes()).decode('utf-8')}"
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
                            points_label = ui.label('No points selected').classes('font-mono text-xs')

                        # Live-Mauskoordinaten (rechtsbündig, grau)
                        with ui.row().classes('justify-end'):
                            coords_label = ui.label('(-, -)')\
                                .classes('text-sm font-mono text-gray-500')

                        # Aktions-Buttons
                        with ui.row().classes('gap-2'):
                            ui.button(icon='save', color='primary', on_click=save_roi)\
                                .classes('flex-grow').props('round').tooltip('save polygon')
                            ui.button(icon='undo', color='warning', on_click=undo_last_point)\
                                .classes('flex-grow').props('round').tooltip('undo last point')
                            ui.button(icon='restart_alt', color='secondary', on_click=reset_roi)\
                                .classes('flex-grow').props('round').tooltip('clear all')
                            ui.button(icon='close', color='negative', on_click=toggle_roi_editor)\
                                .classes('flex-grow').props('round').tooltip('close')

                    # Editor initial versteckt
                    roi_editor_container.set_visibility(False)

    initialize_from_config()
    refresh_ui()
