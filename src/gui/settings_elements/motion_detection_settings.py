from __future__ import annotations
from typing import Optional, cast, Any
import asyncio

from nicegui import ui

from src.cam.camera import Camera, MotionDetector
from src.gui.util import schedule_bg
from src.config import get_global_config, save_global_config, get_logger
from src.gui.bindings import bind_number_slider
from src.gui.settings_elements.ui_helpers import create_heading_row

logger = get_logger('gui.motion')


def create_motiondetection_card(camera: Optional[Camera] = None, *, show_header: bool = True) -> None:
    """Motion Detection settings: Sensitivity only.

    All ROI functionality has been moved to camfeed_settings.create_camfeed_content.
    """
    config = get_global_config()

    sensitivity_number: Any = None
    sensitivity_slider: Any = None
    notify_client: Any = None
    debounce_task = None

    def _set_control_value(ctrl: Any, value: Any) -> None:
        try:
            if hasattr(ctrl, 'set_value'):
                ctrl.set_value(value)
            else:
                setattr(ctrl, 'value', value)
                if hasattr(ctrl, 'update'):
                    ctrl.update()
        except Exception:
            pass

    def update_sensitivity(value: int) -> None:
        """Update MotionDetector sensitivity and persist with debounce."""
        nonlocal debounce_task
        if camera is None or camera.motion_detector is None:
            return
        motion_detector = camera.motion_detector
        sens_value = max(0.01, value / 100.0)
        motion_detector.update_sensitivity(sens_value)

        async def save_config_delayed() -> None:
            await asyncio.sleep(0.5)
            try:
                if config:
                    config.motion_detection.sensitivity = sens_value
                    save_global_config()
                    logger.info(f'Saved sensitivity: {sens_value}')
                    if notify_client:
                        with notify_client:
                            ui.notify('Sensitivity saved', type='positive', position='bottom-right')
            except Exception:
                logger.exception('Saving sensitivity failed')
                if notify_client:
                    with notify_client:
                        ui.notify('Saving sensitivity failed', type='negative', position='bottom-right')

        if debounce_task is not None and not debounce_task.done():
            try:
                debounce_task.cancel()
            except Exception:
                pass

        debounce_task = schedule_bg(save_config_delayed(), name='save_sensitivity')

    def initialize_from_config() -> None:
        """Initialize knob from detector if available."""
        nonlocal sensitivity_number, sensitivity_slider
        try:
            if camera is None or camera.motion_detector is None:
                return
            md = camera.motion_detector
            if hasattr(md, 'sensitivity'):
                sens_value = int(md.sensitivity * 100)
                if sensitivity_number is not None:
                    _set_control_value(sensitivity_number, sens_value)
                if sensitivity_slider is not None:
                    _set_control_value(sensitivity_slider, sens_value)
        except Exception:
            pass

    if show_header:
        create_heading_row(
            'Motion Detection Settings',
            icon='sensors',
            title_classes='text-h6 font-semibold mb-2',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
    with ui.card().classes('w-full').style('align-items:center;'):
        create_heading_row(
            'Sensitivity',
            icon='tune',
            title_classes='font-semibold mb-2',
            row_classes='items-center gap-2 self-start',
            icon_classes='text-primary text-lg shrink-0',
        )
        with ui.row().classes('justify-center gap-4 w-full'):
            sensitivity_number = (
                ui.number(
                    value=10,
                    min=0,
                    max=100,
                    step=1,
                    format='%.0f',
                )
                .props('dense outlined suffix="%"')
                .tooltip('Adjust motion detection sensitivity (0-100%)')
                .classes('min-w-[120px]')
            )
            sensitivity_slider = (
                ui.slider(min=0, max=100, step=1, value=10)
                .tooltip('Adjust motion detection sensitivity (0-100%)')
                .classes('flex-1')
            )

            bind_number_slider(
                sensitivity_number,
                sensitivity_slider,
                min_value=0,
                max_value=100,
                as_int=True,
                on_change=lambda v: update_sensitivity(int(v))
            )
            try:
                notify_client = getattr(sensitivity_number, 'client', None)
            except Exception:
                notify_client = None

    initialize_from_config()
