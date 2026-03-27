from __future__ import annotations

"""Legacy compatibility wrapper for the old email card module.

The maintained email settings UI lives in
``src.gui.settings_elements.email_settings.create_emailcard``.
This wrapper keeps older imports working without preserving a second,
diverging implementation.
"""

from typing import Optional

from src.config import AppConfig, get_logger
from src.notify import EMailSystem
from src.gui.settings_elements.email_settings import create_emailcard as create_settings_emailcard

logger = get_logger('gui.emailcard_legacy')


def create_emailcard(*, config: Optional[AppConfig] = None, email_system: Optional[EMailSystem] = None) -> None:
    """Delegate to the maintained settings email card implementation."""

    if config is not None:
        logger.info(
            'Legacy email card wrapper called with explicit config; delegating to settings email card and using the global config state'
        )
    create_settings_emailcard(email_system=email_system)
