from nicegui import ui
from nicegui.events import GenericEventArguments
from typing import Optional, Any, Callable, TypeVar

T = TypeVar('T')

def make_handler(setter: Callable[[T], Any], fallback: T) -> Callable[[Any], None]:
    """Versteht 3 Varianten: rohe Werte, GEA mit Dict, GEA mit rohem Wert."""
    def _cb(event: Any) -> None:
        if isinstance(event, GenericEventArguments):
            raw = event.args           # kann Dict _oder_ Skalar sein
            value = raw.get('value', fallback) if isinstance(raw, dict) else raw
        else:
            value = event              # int/float/bool direkt
        setter(value)
    return _cb

from src.cam.camera import Camera

def create_uvc_content(camera: Optional[Camera] = None):
    if camera is None:
        ui.label('⚠️ No Camera connected').classes('text-red')
        return

    ranges = camera.get_uvc_ranges() if camera else {}
    current = camera.get_uvc_current_values() if camera else {}

    with ui.card().style(
        "align-self:stretch; justify-content:center; align-items:start;"
    ):
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
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Brightness:').classes('font-semibold mb-2 self-start')
                    brightness_range = ranges.get('brightness', {'min': -64, 'max': 64, 'default': 0})
                    brightness_value = current.get('brightness', brightness_range['default'])
                    with ui.row().classes('justify-center'):
                        brightness_knob = ui.knob(min=brightness_range['min'], max=brightness_range['max'],
                                value=brightness_value, step=1, show_value=True).tooltip(
                                    'Adjust brightness level (default: 0)')
                    
                    brightness_knob.on('update:model-value', make_handler(camera.set_brightness, brightness_value))

                # Contrast
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Contrast:').classes('font-semibold mb-2 self-start')
                    contrast_range = ranges.get('contrast', {'min': 0, 'max': 64, 'default': 16})
                    contrast_value = current.get('contrast', contrast_range['default'])
                    with ui.row().classes('justify-center'):
                        contrast_knob = ui.knob(min=contrast_range['min'], max=contrast_range['max'],
                                value=contrast_value, step=1, show_value=True).tooltip(
                                    'Adjust contrast level (default: 16)')

                    contrast_knob.on('update:model-value', make_handler(camera.set_contrast, contrast_value))

                # Saturation
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Saturation:').classes('font-semibold mb-2 self-start')
                    saturation_range = ranges.get('saturation', {'min': 0, 'max': 128, 'default': 64})
                    saturation_value = current.get('saturation', saturation_range['default'])
                    with ui.row().classes('justify-center'):
                        saturation_knob = ui.knob(min=saturation_range['min'], max=saturation_range['max'],
                                value=saturation_value, step=1, show_value=True).tooltip(
                                    'Adjust saturation level (default: 64)')

                    saturation_knob.on('update:model-value', make_handler(camera.set_saturation, saturation_value))

                # Sharpness
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Sharpness:').classes('font-semibold mb-2 self-start')
                    sharpness_range = ranges.get('sharpness', {'min': 0, 'max': 14, 'default': 2})
                    sharpness_value = current.get('sharpness', sharpness_range['default'])
                    with ui.row().classes('justify-center'):
                        sharpness_knob = ui.knob(min=sharpness_range['min'], max=sharpness_range['max'],
                                value=sharpness_value, step=1, show_value=True).tooltip(
                                    'Adjust sharpness level (default: 2)')

                    sharpness_knob.on('update:model-value', make_handler(camera.set_sharpness, sharpness_value))

                # Gamma
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Gamma:').classes('font-semibold mb-2 self-start')
                    gamma_range = ranges.get('gamma', {'min': 72, 'max': 500, 'default': 164})
                    gamma_value = current.get('gamma', gamma_range['default'])
                    with ui.row().classes('justify-center'):
                        gamma_knob = ui.knob(min=gamma_range['min'], max=gamma_range['max'],
                                value=gamma_value, step=1, show_value=True).tooltip(
                                    'Adjust gamma level (default: 164)')

                    gamma_knob.on('update:model-value', make_handler(camera.set_gamma, gamma_value))

                # Gain
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Gain:').classes('font-semibold mb-2 self-start')
                    gain_range = ranges.get('gain', {'min': 0, 'max': 100, 'default': 10})
                    gain_value = current.get('gain', gain_range['default'])
                    with ui.row().classes('justify-center'):
                        gain_knob = ui.knob(min=gain_range['min'], max=gain_range['max'],
                                value=gain_value, step=1, show_value=True).tooltip(
                                    'Adjust gain level (default: 10)')

                    gain_knob.on('update:model-value', make_handler(camera.set_gain, gain_value))

                # Backlight Compensation
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Backlight Compensation:').classes('font-semibold mb-2 self-start')
                    backlight_range = ranges.get('backlight_compensation', {'min': 0, 'max': 160, 'default': 42})
                    backlight_value = current.get('backlight_compensation', backlight_range['default'])
                    with ui.row().classes('justify-center'):
                        backlight_knob = ui.knob(min=backlight_range['min'], max=backlight_range['max'],
                                value=backlight_value, step=1, show_value=True).tooltip(
                                    'Adjust backlight compensation level (default: 42)')

                    backlight_knob.on('update:model-value', make_handler(camera.set_backlight_compensation, backlight_value))

                # Hue
                with ui.card().tight().classes('p-4 flex flex-col').style("align-items:center;"):
                    ui.label('Hue:').classes('font-semibold mb-2 self-start')
                    hue_range = ranges.get('hue', {'min': -40, 'max': 40, 'default': 0})
                    hue_value = current.get('hue', hue_range['default'])
                    with ui.row().classes('justify-center'):
                        hue_knob = ui.knob(min=hue_range['min'], max=hue_range['max'],
                                value=hue_value, step=1, show_value=True).tooltip(
                                    'Adjust hue level (default: 0)')

                    hue_knob.on('update:model-value', make_handler(camera.set_hue, hue_value))

            ui.separator()

            # ── Gruppe: Belichtung & Weißabgleich ───────────────────────────
            ui.label('White Balance & Exposure')
                
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
                        wb_manual = ui.knob(
                            min=wb_manual_range['min'], max=wb_manual_range['max'], value=wb_manual_value, step=10, show_value=True
                        ).tooltip('Adjust manual white balance (default: 4600K)')
                        # deaktivieren, solange Auto aktiv
                        wb_manual.bind_enabled_from(
                            wb_auto, 'value', lambda x: not x
                        )
                    
                    if camera:
                        wb_auto.on('update:model-value', make_handler(camera.set_auto_white_balance, wb_auto_value))
                        wb_manual.on('update:model-value', make_handler(camera.set_manual_white_balance, wb_manual_value))
                # Belichtung --------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('Exposure').classes('font-semibold mb-2')
                    exp_auto_value = current.get('exposure_auto', 1) in (1, True)
                    exp_auto = ui.checkbox('exposure auto', value=exp_auto_value).tooltip('Enable automatic exposure adjustment')

                    exp_manual_range = ranges.get('exposure_manual', {'min': -13, 'max': -1, 'default': -6})
                    exp_manual_value = current.get('exposure_manual', exp_manual_range['default'])

                    with ui.row().classes('items-center gap-2'):
                        ui.label('manual exposure:')
                        exp_manual = ui.knob(
                            min=exp_manual_range['min'], max=exp_manual_range['max'], value=exp_manual_value, step=1, show_value=True
                        ).tooltip('Adjust manual exposure (default: -6)')
                        # deaktivieren, solange Auto aktiv
                        exp_manual.bind_enabled_from(
                            exp_auto, 'value', lambda x: not x
                        )
                    
                    if camera:
                        exp_auto.on('update:model-value', make_handler(camera.set_auto_exposure, exp_auto_value))
                        exp_manual.on('update:model-value', make_handler(camera.set_manual_exposure, exp_manual_value))

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
                save_btn.on('click', lambda: camera.save_uvc_config())
                reset_btn.on('click', lambda: camera.reset_uvc_to_defaults())
