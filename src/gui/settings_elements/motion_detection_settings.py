from __future__ import annotations
from typing import Optional, cast, Any
import asyncio

from nicegui import ui

from src.cam.camera import Camera, MotionDetector
from src.gui.util import schedule_bg
from src.config import get_global_config, save_global_config, get_logger

logger = get_logger('gui.motion')


def create_motiondetection_card(camera: Optional[Camera] = None) -> None:
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

    def _bind_controls(number_ctrl: Any, slider_ctrl: Any) -> None:
        syncing = {'from_slider': False, 'from_number': False}

        def _clamp(value: Any) -> float:
            try:
                v = float(value)
            except Exception:
                current = getattr(number_ctrl, 'value', 10)
                try:
                    v = float(current)
                except Exception:
                    v = 10.0
            v = max(0.0, v)
            v = min(100.0, v)
            return v

        def _from_slider(event: Any, commit: bool) -> None:
            if syncing['from_number']:
                return
            raw = getattr(event, 'value', getattr(slider_ctrl, 'value', 10))
            value = _clamp(raw)
            syncing['from_slider'] = True
            try:
                _set_control_value(number_ctrl, int(round(value)))
                if commit:
                    update_sensitivity(int(round(value)))
            finally:
                syncing['from_slider'] = False

        def _from_number(event: Any, commit: bool) -> None:
            if syncing['from_slider']:
                return
            raw = getattr(event, 'value', getattr(number_ctrl, 'value', 10)) if event is not None else getattr(number_ctrl, 'value', 10)
            value = _clamp(raw)
            syncing['from_number'] = True
            try:
                _set_control_value(slider_ctrl, value)
                if commit:
                    update_sensitivity(int(round(value)))
            finally:
                syncing['from_number'] = False

        slider_ctrl.on('update:model-value', lambda e: _from_slider(e, False))
        slider_ctrl.on('change', lambda e: _from_slider(e, True))
        number_ctrl.on('update:model-value', lambda e: _from_number(e, True))
        number_ctrl.on('blur', lambda e: _from_number(e, True))

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
            cam = cast(Camera, camera)
            md = cast(MotionDetector, cam.motion_detector)
            if hasattr(md, 'sensitivity'):
                sens_value = int(md.sensitivity * 100)
                if sensitivity_number is not None:
                    _set_control_value(sensitivity_number, sens_value)
                if sensitivity_slider is not None:
                    _set_control_value(sensitivity_slider, sens_value)
        except Exception:
            pass

    ui.label('Motion Detection Settings').classes('text-h6 font-semibold mb-2')
    with ui.card().classes('w-full').style('align-items:center;'):
        ui.label('Sensitivity:').classes('font-semibold mb-2 self-start')
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

            _bind_controls(sensitivity_number, sensitivity_slider)
            try:
                notify_client = getattr(sensitivity_number, 'client', None)
            except Exception:
                notify_client = None

    initialize_from_config()