from __future__ import annotations

from nicegui import ui

from src.gui.constants import StorageKeys
from src.gui.layout import apply_dark_mode_preference, get_page_dark_mode_controller, set_dark_mode_preference
from src.gui.settings_elements.ui_helpers import create_section_heading
from src.gui.storage import get_ui_pref


def create_appearance_settings() -> None:
    """Render appearance-related UI preferences for the current browser."""

    with ui.column().classes('gap-3'):
        create_section_heading(
            'Appearance',
            icon='palette',
            caption='Adjust how the interface looks in this browser.',
            title_classes='text-subtitle1 font-semibold',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )

        dark_mode = get_page_dark_mode_controller()
        if dark_mode is None:
            dark_mode = apply_dark_mode_preference()

        def _apply_dark_mode(enabled: bool) -> None:
            normalized_value = bool(enabled)
            set_dark_mode_preference(normalized_value)
            if bool(dark_mode.value) != normalized_value:
                dark_mode.value = normalized_value

        with ui.card().classes('w-full').props('flat bordered'):
            with ui.row().classes('w-full items-center justify-between gap-4'):
                with ui.column().classes('gap-1 min-w-0'):
                    ui.label('Dark mode').classes('text-body1 font-medium')
                    ui.label('Switch between the light and dark application theme.').classes('text-body2 text-grey-7')
                ui.switch(
                    'Enabled',
                    value=bool(get_ui_pref(StorageKeys.DARK_MODE, False)),
                    on_change=lambda event: _apply_dark_mode(bool(event.value)),
                ).props('color=primary')
