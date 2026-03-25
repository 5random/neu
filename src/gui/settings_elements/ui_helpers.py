from __future__ import annotations

from typing import Callable, Optional, Any

from nicegui import ui

SECTION_ICONS = {
    'camera': 'photo_camera',
    'camera_feed': 'photo_camera',
    'camera_controls': 'photo_camera',
    'motion': 'sensors',
    'motion_status': 'sensors',
    'measurement': 'straighten',
    'email': 'mail',
    'config': 'description',
    'metadata': 'badge',
    'update': 'system_update',
    'logs': 'receipt_long',
    'stats': 'analytics',
    'history': 'history',
}
ACTION_BUTTON_PRESETS = {
    'apply': {'label': 'Apply', 'icon': 'done', 'color': 'positive'},
    'save': {'label': 'Save', 'icon': 'save', 'color': 'positive'},
    'reset': {'label': 'Reset', 'icon': 'restart_alt', 'color': 'warning'},
    'clear': {'label': 'Clear', 'icon': 'clear', 'color': 'negative'},
}


def create_heading_row(
    title: str,
    *,
    icon: str,
    anchor_id: Optional[str] = None,
    title_classes: str = 'text-h6 font-semibold',
    row_classes: str = 'items-center gap-3 w-full',
    icon_classes: str = 'text-primary text-xl shrink-0',
) -> None:
    row = ui.row().classes(row_classes)
    if anchor_id:
        row.props(f'id={anchor_id}')
    with row:
        ui.icon(icon).classes(icon_classes)
        ui.label(title).classes(title_classes)


def create_section_heading(
    title: str,
    *,
    icon: str,
    caption: Optional[str] = None,
    anchor_id: Optional[str] = None,
    title_classes: str = 'text-h6 font-semibold',
    row_classes: str = 'items-center gap-3 w-full',
    icon_classes: str = 'text-primary text-xl shrink-0',
    column_classes: str = 'w-full gap-1',
    caption_classes: str = 'text-body2 text-grey-7',
) -> None:
    with ui.column().classes(column_classes):
        create_heading_row(
            title,
            icon=icon,
            anchor_id=anchor_id,
            title_classes=title_classes,
            row_classes=row_classes,
            icon_classes=icon_classes,
        )
        if caption:
            ui.label(caption).classes(caption_classes)


def create_action_button(
    kind: str,
    *,
    on_click: Optional[Callable[..., Any]] = None,
    label: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    props: str = 'unelevated no-caps',
    classes: str = 'font-medium',
    tooltip: Optional[str] = None,
) -> Any:
    preset = ACTION_BUTTON_PRESETS.get(kind, {})
    button = ui.button(
        label or preset.get('label', kind.title()),
        icon=icon or preset.get('icon'),
        on_click=on_click,
    )
    color_value = color or preset.get('color')
    prop_parts = [f'color={color_value}'] if color_value else []
    if props:
        prop_parts.append(props)
    button.props(' '.join(prop_parts))
    if classes:
        button.classes(classes)
    if tooltip:
        button.tooltip(tooltip)
    return button
