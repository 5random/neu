from nicegui import ui

with ui.grid(rows=3, columns=3).classes('gap-4 justify-items-center'):

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 1').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 2').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 3').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 4').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 5').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 6').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 7').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 8').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

    with ui.card().tight().classes('flex flex-col items-center'):
        # ⬆️ Deutlich hervorgehobene Überschrift
        ui.label('Knob 9').classes('text-lg font-semibold text-center mb-2')
        
        # 🎛️ Der eigentliche Regler
        ui.knob(
            min=0,
            max=100,
            value=50,
            show_value=True,
        )
        
        # Optionale Zusatzinfo
        ui.label('0–100').classes('text-sm text-gray-500')

ui.run()
