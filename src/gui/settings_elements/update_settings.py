from __future__ import annotations
import asyncio

from nicegui import ui

from src.update import check_update
from src.config import get_logger
from src.gui.settings_elements.ui_helpers import create_section_heading

logger = get_logger('gui.update_settings')


def create_update_settings() -> None:
    """Render the Software Update controls.

    Provides a button to check for updates and, if available, navigates to the
    '/updating' page which handles the update flow.
    """

    with ui.column().classes('gap-3'):
        create_section_heading(
            'Software Update',
            icon='system_update',
            caption='Check for updates and install them if available.',
            title_classes='text-subtitle1 font-semibold',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )

        status_label = ui.label('').classes('text-body2')

        async def on_update_click() -> None:
            try:
                status_label.text = 'Checking for updates…'
                stat = await asyncio.to_thread(check_update)
            except Exception as e:
                logger.exception('Update check failed')
                status_label.text = ''
                ui.notify(f'Update check failed: {e}', type='negative', position='bottom-right')
                return

            behind = int(stat.get('behind', 0) or 0)
            if behind <= 0:
                status_label.text = 'Already up to date.'
                ui.notify('Already up to date.', type='positive', position='bottom-right')
                return

            status_label.text = f'Updates available (behind={behind}). Opening updater…'
            logger.info('Updates available; navigating to /updating')
            ui.navigate.to('/updating', new_tab=False)

        ui.button('Check & Update', icon='system_update', on_click=on_update_click).props('color=primary')
