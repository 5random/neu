from typing import Optional, Tuple
import time

from nicegui import ui

from src.cam.camera import Camera
from src.config import get_logger, save_global_config, get_global_config

logger = get_logger('gui.camfeed')


def create_camfeed_content(camera: Optional[Camera] = None) -> None:
    """Render the live camera feed with an integrated ROI editor.

    - Uses the streaming endpoint /video/frame for the image.
    - Maintains correct aspect ratio and coordinate mapping.
    - Allows selecting ROI corners with clicks and saving to config/camera.
    """
    logger.info('Creating camera feed with ROI editor')

    if camera is None:
        ui.label('⚠️ Camera not available').classes('text-red')
        return

    # Determine image resolution to preserve aspect ratio
    IMG_W, IMG_H = 720, 405
    try:
        status = camera.get_camera_status()
        if status and status.get('resolution'):
            IMG_W = int(status['resolution']['width'])
            IMG_H = int(status['resolution']['height'])
    except Exception:
        pass

    # ROI state and UI refs
    state: dict[str, Optional[Tuple[int, int]]] = {'p1': None, 'p2': None}
    image = None
    tl_label = None
    br_label = None
    coords_label = None
    roi_enabled_checkbox = None

    def svg_cross(x: int, y: int, s: int = 14, col: str = 'deepskyblue') -> str:
        dis_scale = 300 / IMG_H if IMG_H > 300 else 1.0
        h = int(s / dis_scale) // 2
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

    def roi_bounds() -> Optional[Tuple[int, int, int, int]]:
        if state['p1'] and state['p2']:
            x0, y0 = map(min, zip(state['p1'], state['p2']))
            x1, y1 = map(max, zip(state['p1'], state['p2']))
            return x0, y0, x1, y1
        return None

    def update_overlay() -> None:
        nonlocal image
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
        nonlocal tl_label, br_label
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

    def initialize_from_config() -> None:
        nonlocal roi_enabled_checkbox
        try:
            md = camera.motion_detector
            if md and hasattr(md, 'roi'):
                roi = md.roi
                if roi_enabled_checkbox is not None:
                    roi_enabled_checkbox.set_value(getattr(roi, 'enabled', False))
                if getattr(roi, 'enabled', False):
                    x0, y0 = roi.x, roi.y
                    x1, y1 = roi.x + roi.width, roi.y + roi.height
                    state['p1'] = (x0, y0)
                    state['p2'] = (x1, y1)
                else:
                    state['p1'] = state['p2'] = None
                update_overlay()
                update_labels()
        except Exception:
            pass

    def _apply_roi_to_config(enabled: bool, x0: int, y0: int, w: int, h: int) -> None:
        """Persist ROI to global config and camera.app_config, supporting dict or dataclass ROI."""
        def _update_roi_container(container) -> None:
            try:
                if container is None:
                    return
                md_cfg = getattr(container, 'motion_detection', None)
                roi = getattr(md_cfg, 'region_of_interest', None) if md_cfg is not None else None
                if roi is None:
                    # If dict-based config, create dict
                    if isinstance(md_cfg, dict):
                        md_cfg['region_of_interest'] = {
                            'enabled': enabled, 'x': x0, 'y': y0, 'width': w, 'height': h,
                        }
                    return
                # Dataclass-like ROI with attributes
                if hasattr(roi, 'x') and hasattr(roi, 'width'):
                    roi.enabled = enabled
                    roi.x = x0; roi.y = y0; roi.width = w; roi.height = h
                else:
                    # Dict-like ROI
                    try:
                        roi['enabled'] = enabled
                        roi['x'] = x0; roi['y'] = y0; roi['width'] = w; roi['height'] = h
                    except Exception:
                        pass
            except Exception:
                logger.exception('Failed to update ROI container')

        try:
            cfg = get_global_config()
            if cfg and hasattr(cfg, 'motion_detection') and hasattr(cfg.motion_detection, 'region_of_interest'):
                roi_obj = cfg.motion_detection.region_of_interest
                # Dataclass-like ROI
                if not isinstance(roi_obj, dict) and all(hasattr(roi_obj, attr) for attr in ('x', 'y', 'width', 'height', 'enabled')):
                    try:
                        setattr(roi_obj, 'enabled', enabled)
                        setattr(roi_obj, 'x', x0)
                        setattr(roi_obj, 'y', y0)
                        setattr(roi_obj, 'width', w)
                        setattr(roi_obj, 'height', h)
                    except Exception:
                        # Fall back to dict replacement if attribute setting fails
                        cfg.motion_detection.region_of_interest = {
                            'enabled': enabled, 'x': x0, 'y': y0, 'width': w, 'height': h,
                        }
                else:
                    # Dict-like or unknown: replace with dict safely
                    cfg.motion_detection.region_of_interest = {
                        'enabled': enabled, 'x': x0, 'y': y0, 'width': w, 'height': h,
                    }
                save_global_config()
        except Exception:
            logger.exception('Failed to persist ROI to global config')

        # Optionally mirror into camera.app_config if it truly persists whole app_config
        try:
            if camera and getattr(camera, 'app_config', None):
                _update_roi_container(camera.app_config)
                # Do not call camera.save_uvc_config() here; ROI is not a UVC control.
        except Exception:
            logger.exception('Failed to mirror ROI into camera.app_config')

    def update_roi_enabled(enabled: bool) -> None:
        try:
            md = camera.motion_detector
            if not md:
                return
            md.roi.enabled = enabled
            _apply_roi_to_config(enabled, md.roi.x, md.roi.y, md.roi.width, md.roi.height)
            md.reset_background_model()
            ui.notify(f'ROI {"enabled" if enabled else "disabled"}', type='positive', position='bottom-right')
        except Exception as exc:
            logger.error('Failed to toggle ROI: %s', exc, exc_info=True)

    def save_roi() -> None:
        try:
            b = roi_bounds()
            if not b:
                ui.notify('Select two corners first', type='warning', position='bottom-right')
                return
            x0, y0, x1, y1 = b
            # Clamp to bounds
            x0 = max(0, min(x0, IMG_W - 1))
            y0 = max(0, min(y0, IMG_H - 1))
            x1 = max(x0 + 1, min(x1, IMG_W))
            y1 = max(y0 + 1, min(y1, IMG_H))
            roi_w = x1 - x0
            roi_h = y1 - y0
            min_size = 30
            if roi_w < min_size or roi_h < min_size:
                cx = (x0 + x1) // 2
                cy = (y0 + y1) // 2
                x0 = max(0, cx - min_size // 2)
                y0 = max(0, cy - min_size // 2)
                x1 = min(IMG_W, x0 + min_size)
                y1 = min(IMG_H, y0 + min_size)
                roi_w = x1 - x0
                roi_h = y1 - y0
            md = camera.motion_detector
            if not md:
                ui.notify('Motion detector not available', type='warning', position='bottom-right')
                return
            roi_en = bool(roi_enabled_checkbox.value) if roi_enabled_checkbox else True
            md.roi.x = x0
            md.roi.y = y0
            md.roi.width = roi_w
            md.roi.height = roi_h
            md.roi.enabled = roi_en
            md.reset_background_model()

            _apply_roi_to_config(roi_en, x0, y0, roi_w, roi_h)
            state['p1'] = (x0, y0)
            state['p2'] = (x1, y1)
            update_overlay()
            update_labels()
            ui.notify('ROI saved and applied', type='positive', position='bottom-right')
        except Exception as exc:
            logger.error('Failed to save ROI: %s', exc, exc_info=True)
            ui.notify(f'Error saving ROI: {exc}', type='warning', position='bottom-right')

    def reset_roi() -> None:
        state['p1'] = state['p2'] = None
        update_overlay()
        update_labels()

    def handle_mouse(e):
        nonlocal coords_label
        try:
            if coords_label is not None:
                if getattr(e, 'type', '') == 'mouseleave':
                    coords_label.text = '(-, -)'
                else:
                    ix = int(getattr(e, 'image_x', 0))
                    iy = int(getattr(e, 'image_y', 0))
                    coords_label.text = f'({ix}, {iy})'
            if getattr(e, 'type', '') == 'click':
                ix = int(getattr(e, 'image_x', 0))
                iy = int(getattr(e, 'image_y', 0))
                target = 'p1' if state['p1'] is None else ('p2' if state['p2'] is None else None)
                if target:
                    state[target] = (ix, iy)
                else:
                    # third click starts new selection
                    reset_roi()
                    state['p1'] = (ix, iy)
                update_overlay()
                update_labels()
        except Exception:
            pass

    # Layout: Live image with toolbar under it
    with ui.column().classes('w-full gap-2'):
        # Ensure correct aspect ratio for coordinate mapping
        ratio_style = f"aspect-ratio:{IMG_W}/{IMG_H};width:100%;height:auto;"
        image = (
            ui.interactive_image(
                f'/video/frame?{time.time()}',
                on_mouse=handle_mouse,
                events=['click', 'move', 'mouseleave'],
                cross='#19bfd2',
            )
            .style(ratio_style)
            .classes('rounded-borders')
        )

        # Update the streaming source periodically
        ui.timer(0.2, lambda: image.set_source(f'/video/frame?{time.time()}'))

        # Toolbar: ROI enable, save/reset, coords and labels
        with ui.row().classes('items-center gap-2 w-full'):
            roi_enabled_checkbox = ui.checkbox('ROI enabled', value=True).tooltip('Enable/disable Region of Interest')
            roi_enabled_checkbox.on('change', lambda e: update_roi_enabled(bool(getattr(e, 'value', True))))
            ui.button(icon='save', color='primary', on_click=save_roi).props('round').tooltip('save')
            ui.button(icon='restart_alt', color='secondary', on_click=reset_roi).props('round').tooltip('reset')
            ui.space()
            coords_label = ui.label('(-, -)').classes('text-sm font-mono text-gray-500')

        with ui.row().classes('items-center gap-4 text-sm'):
            ui.label('upper left:')
            tl_label = ui.label('-').classes('font-mono')
            ui.label('bottom right:')
            br_label = ui.label('-').classes('font-mono')

    # Initialize state from current config/camera
    initialize_from_config()
    update_overlay()
    update_labels()