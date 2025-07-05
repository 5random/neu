from __future__ import annotations
from typing import Optional, Tuple, TypedDict
from nicegui import ui
from nicegui.events import MouseEventArguments

# ────────────────── Zustand ────────────────────────────────────────────────
class ROIState(TypedDict):
    p1: Optional[Tuple[int, int]]
    p2: Optional[Tuple[int, int]]

state: ROIState = {'p1': None, 'p2': None}

# ────────────────── SVG-Helfer ─────────────────────────────────────────────
def svg_cross(x: int, y: int, size: int = 14, color: str = 'deepskyblue') -> str:
    h = size // 2
    return (
        f'<line x1="{x-h}" y1="{y}" x2="{x+h}" y2="{y}" '
        f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        f'<line x1="{x}" y1="{y-h}" x2="{x}" y2="{y+h}" '
        f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
    )

def svg_circle(x: int, y: int, r: int = 8, color: str = 'gold') -> str:
    return (
        f'<circle cx="{x}" cy="{y}" r="{r}" '
        f'stroke="{color}" stroke-width="3" fill="none" />'
    )

# ────────────────── Anzeige & Overlay ─────────────────────────────────────
def update_overlay() -> None:
    parts: list[str] = []

    # 1) Klick-Marker (Kreuze) immer anzeigen
    if state['p1'] is not None:
        parts.append(svg_cross(*state['p1']))
    if state['p2'] is not None:
        parts.append(svg_cross(*state['p2']))

    # 2) Rechteck + Kreise nur, wenn beide Punkte gesetzt sind
    if state['p1'] and state['p2']:
        # sortiere Koordinaten
        x0 = min(state['p1'][0], state['p2'][0])
        y0 = min(state['p1'][1], state['p2'][1])
        x1 = max(state['p1'][0], state['p2'][0])
        y1 = max(state['p1'][1], state['p2'][1])

        # Rechteck
        parts.append(
            f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" '
            f'stroke="lime" stroke-width="3" fill="none" />'
        )
        # Kreise um obere linke und untere rechte Ecke
        parts.append(svg_circle(x0, y0))
        parts.append(svg_circle(x1, y1))

    image.content = ''.join(parts)

def update_labels() -> None:
    if state['p1'] and state['p2']:
        x0 = min(state['p1'][0], state['p2'][0])
        y0 = min(state['p1'][1], state['p2'][1])
        x1 = max(state['p1'][0], state['p2'][0])
        y1 = max(state['p1'][1], state['p2'][1])
        tl_label.text = f'({x0}, {y0})'
        br_label.text = f'({x1}, {y1})'
    elif state['p1']:
        tl_label.text = f'({state["p1"][0]}, {state["p1"][1]})'
        br_label.text = '–'
    else:
        tl_label.text = br_label.text = '–'

def refresh_ui() -> None:
    update_overlay()
    update_labels()

# ────────────────── Reset ─────────────────────────────────────────────────
def reset_roi() -> None:
    state['p1'] = state['p2'] = None
    refresh_ui()

# ────────────────── Maus-Handler ──────────────────────────────────────────
def handle_click(e: MouseEventArguments) -> None:
    x, y = int(e.image_x), int(e.image_y)
    if state['p1'] is None:
        state['p1'] = (x, y)
    elif state['p2'] is None:
        state['p2'] = (x, y)
    else:                          # dritter Klick → neue Auswahl
        reset_roi()
        handle_click(e)
        return
    refresh_ui()

# ────────────────── UI-Layout ─────────────────────────────────────────────
ui.dark_mode().enable()

with ui.card().classes('max-w-4xl mx-auto shadow-4 rounded-borders'):
    with ui.grid(columns='2fr 1fr').classes('gap-6 p-6'):
        image = ui.interactive_image(
            'https://picsum.photos/id/325/720/405',
            on_mouse=handle_click,
            events=['click'],
            cross=True,
        ).classes('rounded-borders')

        with ui.column().classes('gap-4'):
            ui.label('ROI-Koordinaten').classes('text-h5 font-medium')
            with ui.row().classes('items-baseline gap-2'):
                ui.label('oben links:').classes('text-body1')
                tl_label = ui.label('–').classes('text-body1 font-mono text-lg')
            with ui.row().classes('items-baseline gap-2'):
                ui.label('unten rechts:').classes('text-body1')
                br_label = ui.label('–').classes('text-body1 font-mono text-lg')
            ui.button('Auswahl zurücksetzen', icon='restart_alt',
                      color='primary', 
                      on_click=reset_roi).classes('self-start')

ui.run(reload=False)
