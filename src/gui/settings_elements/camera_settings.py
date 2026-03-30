from nicegui import ui, background_tasks
from typing import Optional, Any, Callable
import asyncio

from src.cam.camera import Camera
from src.gui.util import schedule_bg, cancel_task_safely
from src.gui.uvc_helpers import auto_exposure_value_is_auto, set_nested_config_value
from src.config import get_logger, get_global_config, save_global_config
from src.gui.bindings import bind_number_slider
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row

logger = get_logger('gui.uvc_sliders')


def _handle_config_save_error(config_field: str, error: Exception) -> None:
    """Log config-save failures with a more specific message for invalid config paths."""
    if isinstance(error, AttributeError):
        logger.error(f'Invalid config path for {config_field}: {error}')
        ui.notify(
            f'Invalid config path for {config_field}: {error}',
            type='warning',
            position='bottom-right',
        )
        return

    logger.error(f'Error saving config for {config_field}: {error}')
    ui.notify(
        f'Error saving {config_field}: {error}',
        type='warning',
        position='bottom-right',
    )


def create_uvc_content(camera: Optional[Camera] = None) -> None:
    if camera is None:
        logger.warning("Camera not available - UVC controls disabled")
        ui.label('⚠️ No Camera connected').classes('text-red')
        return
    
    logger.info("Creating UVC Card")

    knob_refs: dict[str, Any] = {}
    debounce_tasks: dict[str, Any] = {}

    ranges = camera.get_uvc_ranges() if camera else {}
    current = camera.get_uvc_current_values() if camera else {}
    compact_value_classes = 'w-[5.5rem] min-w-[5.5rem] max-w-[5.5rem] shrink-0'
    unit_value_classes = 'w-[7rem] min-w-[7rem] max-w-[7rem] shrink-0'
    slider_classes = 'flex-1 min-w-[10rem] max-w-full'
    compact_slider_classes = 'flex-1 min-w-0 max-w-full'

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

    def make_handler(camera_setter: Callable, config_field: str, default_value: Any) -> Callable:
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
                await asyncio.sleep(0.5)  # 500ms Verzögerung
                try:
                    config = get_global_config()
                    if config:
                        # Support nested dataclasses for exposure/white_balance
                        # e.g. uvc_controls.white_balance.value / .auto
                        set_nested_config_value(config, config_field, value)

                        save_global_config()
                        logger.info(f'Saved {config_field}: {value}')
                        
                        # Camera config auch speichern
                        if camera and hasattr(camera, 'save_uvc_config'):
                            camera.save_uvc_config()
                            
                except Exception as e:
                    _handle_config_save_error(config_field, e)

            # Vorherige Task für dieses Feld abbrechen
            if config_field in debounce_tasks:
                cancel_task_safely(debounce_tasks.get(config_field))

            # Start a safe background task (deferred if loop not ready)
            debounce_tasks[config_field] = schedule_bg(save_config_delayed(), name=f'save_{config_field}')
        
        return handler

    def _build_scalar_card(
        *,
        title: str,
        icon: str,
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

        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            create_heading_row(
                title,
                icon=icon,
                title_classes='font-semibold mb-1',
                row_classes='items-center gap-2 self-start',
                icon_classes='text-primary text-lg shrink-0',
            )
            with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                number_ctrl = (
                    ui.number(
                        value=value,
                        min=min_val,
                        max=max_val,
                        step=step,
                        format='%.1f' if is_float else '%.0f',
                    )
                    .props('dense outlined')
                    .tooltip(tooltip)
                    .classes(compact_value_classes)
                )
                slider_ctrl = (
                    ui.slider(min=min_val, max=max_val, step=step, value=value)
                    .tooltip(tooltip)
                    .classes(slider_classes)
                )

            commit = make_handler(setter, config_field, default_val)
            bind_number_slider(
                number_ctrl,
                slider_ctrl,
                min_value=min_val,
                max_value=max_val,
                as_int=not is_float,
                on_change=commit,
            )

            knob_refs[name] = {'slider': slider_ctrl, 'number': number_ctrl}

    def make_instant_handler(camera_setter: Callable, config_field: str, default_value: Any) -> Callable:
        """Für Checkboxes - sofortige Speicherung ohne Debounce"""
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

            # Sofort speichern (für Checkboxes)
            try:
                config = get_global_config()
                if config:
                    set_nested_config_value(config, config_field, value)

                    save_global_config()
                    
                    if camera and hasattr(camera, 'save_uvc_config'):
                        camera.save_uvc_config()
                        
                    logger.info(f'Saved {config_field}: {value}')
                    
            except Exception as e:
                _handle_config_save_error(config_field, e)
        
        return handler

    def reset_uvc() -> bool:
        try:
            if camera.reset_uvc_to_defaults():
                for task in debounce_tasks.values():
                    cancel_task_safely(task)
                debounce_tasks.clear()
                save_ok = camera.save_uvc_config() if hasattr(camera, 'save_uvc_config') else save_global_config()
                updates = (
                    camera.get_uvc_default_control_values()
                    if hasattr(camera, 'get_uvc_default_control_values')
                    else {}
                )
                
                # UI-Controls aktualisieren (Sliders / Checkboxes)
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
            
    # Render directly without wrapping in an extra Card; parent page will provide cards
    with ui.column().classes('w-full gap-4'):
        # ── Gruppe: Bildqualität ────────────────────────────────────────
        create_heading_row(
            'Image Quality',
            icon='tune',
            title_classes='text-h6 font-semibold mb-2',
            row_classes='items-center gap-2 self-start',
            icon_classes='text-primary text-xl shrink-0',
        )

        with ui.grid(columns=2)\
                .style("grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));"
                       "grid-auto-rows:min-content;"
                       "align-self:stretch;")\
                .classes('gap-3 mb-4 items-start'):

                _build_scalar_card(
                    title='Brightness',
                    icon='brightness_6',
                    name='brightness',
                    range_key='brightness',
                    fallback={'min': -64, 'max': 64, 'default': 0},
                    step=1,
                    tooltip='Adjust overall image brightness',
                    setter=camera.set_brightness,
                    config_field='uvc_controls.brightness',
                )
                _build_scalar_card(
                    title='Contrast',
                    icon='contrast',
                    name='contrast',
                    range_key='contrast',
                    fallback={'min': 0, 'max': 64, 'default': 16},
                    step=1,
                    tooltip='Adjust contrast ratio',
                    setter=camera.set_contrast,
                    config_field='uvc_controls.contrast',
                )
                _build_scalar_card(
                    title='Saturation',
                    icon='palette',
                    name='saturation',
                    range_key='saturation',
                    fallback={'min': 0, 'max': 128, 'default': 64},
                    step=1,
                    tooltip='Adjust color saturation',
                    setter=camera.set_saturation,
                    config_field='uvc_controls.saturation',
                )
                _build_scalar_card(
                    title='Sharpness',
                    icon='center_focus_strong',
                    name='sharpness',
                    range_key='sharpness',
                    fallback={'min': 0, 'max': 14, 'default': 2},
                    step=1,
                    tooltip='Adjust image sharpness',
                    setter=camera.set_sharpness,
                    config_field='uvc_controls.sharpness',
                )
                _build_scalar_card(
                    title='Gamma',
                    icon='functions',
                    name='gamma',
                    range_key='gamma',
                    fallback={'min': 72, 'max': 500, 'default': 164},
                    step=1,
                    tooltip='Adjust gamma correction',
                    setter=camera.set_gamma,
                    config_field='uvc_controls.gamma',
                )
                _build_scalar_card(
                    title='Gain',
                    icon='equalizer',
                    name='gain',
                    range_key='gain',
                    fallback={'min': 0, 'max': 100, 'default': 10},
                    step=1,
                    tooltip='Adjust camera gain',
                    setter=camera.set_gain,
                    config_field='uvc_controls.gain',
                )
                _build_scalar_card(
                    title='Backlight Compensation',
                    icon='wb_incandescent',
                    name='backlight_compensation',
                    range_key='backlight_compensation',
                    fallback={'min': 0, 'max': 160, 'default': 42},
                    step=1,
                    tooltip='Compensate for strong backlight',
                    setter=camera.set_backlight_compensation,
                    config_field='uvc_controls.backlight_compensation',
                )
                _build_scalar_card(
                    title='Hue',
                    icon='color_lens',
                    name='hue',
                    range_key='hue',
                    fallback={'min': -180, 'max': 180, 'default': 0},
                    step=1,
                    tooltip='Adjust image hue',
                    setter=camera.set_hue,
                    config_field='uvc_controls.hue',
                )

        ui.separator()

        # ── Gruppe: Belichtung & Weißabgleich ───────────────────────────
                
        # Zwei Cards nebeneinander (Weißabgleich | Belichtung)
        with ui.grid(columns=2).classes('gap-3 w-full items-start').style('grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'):

                # Weißabgleich ------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:start;")\
                        .classes('p-3 flex flex-col gap-2 w-full'):
                    create_heading_row(
                        'White Balance',
                        icon='wb_sunny',
                        title_classes='font-semibold mb-1',
                        row_classes='items-center gap-2',
                        icon_classes='text-primary text-lg shrink-0',
                    )
                    wb_auto_value = bool(current.get('auto_white_balance', 1))
                    wb_auto = ui.checkbox('Auto white balance', value=wb_auto_value).tooltip('Enable automatic white balance adjustment')

                    wb_manual_range = ranges.get('white_balance', {'min': 2800, 'max': 6500, 'default': 4600})
                    wb_manual_value = int(current.get('white_balance', wb_manual_range['default']))
                    with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                        ui.label('Manual:').classes('text-sm text-grey-7 shrink-0')
                        wb_manual_number = (
                            ui.number(
                                value=wb_manual_value,
                                min=wb_manual_range['min'],
                                max=wb_manual_range['max'],
                                step=50,
                                format='%.0f',
                            )
                            .props('dense outlined suffix="K"')
                            .tooltip('Adjust manual white balance (default: 4600K)')
                            .classes(unit_value_classes)
                        )
                        wb_manual_slider = (
                            ui.slider(
                                min=wb_manual_range['min'],
                                max=wb_manual_range['max'],
                                value=wb_manual_value,
                                step=50,
                            )
                            .tooltip('Adjust manual white balance (default: 4600K)')
                            .classes(compact_slider_classes)
                        )

                    wb_manual_number.bind_enabled_from(wb_auto, 'value', lambda x: not x)
                    wb_manual_slider.bind_enabled_from(wb_auto, 'value', lambda x: not x)

                    if camera:
                        wb_auto.on('update:model-value', make_instant_handler(camera.set_auto_white_balance, 'uvc_controls.white_balance.auto', True))
                        wb_commit = make_handler(camera.set_manual_white_balance, 'uvc_controls.white_balance.value', wb_manual_range.get('default', 4600))
                        bind_number_slider(
                            wb_manual_number,
                            wb_manual_slider,
                            min_value=wb_manual_range['min'],
                            max_value=wb_manual_range['max'],
                            as_int=True,
                            on_change=wb_commit,
                        )
                        knob_refs['white_balance_auto'] = wb_auto
                        knob_refs['white_balance_manual'] = {'slider': wb_manual_slider, 'number': wb_manual_number}
                # Belichtung --------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:start;")\
                        .classes('p-3 flex flex-col gap-2 w-full'):
                    create_heading_row(
                        'Exposure',
                        icon='exposure',
                        title_classes='font-semibold mb-1',
                        row_classes='items-center gap-2',
                        icon_classes='text-primary text-lg shrink-0',
                    )
                    exp_auto_value = auto_exposure_value_is_auto(current.get('auto_exposure', 1))
                    exp_auto = ui.checkbox('Auto exposure', value=exp_auto_value).tooltip('Enable automatic exposure adjustment')

                    exp_manual_range = ranges.get('exposure', {'min': -13, 'max': -1, 'default': -6})
                    exp_manual_value = int(current.get('exposure', exp_manual_range['default']))

                    with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                        ui.label('Manual:').classes('text-sm text-grey-7 shrink-0')
                        exp_manual_number = (
                            ui.number(
                                value=exp_manual_value,
                                min=exp_manual_range['min'],
                                max=exp_manual_range['max'],
                                step=1,
                                format='%.0f',
                            )
                            .props('dense outlined suffix="EV"')
                            .tooltip('Adjust manual exposure (default: -6)')
                            .classes(unit_value_classes)
                        )
                        exp_manual_slider = (
                            ui.slider(
                                min=exp_manual_range['min'],
                                max=exp_manual_range['max'],
                                value=exp_manual_value,
                                step=1,
                            )
                            .tooltip('Adjust manual exposure (default: -6)')
                            .classes(compact_slider_classes)
                        )

                    exp_manual_number.bind_enabled_from(exp_auto, 'value', lambda x: not x)
                    exp_manual_slider.bind_enabled_from(exp_auto, 'value', lambda x: not x)

                    if camera:
                        exp_auto.on('update:model-value', make_instant_handler(camera.set_auto_exposure, 'uvc_controls.exposure.auto', True))
                        exp_commit = make_handler(camera.set_manual_exposure, 'uvc_controls.exposure.value', exp_manual_range.get('default', -6))
                        bind_number_slider(
                            exp_manual_number,
                            exp_manual_slider,
                            min_value=exp_manual_range['min'],
                            max_value=exp_manual_range['max'],
                            as_int=True,
                            on_change=exp_commit,
                        )
                        knob_refs['exposure_auto'] = exp_auto
                        knob_refs['exposure_manual'] = {'slider': exp_manual_slider, 'number': exp_manual_number}

        ui.separator().style("align-self:stretch;")

    # Reset Button
    with ui.row().classes('gap-4'):
        save_btn = create_action_button(
            'save',
            label='Save',
            classes='flex-1 font-medium',
            tooltip='Save settings',
        )
        reset_btn = create_action_button(
            'reset',
            label='Reset',
            classes='flex-1 font-medium',
            tooltip='Reset settings',
        )

        if camera:
            def save_settings() -> None:
                try:
                    camera.save_uvc_config()
                    ui.notify('Settings saved successfully!', type='positive', position='bottom-right')
                except Exception as e:
                    logger.error(f"Failed to save UVC config: {e}")
                    ui.notify(f'Failed to save: {e}', type='negative', position='bottom-right')

            save_btn.on('click', save_settings)
            reset_btn.on('click', lambda: reset_uvc())
