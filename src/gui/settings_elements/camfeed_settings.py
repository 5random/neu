from typing import Optional, Tuple, Any, cast
import time

from nicegui import ui

from src.cam.camera import Camera
from src.config import get_logger, save_global_config, get_global_config
from src.cam.motion import MotionDetector

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
    MIN_ROI_SIZE_PX = 30  # unified minimum ROI edge length for live validation
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
    roi_hint_label = None  # live hint for ROI size and disabled state
    # ROI numeric inputs and guard for re-entrancy
    x_input: Any = None
    y_input: Any = None
    w_input: Any = None
    h_input: Any = None
    _updating_inputs = False

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
        # Style depends on enabled state and current size
        try:
            enabled = bool(roi_enabled_checkbox.value) if roi_enabled_checkbox is not None else True
        except Exception:
            enabled = True

        # Always visualize current selection; adapt styling based on state
        if state['p1']:
            cross_col = '#19bfd2' if enabled else '#9aa0a6'  # blue vs gray
            parts.append(svg_cross(state['p1'][0], state['p1'][1], col=cross_col))
        if state['p2']:
            cross_col = '#19bfd2' if enabled else '#9aa0a6'
            parts.append(svg_cross(state['p2'][0], state['p2'][1], col=cross_col))
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            w = max(1, x1 - x0)
            h = max(1, y1 - y0)
            too_small = (w < MIN_ROI_SIZE_PX) or (h < MIN_ROI_SIZE_PX)

            if not enabled:
                rect_style = 'stroke="#9aa0a6" stroke-width="3" stroke-dasharray="8,6" stroke-opacity="0.9" fill="none"'
            elif too_small:
                rect_style = 'stroke="orange" stroke-width="3" stroke-dasharray="4,4" fill="none"'
            else:
                rect_style = 'stroke="lime" stroke-width="3" fill="none"'

            parts.append(
                f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" '
                f'{rect_style} pointer-events="none" vector-effect="non-scaling-stroke" />'
            )
            parts.extend([svg_circle(x0, y0, col=("#19bfd2" if enabled else "#9aa0a6")),
                         svg_circle(x1, y1, col=("#19bfd2" if enabled else "#9aa0a6"))])

            # Optional inline text hint within overlay for clarity
            if not enabled:
                cx = (x0 + x1) // 2
                cy = max(14, y0 + 16)
                parts.append(
                    f'<text x="{cx}" y="{cy}" fill="#9aa0a6" font-size="14" text-anchor="middle" '
                    'pointer-events="none">ROI disabled</text>'
                )
            elif too_small:
                cx = (x0 + x1) // 2
                cy = max(14, y0 + 16)
                parts.append(
                    f'<text x="{cx}" y="{cy}" fill="orange" font-size="14" text-anchor="middle" '
                    'pointer-events="none">min size '
                    f'{MIN_ROI_SIZE_PX}px</text>'
                )
        overlay = ''.join(parts)
        try:
            image.set_content(overlay)
        except Exception:
            try:
                image.content = overlay
            except Exception:
                pass

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

    def update_roi_hint() -> None:
        """Update the live indicator labeling current ROI size and disabled state."""
        nonlocal roi_hint_label
        if roi_hint_label is None:
            return
        try:
            enabled = bool(roi_enabled_checkbox.value) if roi_enabled_checkbox is not None else True
        except Exception:
            enabled = True
        b = roi_bounds()
        if not enabled:
            roi_hint_label.text = 'ROI disabled'
            roi_hint_label.classes(remove='text-warning text-positive', add='text-grey')
            roi_hint_label.visible = True
            return
        if not b:
            roi_hint_label.text = ''
            roi_hint_label.visible = False
            return
        x0, y0, x1, y1 = b
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        if w < MIN_ROI_SIZE_PX or h < MIN_ROI_SIZE_PX:
            roi_hint_label.text = f'ROI too small: {w}×{h} px (min {MIN_ROI_SIZE_PX}px)'
            roi_hint_label.classes(remove='text-grey text-positive', add='text-warning')
            roi_hint_label.visible = True
        else:
            roi_hint_label.text = f'ROI: {w}×{h} px'
            roi_hint_label.classes(remove='text-grey text-warning', add='text-positive')
            roi_hint_label.visible = True

    def update_inputs_from_state() -> None:
        """Reflect current ROI state into numeric inputs without recursion."""
        nonlocal x_input, y_input, w_input, h_input, _updating_inputs
        if x_input is None or y_input is None or w_input is None or h_input is None:
            return
        b = roi_bounds()
        try:
            _updating_inputs = True
            if b:
                x0, y0, x1, y1 = b
                # Prefer set_value when available; fall back to .value
                for ctrl, val in (
                    (x_input, int(x0)),
                    (y_input, int(y0)),
                    (w_input, int(max(1, x1 - x0))),
                    (h_input, int(max(1, y1 - y0))),
                ):
                    try:
                        ctrl.set_value(val)
                    except Exception:
                        try:
                            ctrl.value = val
                        except Exception:
                            pass
            else:
                # Safely clear inputs; avoid None if widget rejects it
                for ctrl in (x_input, y_input, w_input, h_input):
                    try:
                        ctrl.set_value(0)
                    except Exception:
                        try:
                            ctrl.value = 0
                        except Exception:
                            pass
        finally:
            _updating_inputs = False

    def clamp_roi_values(x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
        """Clamp ROI to image bounds using MotionDetector.normalize_roi for consistency."""
        try:
            nx, ny, nw, nh = MotionDetector.normalize_roi(int(x), int(y), int(w), int(h), int(IMG_W), int(IMG_H), min_size=1)
            return nx, ny, nw, nh
        except Exception:
            # Fallback to local clamp logic if anything goes wrong
            x = max(0, min(int(x), int(IMG_W) - 1))
            y = max(0, min(int(y), int(IMG_H) - 1))
            w = max(1, int(w))
            h = max(1, int(h))
            if x + w > IMG_W:
                w = int(IMG_W) - x
            if y + h > IMG_H:
                h = int(IMG_H) - y
            return x, y, w, h

    def update_state_from_inputs(_: Any = None) -> None:
        """Apply numeric input values to state and refresh overlay/labels."""
        nonlocal x_input, y_input, w_input, h_input, _updating_inputs
        if _updating_inputs or x_input is None or y_input is None or w_input is None or h_input is None:
            return
        try:
            # Helper to read values tolerant of intermediate states
            def _get_val(ctrl: Any) -> Optional[int]:
                try:
                    v = getattr(ctrl, 'value', None)
                except Exception:
                    v = None
                if v is None:
                    return None
                try:
                    return int(v)
                except Exception:
                    return None

            xv = _get_val(x_input)
            yv = _get_val(y_input)
            wv = _get_val(w_input)
            hv = _get_val(h_input)
            if any(v is None for v in (xv, yv, wv, hv)):
                return
            # Cast to satisfy static type checkers after the guard above
            x_i = cast(int, xv)
            y_i = cast(int, yv)
            w_i = cast(int, wv)
            h_i = cast(int, hv)
            x, y, w, h = clamp_roi_values(int(x_i), int(y_i), int(w_i), int(h_i))
            # Update inputs to clamped values to keep UI consistent
            try:
                _updating_inputs = True
                try:
                    x_input.set_value(x)
                    y_input.set_value(y)
                    w_input.set_value(w)
                    h_input.set_value(h)
                except Exception:
                    x_input.value = x
                    y_input.value = y
                    w_input.value = w
                    h_input.value = h
            finally:
                _updating_inputs = False
            # Set state (p1, p2) and refresh
            state['p1'] = (x, y)
            state['p2'] = (x + w, y + h)
            update_overlay()
            update_labels()
            update_roi_hint()
        except Exception:
            # Be tolerant to transient invalid input while typing
            pass

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
                update_inputs_from_state()
                update_roi_hint()
        except Exception:
            pass

    def _apply_roi_to_config(enabled: bool, x0: int, y0: int, w: int, h: int) -> None:
        """Persist ROI to global config and camera.app_config, supporting dict or dataclass ROI."""
        def _update_roi_container(container: Any) -> None:
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
            # Base clamp/normalize using MotionDetector helper
            base_w = max(1, x1 - x0)
            base_h = max(1, y1 - y0)
            nx, ny, nw, nh = MotionDetector.normalize_roi(x0, y0, base_w, base_h, IMG_W, IMG_H, min_size=1)
            x0, y0, roi_w, roi_h = nx, ny, nw, nh
            x1, y1 = x0 + roi_w, y0 + roi_h
            min_size = MIN_ROI_SIZE_PX
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
            update_inputs_from_state()
            update_roi_hint()
            ui.notify('ROI saved and applied', type='positive', position='bottom-right')
        except Exception as exc:
            logger.error('Failed to save ROI: %s', exc, exc_info=True)
            ui.notify(f'Error saving ROI: {exc}', type='warning', position='bottom-right')

    def reset_roi() -> None:
        state['p1'] = state['p2'] = None
        update_overlay()
        update_labels()
        update_inputs_from_state()
        update_roi_hint()

    def handle_mouse(e: Any) -> None:
        nonlocal coords_label
        try:
            if coords_label is not None:
                if getattr(e, 'type', '') == 'mouseleave':
                    coords_label.text = '(-, -)'
                else:
                    try:
                        ix = int(getattr(e, 'image_x', 0))
                        iy = int(getattr(e, 'image_y', 0))
                    except Exception:
                        ix, iy = 0, 0
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
                update_inputs_from_state()
                update_roi_hint()
        except Exception:
            pass

    # Layout: Live image with toolbar under it
    with ui.column().classes('w-full gap-2'):
        # Ensure correct aspect ratio for coordinate mapping
        # Avoid fixed aspect-ratio (can desync if camera stream resolution differs); let the image keep its natural ratio
        ratio_style = "width:100%;height:auto;"
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

        # Update the streaming source periodically (with fallback for older NiceGUI versions)
        # Issue #14 fix: Check capability once to avoid repeated try-except
        has_set_source = hasattr(image, 'set_source')
        
        def _refresh_stream() -> None:
            src = f'/video/frame?{time.time()}'
            if has_set_source:
                image.set_source(src)
            else:
                try:
                    image.source = src
                except Exception:
                    pass
        # Ensure only one refresh timer per client for the settings camfeed
        try:
            client = ui.context.client
            try:
                prev = getattr(client, 'cvd_camfeed_timer_settings', None)
                if prev:
                    try:
                        prev.cancel()
                    except Exception:
                        pass
            except Exception:
                pass

            timer = ui.timer(0.2, _refresh_stream)
            try:
                setattr(client, 'cvd_camfeed_timer_settings', timer)
            except Exception:
                pass

            def _cleanup() -> None:
                try:
                    timer.cancel()
                except Exception:
                    pass
                try:
                    if getattr(client, 'cvd_camfeed_timer_settings', None) is timer:
                        delattr(client, 'cvd_camfeed_timer_settings')
                except Exception:
                    pass

            client.on_disconnect(_cleanup)
        except Exception:
            # Fallback: create a simple timer without guards
            ui.timer(0.2, _refresh_stream)

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

        # ROI numeric inputs (x, y, w, h) with live sync
        with ui.row().classes('items-center gap-3 w-full'):
            x_input = ui.number(label='x', value=None, min=0, max=IMG_W - 1, step=1, format='%.0f').props('dense outlined suffix="px"')
            y_input = ui.number(label='y', value=None, min=0, max=IMG_H - 1, step=1, format='%.0f').props('dense outlined suffix="px"')
            w_input = ui.number(label='w', value=None, min=1, max=IMG_W, step=1, format='%.0f').props('dense outlined suffix="px"')
            h_input = ui.number(label='h', value=None, min=1, max=IMG_H, step=1, format='%.0f').props('dense outlined suffix="px"')

            # Wire input change events for live updates
            for control in (x_input, y_input, w_input, h_input):
                control.on('update:model-value', update_state_from_inputs)
                control.on('change', update_state_from_inputs)

        # Live hint below inputs: ROI size and min-size warning / disabled state
        with ui.row().classes('items-center gap-2 w-full'):
            roi_hint_label = ui.label('').classes('text-sm text-grey')

        # Enable/disable inputs based on ROI enabled state
        def _sync_inputs_enabled() -> None:
            try:
                enabled = bool(roi_enabled_checkbox.value) if roi_enabled_checkbox else True
                for control in (x_input, y_input, w_input, h_input):
                    if control is None:
                        continue
                    if enabled:
                        control.enable()
                    else:
                        control.disable()
                update_roi_hint()
            except Exception:
                pass
        roi_enabled_checkbox.on('change', lambda e: _sync_inputs_enabled())

    # Initialize state from current config/camera
    initialize_from_config()
    update_overlay()
    update_labels()
    update_inputs_from_state()
    update_roi_hint()
    # Ensure inputs reflect enabled/disabled state at start using helper
    try:
        _sync_inputs_enabled()
    except Exception:
        pass