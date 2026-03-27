import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nicegui import ui, app
from src.config import AppConfig, get_global_config, save_global_config
from .constants import StorageKeys
from .storage import get_ui_pref, get_ui_storage, set_ui_pref

logger = logging.getLogger('gui.layout')
_RUNTIME_WEBSITE_URL_KEY = 'cvd.runtime_website_url'

_OVERLAY_HEAD_HTML = """
<style>
/* Keep notifications above sticky controls and other fixed overlays. */
.q-notifications__list,
.q-notification {
    z-index: 12000 !important;
}
</style>
"""


def compute_gui_title(
    cfg: AppConfig | None = None,
    *,
    cvd_id: object | None = None,
    cvd_name: object | None = None,
) -> str:
    """Compute the browser/UI title from the configured template and metadata."""
    try:
        resolved_cfg = cfg or get_global_config()
        if resolved_cfg and getattr(resolved_cfg, 'gui', None):
            tpl = getattr(resolved_cfg.gui, 'title', '') or 'CVD-TRACKER'
            meta = getattr(resolved_cfg, 'metadata', None)
            params = {
                'cvd_id': getattr(meta, 'cvd_id', '') if cvd_id is None else cvd_id,
                'cvd_name': getattr(meta, 'cvd_name', '') if cvd_name is None else cvd_name,
            }
            try:
                return str(tpl).format(**params)
            except Exception:
                return str(tpl)
    except Exception:
        pass
    return 'CVD-TRACKER'


def install_overlay_styles() -> None:
    """Ensure transient notifications render above fixed/sticky controls."""
    ui.add_head_html(_OVERLAY_HEAD_HTML)


def _normalize_runtime_website_url(raw_url: object | None) -> str | None:
    """Normalize an absolute request URL to the app base URL."""
    text = str(raw_url or '').strip()
    if not text:
        return None

    try:
        parts = urlsplit(text)
    except Exception:
        return None

    if not parts.scheme or not parts.netloc:
        return None

    path = parts.path or '/'
    if not path.endswith('/'):
        if path.count('/') > 1:
            path = path.rsplit('/', 1)[0] + '/'
        else:
            path = '/'

    return urlunsplit((parts.scheme, parts.netloc, path or '/', '', ''))


def _resolve_runtime_website_url(client: object | None = None) -> str | None:
    """Resolve the current app base URL from the active NiceGUI client."""
    active_client = client
    if active_client is None:
        try:
            active_client = ui.context.client
        except Exception:
            active_client = None

    request = getattr(active_client, 'request', None) if active_client is not None else None
    for candidate in (getattr(request, 'base_url', None), getattr(request, 'url', None)):
        normalized = _normalize_runtime_website_url(candidate)
        if normalized:
            return normalized
    return None


def sync_runtime_website_url(*, client: object | None = None, persist: bool = True) -> str | None:
    """Sync the current app base URL into runtime state and email config."""
    resolved_url = _resolve_runtime_website_url(client=client)
    if not resolved_url:
        return None

    try:
        current_runtime = str(app.storage.general.get(_RUNTIME_WEBSITE_URL_KEY, '') or '').strip()
        if current_runtime != resolved_url:
            app.storage.general[_RUNTIME_WEBSITE_URL_KEY] = resolved_url
    except Exception:
        logger.debug('Failed to update runtime website URL storage', exc_info=True)

    cfg = get_global_config()
    email_cfg = getattr(cfg, 'email', None) if cfg is not None else None
    if email_cfg is None:
        return resolved_url

    current_configured = str(getattr(email_cfg, 'website_url', '') or '').strip()
    if current_configured == resolved_url:
        return resolved_url

    email_cfg.website_url = resolved_url
    if persist:
        if save_global_config():
            logger.info('Updated email.website_url to %s', resolved_url)
        else:
            logger.warning('Failed to persist adaptive email.website_url=%s', resolved_url)
    return resolved_url


def _persist_dark_mode_preference(enabled: bool) -> None:
    """Persist the dark mode preference for the current browser profile."""
    set_ui_pref(StorageKeys.DARK_MODE, bool(enabled))


def apply_dark_mode_preference() -> Any:
    """Create the page-local dark mode controller from persisted UI preferences."""
    dark = ui.dark_mode(
        value=bool(get_ui_pref(StorageKeys.DARK_MODE, False)),
        on_change=lambda event: _persist_dark_mode_preference(bool(event.value)),
    )
    try:
        setattr(ui.context.client, 'cvd_dark_mode_controller', dark)
    except Exception:
        logger.debug('Failed to store dark mode controller on client', exc_info=True)
    return dark


