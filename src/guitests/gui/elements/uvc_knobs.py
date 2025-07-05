from nicegui import ui


def create_uvc_content():
    with ui.card().style(
        "align-self:stretch; justify-content:center; align-items:start;"
    ):
        with ui.column().classes('gap-4'):

            # ── Gruppe: Bildqualität ────────────────────────────────────────
            ui.label('Image Quality')\
                .style("align-self:flex-start; display:block;")\
                .classes('text-h6 font-semibold mb-2')

            with ui.grid(columns=2, rows=2)\
                    .style("grid-template-rows:repeat(2, minmax(0, 1fr));"
                           "grid-template-columns:repeat(2, minmax(0, 1fr));"
                           "align-self:stretch;")\
                    .classes('gap-4 mb-4'):

                # Helligkeit
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Helligkeit').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=50, step=1, show_value=True)
                    ui.label('0–100').classes('text-sm text-gray-500')

                # Kontrast
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Kontrast').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=50, step=1, show_value=True)
                    ui.label('0–100').classes('text-sm text-gray-500')

                # Sättigung
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Sättigung').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=50, step=1, show_value=True)
                    ui.label('0–100').classes('text-sm text-gray-500')

                # Schärfe
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Schärfe').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=50, step=1, show_value=True)
                    ui.label('0–100').classes('text-sm text-gray-500')

            ui.separator()

            # ── Gruppe: Belichtung & Weißabgleich ───────────────────────────
            ui.label('White Balance & Exposure')\
                .classes('text-h6 font-semibold mb-2')

            # Zwei Cards nebeneinander (Weißabgleich | Belichtung)
            with ui.row().classes('gap-4 w-full'):

                # Weißabgleich ------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('White Balance').classes('font-semibold mb-2')
                    wb_auto = ui.checkbox('white balance auto', value=True)

                    with ui.row().classes('items-center gap-2'):
                        ui.label('manual white balance:')
                        wb_manual = ui.knob(
                            min=2800, max=6500, value=5000, step=10, show_value=True
                        )
                        # deaktivieren, solange Auto aktiv
                        wb_manual.bind_enabled_from(
                            wb_auto, 'value', lambda x: not x
                        )

                # Belichtung --------------------------------------------------
                with ui.card().tight()\
                        .style("align-self:stretch;")\
                        .classes('p-4 flex flex-col gap-2'):
                    ui.label('Exposure').classes('font-semibold mb-2')
                    exp_auto = ui.checkbox('exposure auto', value=True)

                    with ui.row().classes('items-center gap-2'):
                        ui.label('manual exposure:')
                        exp_manual = ui.knob(
                            min=1, max=1000, value=100, step=1, show_value=True
                        )
                        # deaktivieren, solange Auto aktiv
                        exp_manual.bind_enabled_from(
                            exp_auto, 'value', lambda x: not x
                        )

            ui.separator().style("align-self:stretch;")

            # ── Weitere Einstellungen ───────────────────────────────────────
            ui.label('Advanced Settings').classes('text-h6 font-semibold mb-2')

            with ui.grid(columns=3)\
                    .style("grid-template-columns:repeat(3, minmax(0, 1fr));"
                           "align-self:stretch;")\
                    .classes('gap-4 mb-4'):

                # Gamma
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Gamma').classes('font-semibold mb-2')
                    ui.knob(min=50, max=300, value=100, step=1, show_value=True)

                # Gain
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Gain').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=0, step=1, show_value=True)

                # Backlight Compensation
                with ui.card().tight().classes('p-4 flex flex-col items-center'):
                    ui.label('Backlight').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=0, step=1, show_value=True)

            # Reset Button
            with ui.row().classes('gap-4 w-full'):
                ui.button(icon='save', color='primary')\
                    .classes('flex-1 text-gray-500')\
                    .tooltip('save settings')
                ui.button(
                    icon='restore',
                    color='secondary'
                ).classes('flex-1 text-gray-500').tooltip('reset settings')
