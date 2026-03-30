from nicegui import ui, background_tasks
from typing import Optional, Any, Callable
import asyncio

from src.cam.camera import Camera
from src.gui.util import schedule_bg, cancel_task_safely
from src.gui.uvc_helpers import auto_exposure_value_is_auto, set_nested_config_value
from src.config import get_logger, get_global_config, save_global_config

logger = get_logger('gui.uvc_knobs')

def create_uvc_content(camera: Optional[Camera] = None) -> None:
    if camera is None:
        logger.warning("Camera not available - UVC controls disabled")
        ui.label('⚠️ No Camera connected').classes('text-red')
        return
    
    logger.info("Creating UVC Card")

    knob_refs: dict[str, Any] = {}
    debounce_tasks: dict[str, Any] = {}

    # Helpers
    def _set_control_value(ctrl: Any, value: Any) -> None:
        try:
            if hasattr(ctrl, 'set_value'):
                ctrl.set_value(value)
            else:
                setattr(ctrl, 'value', value)
                if hasattr(ctrl, 'update'):
                    ctrl.update()
        except Exception as e:
            logger.debug(f'Failed to set control value: {e}')

    def _bind_numeric_pair(
        number_ctrl: Any,
        slider_ctrl: Any,
        *,
        min_value: float,
        max_value: float,
        as_int: bool,
        on_commit: Callable[[float | int], None],
    ) -> None:
        syncing = {'from_slider': False, 'from_number': False}

        def _fmt(value: float) -> float | int:
            return int(round(value)) if as_int else value

        def _clamp(value: Any) -> float:
            try:
                v = float(value)
            except Exception:
                current = getattr(number_ctrl, 'value', min_value)
                try:
                    v = float(current)
                except Exception:
                    v = min_value
            v = max(min_value, v)
            v = min(max_value, v)
            return v

        def _from_slider(event: Any, commit: bool) -> None:
            if syncing['from_number']:
                return
            raw = getattr(event, 'value', getattr(slider_ctrl, 'value', min_value))
            value = _clamp(raw)
            syncing['from_slider'] = True
            try:
                _set_control_value(number_ctrl, _fmt(value))
                if commit:
                    on_commit(_fmt(value))
            finally:
                syncing['from_slider'] = False

        def _from_number(event: Any, commit: bool) -> None:
            if syncing['from_slider']:
                return
            raw = getattr(event, 'value', getattr(number_ctrl, 'value', min_value)) if event is not None else getattr(number_ctrl, 'value', min_value)
            value = _clamp(raw)
            slider_value = value if not as_int else _fmt(value)
            syncing['from_number'] = True
            try:
                _set_control_value(slider_ctrl, slider_value)
                if commit:
                    on_commit(_fmt(value))
            finally:
                syncing['from_number'] = False

        slider_ctrl.on('update:model-value', lambda e: _from_slider(e, False))
        slider_ctrl.on('change', lambda e: _from_slider(e, True))
        number_ctrl.on('update:model-value', lambda e: _from_number(e, True))
        number_ctrl.on('blur', lambda e: _from_number(e, True))

    def _build_scalar_card(
        *,
        title: str,
        name: str,
        range_key: str,
        fallback: dict[str, float],
        step: float,
        tooltip: str,
        setter: Callable[[float | int], Any],
        config_field: str,
        is_float: bool = False,
    ) -> None:
        range_info = ranges.get(range_key, fallback)
        min_val = range_info.get('min', fallback['min'])
        max_val = range_info.get('max', fallback['max'])
        default_val = range_info.get('default', fallback['default'])
        raw_value = current.get(name, default_val)
        value = float(raw_value) if is_float else int(raw_value)

        # Compact card layout
        with ui.card().tight().classes('p-3 flex flex-col gap-1').style('align-items:stretch;'):
            ui.label(f'{title}').classes('text-caption font-medium text-grey-8')
            with ui.row().classes('items-center gap-2 w-full no-wrap'):
                slider_ctrl = (
                    ui.slider(min=min_val, max=max_val, step=step, value=value)
                    .tooltip(tooltip)
                    .classes('flex-1 min-w-[80px]')
                    .props('dense')
                )
                number_ctrl = (
                    ui.number(
                        value=value,
                        min=min_val,
                        max=max_val,
                        step=step,
                        format='%.1f' if is_float else '%.0f',
                    )
                    .props('dense outlined hide-bottom-space')
                    .tooltip(tooltip)
                    .classes('w-[60px]')
                    .style('font-size: 12px;')
                )

            commit = make_handler(setter, config_field, default_val)
            _bind_numeric_pair(
                number_ctrl,
                slider_ctrl,
                min_value=min_val,
                max_value=max_val,
                as_int=not is_float,
                on_commit=commit,
            )

            knob_refs[name] = {'slider': slider_ctrl, 'number': number_ctrl}

    def make_handler(camera_setter: Callable[[Any], Any], config_field: str, default_value: Any) -> Callable[[Any], None]:
        def handler(event: Any) -> None:
            if hasattr(event, 'args'):
                if isinstance(event.args, dict):
                    value = event.args.get('value', default_value)
                elif isinstance(event.args, (int, float, bool)):
                    value = event.args
                else:
                    value = default_value
            elif hasattr(event, 'value'):
                value = event.value
            else:
                value = event if isinstance(event, (int, float, bool)) else default_value

            camera_setter(value)

             # Delayed config saving            
            async def save_config_delayed() -> None:
                await asyncio.sleep(0.5)
                try:
                    config = get_global_config()
                    if config:
                        set_nested_config_value(config, config_field, value)

                        save_global_config()
                        logger.info(f'Saved {config_field}: {value}')
                        
                        if camera and hasattr(camera, 'save_uvc_config'):
                            camera.save_uvc_config()
                            
                except Exception as e:
                    logger.error(f'Error saving config for {config_field}: {e}')
                    ui.notify(f'Error saving {config_field}: {e}', type='warning',
                              position='bottom-right')

            if config_field in debounce_tasks:
                cancel_task_safely(debounce_tasks.get(config_field))

            debounce_tasks[config_field] = schedule_bg(save_config_delayed(), name=f'save_{config_field}')
        
        return handler
    
    def make_instant_handler(camera_setter: Callable[[Any], Any], config_field: str, default_value: Any) -> Callable[[Any], None]:
        def handler(event: Any) -> None:
            if hasattr(event, 'args'):
                if isinstance(event.args, dict):
                    value = event.args.get('value', default_value)
                elif isinstance(event.args, (int, float, bool)):
                    value = event.args
                else:
                    value = default_value
            elif hasattr(event, 'value'):
                value = event.value
            else:
                value = event if isinstance(event, (int, float, bool)) else default_value

            camera_setter(value)

            try:
                config = get_global_config()
                if config:
                    set_nested_config_value(config, config_field, value)

                    save_global_config()
                    
                    if camera and hasattr(camera, 'save_uvc_config'):
                        camera.save_uvc_config()
                        
                    logger.info(f'Saved {config_field}: {value}')
                    
            except Exception as e:
                logger.error(f'Error saving config for {config_field}: {e}')
                ui.notify(f'Error saving {config_field}: {e}', type='warning',
                          position='bottom-right')
        
        return handler

    ranges = camera.get_uvc_ranges() if camera else {}
    current = camera.get_uvc_current_values() if camera else {}

    def _build_default_control_updates() -> dict[str, Any]:
        if hasattr(camera, 'get_uvc_default_control_values'):
            try:
                updates = camera.get_uvc_default_control_values()
                if isinstance(updates, dict) and updates:
                    return dict(updates)
            except Exception as e:
                logger.debug(f'Could not read default control values directly: {e}')

        if hasattr(camera, 'get_uvc_defaults'):
            try:
                defaults = camera.get_uvc_defaults()
                if isinstance(defaults, dict) and defaults:
                    white_balance = defaults.get('white_balance', {}) or {}
                    exposure = defaults.get('exposure', {}) or {}
                    return {
                        'brightness': defaults.get('brightness', ranges.get('brightness', {}).get('default', 0)),
                        'contrast': defaults.get('contrast', ranges.get('contrast', {}).get('default', 16)),
                        'saturation': defaults.get('saturation', ranges.get('saturation', {}).get('default', 64)),
                        'sharpness': defaults.get('sharpness', ranges.get('sharpness', {}).get('default', 2)),
                        'gamma': defaults.get('gamma', ranges.get('gamma', {}).get('default', 164)),
                        'gain': defaults.get('gain', ranges.get('gain', {}).get('default', 10)),
                        'backlight_compensation': defaults.get(
                            'backlight_compensation',
                            ranges.get('backlight_compensation', {}).get('default', 42),
                        ),
                        'hue': defaults.get('hue', ranges.get('hue', {}).get('default', 0)),
                        'white_balance_auto': bool(white_balance.get('auto', True)),
                        'white_balance_manual': white_balance.get(
                            'value',
                            ranges.get('white_balance', {}).get('default', 4600),
                        ),
                        'exposure_auto': bool(exposure.get('auto', True)),
                        'exposure_manual': exposure.get(
                            'value',
                            ranges.get('exposure', {}).get('default', -6),
                        ),
                    }
            except Exception as e:
                logger.debug(f'Could not reconstruct default control values from defaults: {e}')

        if hasattr(camera, 'get_uvc_current_values'):
            try:
                current_values = camera.get_uvc_current_values() or {}
                updates: dict[str, Any] = {}
                passthrough = (
                    'brightness',
                    'contrast',
                    'saturation',
                    'hue',
                    'gain',
                    'sharpness',
                    'gamma',
                    'backlight_compensation',
                )
                for key in passthrough:
                    if key in current_values:
                        updates[key] = current_values[key]

                if 'auto_white_balance' in current_values:
                    updates['white_balance_auto'] = bool(current_values['auto_white_balance'])
                if 'white_balance' in current_values:
                    updates['white_balance_manual'] = current_values['white_balance']
                if 'auto_exposure' in current_values:
                    updates['exposure_auto'] = auto_exposure_value_is_auto(current_values['auto_exposure'])
                if 'exposure' in current_values:
                    updates['exposure_manual'] = current_values['exposure']

                if updates:
                    return updates
            except Exception as e:
                logger.debug(f'Could not rebuild control values from current UVC values: {e}')

        return {
            'brightness': ranges.get('brightness', {}).get('default', 0),
            'contrast': ranges.get('contrast', {}).get('default', 16),
            'saturation': ranges.get('saturation', {}).get('default', 64),
            'sharpness': ranges.get('sharpness', {}).get('default', 2),
            'gamma': ranges.get('gamma', {}).get('default', 164),
            'gain': ranges.get('gain', {}).get('default', 10),
            'backlight_compensation': ranges.get('backlight_compensation', {}).get('default', 42),
            'hue': ranges.get('hue', {}).get('default', 0),
            'white_balance_auto': True,
            'white_balance_manual': ranges.get('white_balance', {}).get('default', 4600),
            'exposure_auto': True,
            'exposure_manual': ranges.get('exposure', {}).get('default', -6),
        }

    def reset_uvc() -> bool:
        try:
            if camera.reset_uvc_to_defaults():
                for task in debounce_tasks.values():
                    cancel_task_safely(task)
                debounce_tasks.clear()

                try:
                    if hasattr(camera, 'save_uvc_config'):
                        save_result = camera.save_uvc_config()
                    else:
                        save_result = save_global_config()
                    save_ok = True if save_result is None else bool(save_result)
                except Exception as e:
                    logger.warning(f'Could not persist reset UVC defaults: {e}')
                    save_ok = False

                updates = _build_default_control_updates()
                
                for name, value in updates.items():
                    ctrl = knob_refs.get(name)
                    try:
                        if ctrl is None:
                            continue
                        if isinstance(ctrl, dict):
                            slider_ctrl = ctrl.get('slider')
                            number_ctrl = ctrl.get('number')
                            if slider_ctrl is not None:
                                _set_control_value(slider_ctrl, value)
                            if number_ctrl is not None:
                                _set_control_value(number_ctrl, value)
                        else:
                            _set_control_value(ctrl, value)
                    except Exception as e:
                        logger.warning(f'Failed to update control {name}: {e}')

                if save_ok:
                    ui.notify('UVC settings reset to defaults!', type='positive', position='bottom-right')
                else:
                    ui.notify('UVC settings were reset, but could not be saved.', type='warning', position='bottom-right')
                return True
        
        except Exception as e:
            logger.error(f'Error resetting UVC settings: {e}')
            ui.notify(f'Error resetting UVC settings: {e}', type='warning', position='bottom-right')
        return False
            
    # Main Layout
    with ui.card().classes('w-full flex-1').style('align-self:stretch; min-height:0;'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label('Camera Controls').classes('text-h6 font-semibold')
            ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')) \
                .props('flat round dense').tooltip('Open camera settings')

        with ui.column().classes('w-full gap-2'):
            # Image Quality Grid
            with ui.grid(columns=2).classes('w-full gap-2'):
                _build_scalar_card(
                    title='Brightness', name='brightness', range_key='brightness',
                    fallback={'min': -64, 'max': 64, 'default': 0}, step=1,
                    tooltip='Adjust brightness', setter=camera.set_brightness,
                    config_field='uvc_controls.brightness',
                )
                _build_scalar_card(
                    title='Contrast', name='contrast', range_key='contrast',
                    fallback={'min': 0, 'max': 64, 'default': 16}, step=1,
                    tooltip='Adjust contrast', setter=camera.set_contrast,
                    config_field='uvc_controls.contrast',
                )
                _build_scalar_card(
                    title='Saturation', name='saturation', range_key='saturation',
                    fallback={'min': 0, 'max': 128, 'default': 64}, step=1,
                    tooltip='Adjust saturation', setter=camera.set_saturation,
                    config_field='uvc_controls.saturation',
                )
                _build_scalar_card(
                    title='Sharpness', name='sharpness', range_key='sharpness',
                    fallback={'min': 0, 'max': 14, 'default': 2}, step=1,
                    tooltip='Adjust sharpness', setter=camera.set_sharpness,
                    config_field='uvc_controls.sharpness',
                )

            # Advanced Controls (Collapsible)
            with ui.expansion('Advanced Controls', icon='tune').classes('w-full border rounded-borders'):
                with ui.grid(columns=2).classes('w-full gap-2 p-2'):
                    _build_scalar_card(
                        title='Gamma', name='gamma', range_key='gamma',
                        fallback={'min': 72, 'max': 500, 'default': 164}, step=1,
                        tooltip='Adjust gamma', setter=camera.set_gamma,
                        config_field='uvc_controls.gamma',
                    )
                    _build_scalar_card(
                        title='Gain', name='gain', range_key='gain',
                        fallback={'min': 0, 'max': 100, 'default': 10}, step=1,
                        tooltip='Adjust gain', setter=camera.set_gain,
                        config_field='uvc_controls.gain',
                    )
                    _build_scalar_card(
                        title='Backlight', name='backlight_compensation', range_key='backlight_compensation',
                        fallback={'min': 0, 'max': 160, 'default': 42}, step=1,
                        tooltip='Backlight compensation', setter=camera.set_backlight_compensation,
                        config_field='uvc_controls.backlight_compensation',
                    )
                    _build_scalar_card(
                        title='Hue', name='hue', range_key='hue',
                        fallback={'min': -180, 'max': 180, 'default': 0}, step=1,
                        tooltip='Adjust hue', setter=camera.set_hue,
                        config_field='uvc_controls.hue',
                    )

            ui.separator().classes('my-1')

            # Exposure & White Balance
            with ui.grid(columns=2).classes('w-full gap-2'):
                # White Balance
                with ui.card().tight().classes('p-3 flex flex-col gap-2'):
                    ui.label('White Balance').classes('text-caption font-medium text-grey-8')
                    wb_auto_value = bool(current.get('auto_white_balance', 1))
                    wb_auto = ui.checkbox('Auto', value=wb_auto_value).props('dense')
                    
                    wb_manual_range = ranges.get('white_balance', {'min': 2800, 'max': 6500, 'default': 4600})
                    wb_manual_value = int(current.get('white_balance', wb_manual_range['default']))
                    
                    with ui.row().classes('items-center gap-2 w-full no-wrap'):
                        wb_slider = ui.slider(min=wb_manual_range['min'], max=wb_manual_range['max'], value=wb_manual_value, step=50).classes('flex-1').props('dense')
                        wb_number = ui.number(value=wb_manual_value, min=wb_manual_range['min'], max=wb_manual_range['max'], step=50).props('dense outlined hide-bottom-space').classes('w-[60px]').style('font-size: 12px;')

                    wb_number.bind_enabled_from(wb_auto, 'value', lambda x: not x)
                    wb_slider.bind_enabled_from(wb_auto, 'value', lambda x: not x)

                    if camera:
                        wb_auto.on('update:model-value', make_instant_handler(camera.set_auto_white_balance, 'uvc_controls.white_balance.auto', True))
                        wb_commit = make_handler(camera.set_manual_white_balance, 'uvc_controls.white_balance.value', wb_manual_range.get('default', 4600))
                        _bind_numeric_pair(wb_number, wb_slider, min_value=wb_manual_range['min'], max_value=wb_manual_range['max'], as_int=True, on_commit=wb_commit)
                        knob_refs['white_balance_auto'] = wb_auto
                        knob_refs['white_balance_manual'] = {'slider': wb_slider, 'number': wb_number}

                # Exposure
                with ui.card().tight().classes('p-3 flex flex-col gap-2'):
                    ui.label('Exposure').classes('text-caption font-medium text-grey-8')
                    exp_auto_value = auto_exposure_value_is_auto(current.get('auto_exposure', 1))
                    exp_auto = ui.checkbox('Auto', value=exp_auto_value).props('dense')

                    exp_manual_range = ranges.get('exposure', {'min': -13, 'max': -1, 'default': -6})
                    exp_manual_value = int(current.get('exposure', exp_manual_range['default']))

                    with ui.row().classes('items-center gap-2 w-full no-wrap'):
                        exp_slider = ui.slider(min=exp_manual_range['min'], max=exp_manual_range['max'], value=exp_manual_value, step=1).classes('flex-1').props('dense')
                        exp_number = ui.number(value=exp_manual_value, min=exp_manual_range['min'], max=exp_manual_range['max'], step=1).props('dense outlined hide-bottom-space').classes('w-[60px]').style('font-size: 12px;')

                    exp_number.bind_enabled_from(exp_auto, 'value', lambda x: not x)
                    exp_slider.bind_enabled_from(exp_auto, 'value', lambda x: not x)

                    if camera:
                        exp_auto.on('update:model-value', make_instant_handler(camera.set_auto_exposure, 'uvc_controls.exposure.auto', True))
                        exp_commit = make_handler(camera.set_manual_exposure, 'uvc_controls.exposure.value', exp_manual_range.get('default', -6))
                        _bind_numeric_pair(exp_number, exp_slider, min_value=exp_manual_range['min'], max_value=exp_manual_range['max'], as_int=True, on_commit=exp_commit)
                        knob_refs['exposure_auto'] = exp_auto
                        knob_refs['exposure_manual'] = {'slider': exp_slider, 'number': exp_number}

            # Actions
            def save_settings() -> None:
                try:
                    camera.save_uvc_config()
                    ui.notify('Saved', type='positive')
                except Exception as e:
                    logger.error(f"Failed to save UVC config: {e}")
                    ui.notify(f'Failed to save: {e}', type='negative')

            with ui.row().classes('w-full justify-end gap-2 mt-2'):
                ui.button(icon='restore', on_click=reset_uvc).props('flat round dense color=grey').tooltip('Reset to defaults')
                ui.button(icon='save', on_click=save_settings).props('flat round dense color=primary').tooltip('Save settings')
