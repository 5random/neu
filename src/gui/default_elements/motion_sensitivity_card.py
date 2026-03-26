from __future__ import annotations

import asyncio
from typing import Any, Optional

from nicegui import ui

from src.cam.camera import Camera
from src.config import get_global_config, get_logger, save_global_config
from src.gui.bindings import bind_number_slider
from src.gui.util import schedule_bg

logger = get_logger('gui.motion_sensitivity')


def create_motion_sensitivity_card(camera: Optional[Camera] = None) -> None:
    """Render a compact dashboard card for motion sensitivity only."""
    config = get_global_config()
    sensitivity_number: Any = None
    sensitivity_slider: Any = None
    debounce_task = None

    def _set_control_value(control: Any, value: int) -> None:
        try:
            if hasattr(control, 'set_value'):
                control.set_value(value)
            else:
                control.value = value
                if hasattr(control, 'update'):
                    control.update()
        except Exception:
            logger.exception('Failed to update motion sensitivity control')

    def _current_sensitivity() -> int:
        if camera is not None and camera.motion_detector is not None:
            try:
                return int(camera.motion_detector.sensitivity * 100)
            except Exception:
                logger.exception('Failed to read motion detector sensitivity')
        if config is not None:
            try:
                return int(config.motion_detection.sensitivity * 100)
            except Exception:
                logger.exception('Failed to read configured motion sensitivity')
        return 10

    def _persist_sensitivity(raw_value: int) -> None:
        nonlocal debounce_task

        if camera is None or camera.motion_detector is None:
            ui.notify('Camera not available', type='warning', position='bottom-right')
            return

        motion_detector = camera.motion_detector
        if motion_detector is None:
            ui.notify('Motion detector not available', type='warning', position='bottom-right')
            return
        sensitivity = max(0.01, raw_value / 100.0)
        motion_detector.update_sensitivity(sensitivity)

        async def _save() -> None:
            await asyncio.sleep(0.5)
            try:
                latest_config = get_global_config()
                if latest_config is None:
                    logger.error('Global config unavailable while saving motion sensitivity')
                    ui.notify(
                        'Sensitivity could not be saved',
                        type='negative',
                        position='bottom-right',
                    )
                    return

                latest_config.motion_detection.sensitivity = sensitivity
                if not save_global_config():
                    logger.error('save_global_config returned False while saving motion sensitivity')
                    ui.notify(
                        'Sensitivity could not be saved',
                        type='negative',
                        position='bottom-right',
                    )
                    return

                ui.notify('Sensitivity saved', type='positive', position='bottom-right')
            except Exception:
                logger.exception('Failed to save motion sensitivity')
                ui.notify(
                    'Sensitivity could not be saved',
                    type='negative',
                    position='bottom-right',
                )

        if debounce_task is not None:
            try:
                if not debounce_task.done():
                    debounce_task.cancel()
            except Exception:
                pass

        debounce_task = schedule_bg(_save(), name='save_motion_sensitivity')

    with ui.card().classes('w-full').style('align-items:stretch;'):
        ui.label('Motion Detection Sensitivity').classes('text-h6 font-semibold')
        ui.label('Adjust how sensitive the motion detection should react.').classes(
            'text-caption text-grey-7'
        )

        with ui.row().classes('items-center gap-3 w-full mt-2 no-wrap'):
            sensitivity_number = (
                ui.number(
                    value=_current_sensitivity(),
                    min=0,
                    max=100,
                    step=1,
                    format='%.0f',
                )
                .props('dense outlined suffix="%" hide-bottom-space')
                .classes('w-28 min-w-[7rem]')
            )
            sensitivity_slider = (
                ui.slider(
                    min=0,
                    max=100,
                    step=1,
                    value=_current_sensitivity(),
                )
                .classes('flex-1')
                .tooltip('Motion sensitivity in percent')
            )

        bind_number_slider(
            sensitivity_number,
            sensitivity_slider,
            min_value=0,
            max_value=100,
            as_int=True,
            on_change=lambda value: _persist_sensitivity(int(value)),
        )

        if camera is None or camera.motion_detector is None:
            sensitivity_number.disable()
            sensitivity_slider.disable()
            ui.label('No active camera available.').classes('text-caption text-warning')
        else:
            initial_value = _current_sensitivity()
            _set_control_value(sensitivity_number, initial_value)
            _set_control_value(sensitivity_slider, initial_value)
