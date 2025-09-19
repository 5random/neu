from __future__ import annotations
from typing import Optional, cast
import asyncio

from nicegui import ui
from nicegui.elements.knob import Knob

from src.cam.camera import Camera, MotionDetector
from src.gui.util import schedule_bg
from src.config import get_global_config, save_global_config, get_logger

logger = get_logger('gui.motion')


def create_motiondetection_card(camera: Optional[Camera] = None) -> None:
    """Motion Detection settings: Sensitivity only.

    All ROI functionality has been moved to camfeed_settings.create_camfeed_content.
    """
    config = get_global_config()

    sensitivity_knob: Knob | None = None
    debounce_task = None

    def update_sensitivity(value: int) -> None:
        """Update MotionDetector sensitivity and persist with debounce."""
        nonlocal debounce_task
        if camera is None or camera.motion_detector is None:
            return
        cam = cast(Camera, camera)
        md = cast(MotionDetector, cam.motion_detector)
        sens_value = max(0.01, value / 100.0)
        md.update_sensitivity(sens_value)

        async def save_config_delayed():
            await asyncio.sleep(0.5)
            try:
                if cam.motion_detector and config:
                    config.motion_detection.sensitivity = sens_value
                    save_global_config()
                    logger.info(f'Saved sensitivity: {sens_value}')
                    ui.notify('Sensitivity saved', type='positive', position='bottom-right')
            except Exception:
                logger.exception('Saving sensitivity failed')
                ui.notify('Saving sensitivity failed', type='negative', position='bottom-right')

        if debounce_task is not None and not debounce_task.done():
            try:
                debounce_task.cancel()
            except Exception:
                pass

        debounce_task = schedule_bg(save_config_delayed(), name='save_sensitivity')

    def initialize_from_config() -> None:
        """Initialize knob from detector if available."""
        try:
            if camera is None or camera.motion_detector is None:
                return
            cam = cast(Camera, camera)
            md = cast(MotionDetector, cam.motion_detector)
            if sensitivity_knob is not None and hasattr(md, 'sensitivity'):
                sens_value = int(md.sensitivity * 100)
                sensitivity_knob.set_value(sens_value)
        except Exception:
            pass

    ui.label('Motion Detection Settings').classes('text-h6 font-semibold mb-2')
    with ui.card().classes('w-full').style('align-items:center;'):
        ui.label('Sensitivity:').classes('font-semibold mb-2 self-start')
        with ui.row().classes('justify-center gap-4'):
            sensitivity_knob = ui.knob(min=0, max=100, value=10, step=1, show_value=True).tooltip('Adjust motion detection sensitivity (0-100%)')

            def _bind_sensitivity():
                if sensitivity_knob:
                    def _on_change(e):
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
                            return
                    sensitivity_knob.on('change', _on_change)
            ui.timer(0.0, _bind_sensitivity, once=True)

    initialize_from_config()