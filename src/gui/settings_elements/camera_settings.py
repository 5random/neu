from nicegui import ui, background_tasks
from nicegui.events import GenericEventArguments
from typing import Optional, Any, Callable, TypeVar
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

    knob_refs = {}
    debounce_tasks = {}
    
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
                    'white_balance_auto': bool(updated_current.get('white_balance_auto', 1)),
                    'white_balance_manual': int(updated_current.get('white_balance_manual', updated_ranges.get('white_balance_manual', {}).get('default', 4600))),
                    'exposure_auto': bool(updated_current.get('exposure_auto', 1)),
                    'exposure_manual': int(updated_current.get('exposure_manual', updated_ranges.get('exposure_manual', {}).get('default', -6)))
                }
                
                # UI-Controls aktualisieren (Sliders / Checkboxes)
                for name, value in updates.items():
                    ctrl = knob_refs.get(name)
                    try:
                        if ctrl is None:
                            continue
                        if hasattr(ctrl, 'set_value'):
                            ctrl.set_value(value)
                        else:
                            setattr(ctrl, 'value', value)
                            if hasattr(ctrl, 'update'):
                                ctrl.update()
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

                # Brightness
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Brightness:').classes('font-semibold mb-2 self-start')
                    brightness_range = ranges.get('brightness', {'min': -64, 'max': 64, 'default': 0})
                    brightness_value = int(current.get('brightness', brightness_range['default']))
                    val_lbl = ui.label(str(brightness_value)).classes('self-end text-caption')
                    brightness_slider = ui.slider(min=brightness_range['min'], max=brightness_range['max'], step=1, value=brightness_value)
                    knob_refs['brightness'] = brightness_slider
                    def _on_brightness(e):
                        v = int(getattr(e, 'value', brightness_slider.value))
                        val_lbl.text = str(v)
                        make_handler(camera.set_brightness, 'uvc_controls.brightness', 0)(v)
                    brightness_slider.on('change', _on_brightness)

                # Contrast
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Contrast:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('contrast', {'min': 0, 'max': 95, 'default': 16})
                    v0 = int(current.get('contrast', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['contrast'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_contrast, 'uvc_controls.contrast', 16)(int(getattr(e, 'value', sld.value)))))

                # Saturation
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Saturation:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('saturation', {'min': 0, 'max': 255, 'default': 64})
                    v0 = int(current.get('saturation', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['saturation'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_saturation, 'uvc_controls.saturation', 64)(int(getattr(e, 'value', sld.value)))))


                # Sharpness
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Sharpness:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('sharpness', {'min': 0, 'max': 14, 'default': 2})
                    v0 = int(current.get('sharpness', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['sharpness'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_sharpness, 'uvc_controls.sharpness', 2)(int(getattr(e, 'value', sld.value)))))

                # Gamma
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Gamma:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('gamma', {'min': 72, 'max': 500, 'default': 164})
                    v0 = int(current.get('gamma', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['gamma'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_gamma, 'uvc_controls.gamma', 164)(int(getattr(e, 'value', sld.value)))))

                # Gain
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Gain:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('gain', {'min': 0, 'max': 127, 'default': 10})
                    v0 = int(current.get('gain', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['gain'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_gain, 'uvc_controls.gain', 10)(int(getattr(e, 'value', sld.value)))))

                # Backlight Compensation
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Backlight Compensation:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('backlight_compensation', {'min': 0, 'max': 255, 'default': 42})
                    v0 = int(current.get('backlight_compensation', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['backlight_compensation'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_backlight_compensation, 'uvc_controls.backlight_compensation', 42)(int(getattr(e, 'value', sld.value)))))

                # Hue
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:stretch;"):
                    ui.label('Hue:').classes('font-semibold mb-2 self-start')
                    r = ranges.get('hue', {'min': -180, 'max': 180, 'default': 0})
                    v0 = int(current.get('hue', r['default']))
                    lbl = ui.label(str(v0)).classes('self-end text-caption')
                    sld = ui.slider(min=r['min'], max=r['max'], step=1, value=v0)
                    knob_refs['hue'] = sld
                    sld.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', sld.value)))), make_handler(camera.set_hue, 'uvc_controls.hue', 0)(int(getattr(e, 'value', sld.value)))))

            ui.separator()

            # ── Gruppe: Belichtung & Weißabgleich ───────────────────────────
                
            # Zwei Cards nebeneinander (Weißabgleich | Belichtung)
            with ui.grid(columns=2).classes('gap-4 w-full'):

                # Weißabgleich ------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('White Balance').classes('font-semibold mb-2')
                    wb_auto_value = current.get('white_balance_auto', 1) == 1
                    wb_auto = ui.checkbox('white balance auto', value=wb_auto_value).tooltip('Enable automatic white balance adjustment')
                    
                    wb_manual_range = ranges.get('white_balance_manual', {'min': 2800, 'max': 6500, 'default': 4600})
                    wb_manual_value = current.get('white_balance_manual', wb_manual_range['default'])
                    with ui.row().classes('items-center gap-2'):
                        ui.label('manual white balance:')
                        lbl = ui.label(str(int(wb_manual_value))).classes('text-caption')
                        wb_manual = ui.slider(
                            min=wb_manual_range['min'], max=wb_manual_range['max'], value=int(wb_manual_value), step=50
                        ).tooltip('Adjust manual white balance (default: 4600K)')
                        # deaktivieren, solange Auto aktiv
                        wb_manual.bind_enabled_from(
                            wb_auto, 'value', lambda x: not x
                        )
                    
                    if camera:
                        wb_auto.on('update:model-value', make_instant_handler(camera.set_auto_white_balance, 'uvc_controls.white_balance_auto', True))
                        wb_manual.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', wb_manual.value)))), make_handler(camera.set_manual_white_balance, 'uvc_controls.white_balance_manual', 4600)(int(getattr(e, 'value', wb_manual.value)))))
                        knob_refs['white_balance_auto'] = wb_auto
                        knob_refs['white_balance_manual'] = wb_manual
                # Belichtung --------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('Exposure').classes('font-semibold mb-2')
                    exp_auto_value = current.get('exposure_auto', 1) == 1
                    exp_auto = ui.checkbox('exposure auto', value=exp_auto_value).tooltip('Enable automatic exposure adjustment')

                    exp_manual_range = ranges.get('exposure_manual', {'min': -13, 'max': -1, 'default': -6})
                    exp_manual_value = current.get('exposure_manual', exp_manual_range['default'])

                    with ui.row().classes('items-center gap-2'):
                        ui.label('manual exposure:')
                        lbl = ui.label(str(int(exp_manual_value))).classes('text-caption')
                        exp_manual = ui.slider(
                            min=exp_manual_range['min'], max=exp_manual_range['max'], value=int(exp_manual_value), step=1
                        ).tooltip('Adjust manual exposure (default: -6)')
                        # deaktivieren, solange Auto aktiv
                        exp_manual.bind_enabled_from(
                            exp_auto, 'value', lambda x: not x
                        )
                    
                    if camera:
                        exp_auto.on('update:model-value', make_instant_handler(camera.set_auto_exposure, 'uvc_controls.exposure_auto', True))
                        exp_manual.on('change', lambda e: (lbl.__setattr__('text', str(int(getattr(e, 'value', exp_manual.value)))), make_handler(camera.set_manual_exposure, 'uvc_controls.exposure_manual', -6)(int(getattr(e, 'value', exp_manual.value)))))
                        knob_refs['exposure_auto'] = exp_auto
                        knob_refs['exposure_manual'] = exp_manual

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
                reset_btn.on('click', lambda: (reset_uvc(), ui.notify('Settings reset to defaults!', type='positive', position='bottom-right')))
