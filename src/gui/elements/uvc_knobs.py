from nicegui import ui

# ---------- Grid 3x3 --------------------------------------------------------
for row in range(3):
    with ui.row().classes('gap-4'):
        for col in range(3):
            index = row * 3 + col + 1
            with ui.card().tight().classes('w-52 flex flex-col items-center'):
                # ⬆️ Deutlich hervorgehobene Überschrift
                ui.label(f'Knob {index}')\
                    .classes('text-lg font-semibold text-center mb-2')
                
                # 🎛️ Der eigentliche Regler
                ui.knob(
                    min=0,
                    max=100,
                    value=50,
                    show_value=True,
                ).classes('w-44 h-44')
                
                # Optionale Zusatzinfo
                ui.label('0–100').classes('text-sm text-gray-500')
# ---------------------------------------------------------------------------

ui.run()
