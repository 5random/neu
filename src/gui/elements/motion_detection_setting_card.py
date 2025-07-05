from __future__ import annotations
from typing import Optional, Tuple, TypedDict
from pathlib import Path
import json

from nicegui import ui
from nicegui.events import MouseEventArguments

def create_motiondetection_card():
    # ─────────────────────────── Bildkonstanten ──────────────────────────────
    IMG_SRC = 'https://picsum.photos/id/325/720/405'
    IMG_W, IMG_H = 720, 405                                   # 16:9

    # ─────────────────────────── ROI-State ───────────────────────────────────
    class ROIState(TypedDict):
        p1: Optional[Tuple[int, int]]
        p2: Optional[Tuple[int, int]]

    state: ROIState = {'p1': None, 'p2': None}
    ROI_FILE = Path('roi_config.json')

    # ─────────────────────────── SVG-Helper ──────────────────────────────────
    def svg_cross(x: int, y: int, s: int = 14, col: str = 'deepskyblue') -> str:
        h = s // 2
        return (f'<line x1="{x-h}" y1="{y}" x2="{x+h}" y2="{y}" stroke="{col}" '
                'stroke-width="3" stroke-linecap="round" />'
                f'<line x1="{x}" y1="{y-h}" x2="{x}" y2="{y+h}" stroke="{col}" '
                'stroke-width="3" stroke-linecap="round" />')

    def svg_circle(x: int, y: int, r: int = 8, col: str = 'gold') -> str:
        return (f'<circle cx="{x}" cy="{y}" r="{r}" stroke="{col}" '
                'stroke-width="3" fill="none" />')

    # ─────────────────────────── ROI-Logik ───────────────────────────────────
    def roi_bounds() -> Optional[Tuple[int, int, int, int]]:
        if state['p1'] and state['p2']:
            x0, y0 = map(min, zip(state['p1'], state['p2']))
            x1, y1 = map(max, zip(state['p1'], state['p2']))
            return x0, y0, x1, y1
        return None

    def roi_text() -> str:
        b = roi_bounds()
        if b:
            x0, y0, x1, y1 = b
            return f'ROI: ({x0}, {y0}) – ({x1}, {y1})'
        elif state['p1']:
            x, y = state['p1']
            return f'ROI: ({x}, {y}) – (…)'
        return 'ROI: nicht aktiv'

    # ── UI-Update-Funktionen für den Editor ──
    def update_overlay() -> None:
        parts: list[str] = []
        if state['p1']: parts.append(svg_cross(*state['p1']))
        if state['p2']: parts.append(svg_cross(*state['p2']))
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            parts.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" '
                        'stroke="lime" stroke-width="3" fill="none" />')
            parts.extend([svg_circle(x0, y0), svg_circle(x1, y1)])
        image.content = ''.join(parts)

    def update_labels() -> None:
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            tl_label.text, br_label.text = f'({x0}, {y0})', f'({x1}, {y1})'
        elif state['p1']:
            tl_label.text, br_label.text = f'({state["p1"][0]}, {state["p1"][1]})', '–'
        else:
            tl_label.text = br_label.text = '–'

    def refresh_ui() -> None:
        update_overlay()
        update_labels()

    def reset_roi() -> None:
        state['p1'] = state['p2'] = None
        refresh_ui()

    def handle_click(e: MouseEventArguments) -> None:
        x, y = int(e.image_x), int(e.image_y)
        tgt = 'p1' if state['p1'] is None else ('p2' if state['p2'] is None else None)
        if tgt:
            state[tgt] = (x, y)
        else:
            reset_roi()
            handle_click(e)
            return
        refresh_ui()

    # ─── Speichern ───
    def save_roi() -> None:
        if (b := roi_bounds()):
            x0, y0, x1, y1 = b
            ROI_FILE.write_text(json.dumps(
                {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1}, indent=2))
            ui.notify('ROI gespeichert', type='positive', position='bottom-right')
        else:
            ui.notify('Bitte zuerst beide Ecken wählen!',
                    type='warning', position='bottom-right')

    # ─────────────────────── ROI-Editor-UI ───────────────────────
    def create_roi_editor() -> None:
        with ui.card().classes('w-full max-w-none lg:max-w-7xl mx-auto shadow-4 rounded-borders'):
            with ui.grid(columns='3fr 1fr').classes('gap-6 p-6'):
                global image
                image = (ui.interactive_image(IMG_SRC, on_mouse=handle_click,
                                            events=['click'], cross=True)
                        .style(f'aspect-ratio:{IMG_W}/{IMG_H};width:100%;height:auto;'
                                'object-fit:contain;')
                        .classes('rounded-borders'))
                with ui.column().classes('gap-4'):
                    ui.label('ROI-Koordinaten').classes('text-h5 font-medium')
                    with ui.row():
                        ui.label('oben links:'); global tl_label
                        tl_label = ui.label('–').classes('font-mono')
                    with ui.row():
                        ui.label('unten rechts:'); global br_label
                        br_label = ui.label('–').classes('font-mono')
                    with ui.row().classes('gap-2'):
                        ui.button('Zurücksetzen', icon='restart_alt',
                                color='primary', on_click=reset_roi)
                        ui.button('Speichern', icon='save',
                                color='primary', on_click=save_roi)
        refresh_ui()

    # ─────────────────────────── Routen ──────────────────────────────────────
    @ui.page('/')
    def motion_setting_page():
        with ui.card().classes('w-full max-w-md mx-auto shadow-4 rounded-borders p-6'):
            ui.label('Motion-Setting').classes('text-h5 mb-4')

            # Knob und ROI-Controls nebeneinander
            with ui.row().classes('items-center justify-center gap-6 mb-6'):
                # Knob-Card
                with ui.card().tight().classes('w-52 flex flex-col items-center'):
                    ui.label('Knob 1').classes('font-semibold mb-2')
                    ui.knob(min=0, max=100, value=50, show_value=True).classes('w-44 h-44')
                    ui.label('0 – 100').classes('text-sm text-gray-500')

                # ROI-Text und Button
                with ui.column().classes('gap-2 items-start'):
                    global label_roi
                    label_roi = ui.label(roi_text()).classes('text-md font-mono')
                    btn = ui.button('ROI Editor öffnen', icon='crop',
                                    on_click=lambda: ui.navigate.to('/roi-editor'))
                    btn.tooltip('ROI Editor öffnen')

    @ui.page('/roi-editor')
    def roi_page():
        ui.link('← Zurück', '/').classes('mb-4')
        create_roi_editor()
