from __future__ import annotations
from pathlib import Path

from nicegui import ui, app

from src.config import get_logger

logger = get_logger('gui.config_settings')


def create_config_settings() -> None:
    """Render a button to download the active configuration file (config.yaml)."""

    def download_config_yaml() -> None:
        cfg_dir = Path('config')
        cfg_file = cfg_dir / 'config.yaml'
        if not cfg_file.exists():
            ui.notify('Config file not found (config/config.yaml)', type='warning', position='bottom-right')
            return

        # Ensure static mapping exists (idempotent)
        try:
            app.add_static_files('/config', str(cfg_dir))
        except Exception:
            # Mapping may already exist
            pass

        ui.download.from_url('/config/config.yaml')
        ui.notify('Config downloaded', type='positive', position='bottom-right')
        logger.info('Config file offered for download')

    with ui.column().classes('gap-3'):
        ui.label('Configuration').classes('text-subtitle1 font-semibold').props('id=config')
        ui.label('Download the currently active configuration file for backup or review.').classes('text-body2')
        with ui.row().classes('gap-2'):
            ui.button('Download config.yaml', icon='download', on_click=download_config_yaml).props('color=primary')