def get_page_dark_mode_controller() -> Any | None:
    """Return the current page's dark mode controller if available."""
    try:
        client = ui.context.client
    except Exception:
        return None
    return getattr(client, 'cvd_dark_mode_controller', None)


def set_dark_mode_preference(enabled: bool) -> None:
    """Apply and persist the dark mode preference for the current page."""
    normalized_value = bool(enabled)
    _persist_dark_mode_preference(normalized_value)
    dark = get_page_dark_mode_controller()
    if dark is not None and bool(dark.value) != normalized_value:
        dark.value = normalized_value


def _resolve_header_route(explicit_route: str | None = None) -> str | None:
    """Resolve the current route for header-specific UI decisions."""
    if explicit_route:
        return str(explicit_route).strip() or None

    try:
        client = ui.context.client
    except Exception:
        client = None

    request = getattr(client, 'request', None) if client is not None else None
    request_url = getattr(request, 'url', None)
    if request_url is None:
        return None

    try:
        return urlsplit(str(request_url)).path or '/'
    except Exception:
        return None


def build_header(current_route: str | None = None) -> None:
    install_overlay_styles()
    sync_runtime_website_url(persist=True)
    current_title = compute_gui_title()
    resolved_route = _resolve_header_route(current_route)
    show_home_button = resolved_route in ('/settings', '/help')
    show_settings_button = resolved_route in ('/', '/help')

    if resolved_route not in ('/', '/settings', '/help'):
        show_home_button = True
        show_settings_button = True

    ui.page_title(current_title)
    with ui.header().classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        apply_dark_mode_preference()

        # Refresh the shared title on each page build so config changes are reflected.
        set_ui_pref(StorageKeys.GUI_TITLE, current_title)

        # --- Linke Seite -------------------------------------------
        with ui.row().classes('items-center gap-3'):
            shutdown_dialog = ui.dialog().classes('items-center justify-center')
            with shutdown_dialog:
                with ui.card().classes('items-center justify-center'):
                    ui.label('Shutdown the server?').classes('text-h6')
                    
                    async def do_shutdown() -> None:
                        ui.navigate.to('/shutdown', new_tab=False)
                        import asyncio
                        await asyncio.sleep(2)
                        app.shutdown()

                    with ui.row().classes('gap-2 items-center justify-center'):
                        ui.button('Yes', on_click=do_shutdown).props('color=negative').tooltip('Shutdown the server and close the application')
                        ui.button('No', on_click=shutdown_dialog.close).props('color=positive').tooltip('Cancel shutdown')

            def show_shutdown_dialog() -> None:
                shutdown_dialog.open()

            ui.button(icon='img:/pics/logo_ipc_short.svg', on_click=show_shutdown_dialog).props('flat').style('max-height:72px; width:auto').tooltip('Shutdown the server and close the application')

            title_label = ui.label().props('id=cvd-header-title').classes(
                'text-xl font-semibold tracking-wider text-gray-100')
            try:
                title_label.bind_text_from(get_ui_storage(), StorageKeys.GUI_TITLE)
            except Exception:
                title_label.text = current_title

        # --- Rechte Seite ------------------------------------------
        with ui.row().classes('items-center gap-4'):
            ui.button(icon='help', on_click=lambda: ui.navigate.to('/help'))\
                .props('flat round dense').classes('text-xl').tooltip('Help')

            def _go_home() -> None:
                set_ui_pref(StorageKeys.LAST_ROUTE, '/')
                ui.navigate.to('/', new_tab=False)

            def _go_settings() -> None:
                set_ui_pref(StorageKeys.LAST_ROUTE, '/settings')
                ui.navigate.to('/settings', new_tab=False)

            if show_home_button:
                ui.button(icon='home', on_click=_go_home)\
                  .props('flat round dense id=cvd-header-home').classes('text-xl').tooltip('Home')

            if show_settings_button:
                ui.button(icon='settings', on_click=_go_settings)\
                  .props('flat round dense id=cvd-header-settings').classes('text-xl').tooltip('Open settings')

def build_footer() -> None:
    with ui.footer(fixed=False).classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        with ui.row().classes('items-center justify-between px-4 py-2'):
            footer_label = ui.label().props('id=cvd-footer-title').classes('text-white text-sm')
            try:
                footer_label.bind_text_from(get_ui_storage(), StorageKeys.GUI_TITLE)
            except Exception:
                footer_label.text = compute_gui_title()
            ui.label('© 2025 TUHH KVWEB').classes('text-white text-sm')
