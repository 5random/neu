from nicegui import ui

def create_uvc_content():
    with ui.card().classes('w-full max-w-2xl'):
        with ui.grid(rows=3, columns=3).classes('gap-4 justify-items-center'):

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 1').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 2').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 3').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 4').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 5').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 6').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 7').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 8').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')

            with ui.card().classes('w-full h-full flex flex-col items-center justify-between p-4'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label('Knob 9').classes('font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                )
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')
