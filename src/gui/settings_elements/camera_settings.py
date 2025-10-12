from nicegui import ui, background_tasks
from typing import Optional, Any, Callable
import asyncio

from src.cam.camera import Camera
from src.gui.util import schedule_bg, cancel_task_safely
from src.config import get_logger, get_global_config, save_global_config

logger = get_logger('gui.uvc_sliders')

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

        with ui.card().tight().classes('p-4 flex flex-col').style('align-items:stretch;'):
            ui.label(f'{title}:').classes('font-semibold mb-2 self-start')
            with ui.row().classes('items-center gap-3 w-full'):
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
                    .classes('min-w-[120px]')
                )
                slider_ctrl = (
                    ui.slider(min=min_val, max=max_val, step=step, value=value)
                    .tooltip(tooltip)
                    .classes('flex-1')
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

    def _to_bool_auto_exposure(val: Any) -> bool:
        """Interpret OpenCV auto-exposure flag across platforms.
        Windows typically reports 0.75 (auto) / 0.25 (manual);
        Linux often 3 (auto) / 1 (manual); sometimes 0/1.
        """
        try:
            f = float(val)
        except Exception:
            return bool(val)
        # Treat > 0.5 as auto, and explicit 3.0 as auto
        return f > 0.5 or abs(f - 3.0) < 1e-6
    
    def make_handler(camera_setter, config_field: str, default_value):
        def handler(event):
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
            async def save_config_delayed():
                await asyncio.sleep(0.5)  # 500ms Verzögerung
                try:
                    config = get_global_config()
                    if config:
                        # Support nested dataclasses for exposure/white_balance
                        # e.g. uvc_controls.white_balance.value / .auto
                        obj = config
                        fields = config_field.split('.')
                        for field in fields[:-1]:
                            obj = getattr(obj, field)
                        setattr(obj, fields[-1], value)

                        save_global_config()
                        logger.info(f'Saved {config_field}: {value}')
                        
                        # Camera config auch speichern
                        if camera and hasattr(camera, 'save_uvc_config'):
                            camera.save_uvc_config()
                            
                except Exception as e:
                    logger.error(f'Error saving config for {config_field}: {e}')
                    ui.notify(f'Error saving {config_field}: {e}', type='warning',
                              position='bottom-right')

            # Vorherige Task für dieses Feld abbrechen
            if config_field in debounce_tasks:
                cancel_task_safely(debounce_tasks.get(config_field))

            # Start a safe background task (deferred if loop not ready)
            debounce_tasks[config_field] = schedule_bg(save_config_delayed(), name=f'save_{config_field}')
        
        return handler
    
    def make_instant_handler(camera_setter, config_field: str, default_value):
        """Für Checkboxes - sofortige Speicherung ohne Debounce"""
        def handler(event):
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
                    obj = config
                    fields = config_field.split('.')
                    for field in fields[:-1]:
                        obj = getattr(obj, field)
                    setattr(obj, fields[-1], value)

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

    def reset_uvc():
        try:
            if camera.reset_uvc_to_defaults():
                for task in debounce_tasks.values():
                    if not task.done():
                        task.cancel()
                debounce_tasks.clear()
                # Neue Werte von der Kamera abrufen
                updated_current = camera.get_uvc_current_values()
                updated_ranges = camera.get_uvc_ranges()
                
                # Alle Regler aktualisieren
                updates = {
                    'brightness': int(updated_current.get('brightness', updated_ranges.get('brightness', {}).get('default', 0))),
                    'contrast': int(updated_current.get('contrast', updated_ranges.get('contrast', {}).get('default', 16))),
                    'saturation': int(updated_current.get('saturation', updated_ranges.get('saturation', {}).get('default', 64))),
                    'sharpness': int(updated_current.get('sharpness', updated_ranges.get('sharpness', {}).get('default', 2))),
                    'gamma': int(updated_current.get('gamma', updated_ranges.get('gamma', {}).get('default', 164))),
                    'gain': int(updated_current.get('gain', updated_ranges.get('gain', {}).get('default', 10))),
                    'backlight_compensation': int(updated_current.get('backlight_compensation', updated_ranges.get('backlight_compensation', {}).get('default', 42))),
                    'hue': int(updated_current.get('hue', updated_ranges.get('hue', {}).get('default', 0))),
                    # Use correct keys from Camera.get_uvc_current_values
                    'white_balance_auto': bool(updated_current.get('auto_white_balance', 1)),
                    'white_balance_manual': int(updated_current.get('white_balance', updated_ranges.get('white_balance', {}).get('default', 4600))),
                    'exposure_auto': _to_bool_auto_exposure(updated_current.get('auto_exposure', 1)),
                    'exposure_manual': int(updated_current.get('exposure', updated_ranges.get('exposure', {}).get('default', -6)))
                }
                
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

                ui.notify('UVC settings reset to defaults!', type='positive', position='bottom-right')        
                return True
        
        except Exception as e:
            logger.error(f'Error resetting UVC settings: {e}')
            ui.notify(f'Error resetting UVC settings: {e}', type='warning', position='bottom-right')
        return False
            
    # Render directly without wrapping in an extra Card; parent page will provide cards
    with ui.column().style("align-self:stretch; flex-direction:column; flex-wrap:wrap; justify-content:end; align-items:start; display:flex;").classes('gap-4'):

        # ── Gruppe: Bildqualität ────────────────────────────────────────
        ui.label('Image Quality')\
            .style("align-self:flex-start; display:block;")\
            .classes('text-h6 font-semibold mb-2')

        with ui.grid(columns=2, rows=2)\
                .style("grid-template-rows:repeat(2, minmax(0, 1fr));"
                       "grid-template-columns:repeat(2, minmax(0, 1fr));"
                       "align-self:stretch;")\
                .classes('gap-4 mb-4'):

                _build_scalar_card(
                    title='Brightness',
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
        with ui.grid(columns=2).classes('gap-4 w-full'):

                # Weißabgleich ------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('White Balance').classes('font-semibold mb-2')
                    wb_auto_value = bool(current.get('auto_white_balance', 1))
                    wb_auto = ui.checkbox('Auto white balance', value=wb_auto_value).tooltip('Enable automatic white balance adjustment')

                    wb_manual_range = ranges.get('white_balance', {'min': 2800, 'max': 6500, 'default': 4600})
                    wb_manual_value = int(current.get('white_balance', wb_manual_range['default']))
                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.label('Manual:').classes('text-sm text-grey-7')
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
                            .classes('min-w-[120px]')
                        )
                        wb_manual_slider = (
                            ui.slider(
                                min=wb_manual_range['min'],
                                max=wb_manual_range['max'],
                                value=wb_manual_value,
                                step=50,
                            )
                            .tooltip('Adjust manual white balance (default: 4600K)')
                            .classes('flex-1')
                        )

                    wb_manual_number.bind_enabled_from(wb_auto, 'value', lambda x: not x)
                    wb_manual_slider.bind_enabled_from(wb_auto, 'value', lambda x: not x)

                    if camera:
                        wb_auto.on('update:model-value', make_instant_handler(camera.set_auto_white_balance, 'uvc_controls.white_balance.auto', True))
                        wb_commit = make_handler(camera.set_manual_white_balance, 'uvc_controls.white_balance.value', wb_manual_range.get('default', 4600))
                        _bind_numeric_pair(
                            wb_manual_number,
                            wb_manual_slider,
                            min_value=wb_manual_range['min'],
                            max_value=wb_manual_range['max'],
                            as_int=True,
                            on_commit=wb_commit,
                        )
                        knob_refs['white_balance_auto'] = wb_auto
                        knob_refs['white_balance_manual'] = {'slider': wb_manual_slider, 'number': wb_manual_number}
                # Belichtung --------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('Exposure').classes('font-semibold mb-2')
                    exp_auto_value = _to_bool_auto_exposure(current.get('auto_exposure', 1))
                    exp_auto = ui.checkbox('Auto exposure', value=exp_auto_value).tooltip('Enable automatic exposure adjustment')

                    exp_manual_range = ranges.get('exposure', {'min': -13, 'max': -1, 'default': -6})
                    exp_manual_value = int(current.get('exposure', exp_manual_range['default']))

                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.label('Manual:').classes('text-sm text-grey-7')
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
                            .classes('min-w-[120px]')
                        )
                        exp_manual_slider = (
                            ui.slider(
                                min=exp_manual_range['min'],
                                max=exp_manual_range['max'],
                                value=exp_manual_value,
                                step=1,
                            )
                            .tooltip('Adjust manual exposure (default: -6)')
                            .classes('flex-1')
                        )

                    exp_manual_number.bind_enabled_from(exp_auto, 'value', lambda x: not x)
                    exp_manual_slider.bind_enabled_from(exp_auto, 'value', lambda x: not x)

                    if camera:
                        exp_auto.on('update:model-value', make_instant_handler(camera.set_auto_exposure, 'uvc_controls.exposure.auto', True))
                        exp_commit = make_handler(camera.set_manual_exposure, 'uvc_controls.exposure.value', exp_manual_range.get('default', -6))
                        _bind_numeric_pair(
                            exp_manual_number,
                            exp_manual_slider,
                            min_value=exp_manual_range['min'],
                            max_value=exp_manual_range['max'],
                            as_int=True,
                            on_commit=exp_commit,
                        )
                        knob_refs['exposure_auto'] = exp_auto
                        knob_refs['exposure_manual'] = {'slider': exp_manual_slider, 'number': exp_manual_number}

        ui.separator().style("align-self:stretch;")

    # Reset Button
    with ui.row().classes('gap-4'):
            save_btn = ui.button(icon='save', color='primary')\
                .classes('flex-1 text-gray-500').props('round')\
                .tooltip('save settings')
            reset_btn = ui.button(
                icon='restore',
                color='secondary'
            ).classes('flex-1 text-gray-500').props('round').tooltip('reset settings')

            if camera:
                save_btn.on('click', lambda: (camera.save_uvc_config(), ui.notify('Settings saved successfully!', type='positive', position='bottom-right')))
                reset_btn.on('click', lambda: reset_uvc())