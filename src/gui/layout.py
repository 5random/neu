from nicegui import ui, app
from src.config import get_global_config
from .constants import StorageKeys
from .storage import get_ui_pref, get_ui_storage, set_ui_pref

_OVERLAY_HEAD_HTML = """
<style>
/* Keep notifications above sticky controls and other fixed overlays. */
.q-notifications__list,
.q-notification {
    z-index: 12000 !important;
}
</style>
"""


def compute_gui_title(cfg: object | None = None, *, cvd_id: object | None = None, cvd_name: object | None = None) -> str:
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


def build_header() -> None:
    install_overlay_styles()
    with ui.header().classes('items-center justify-between shadow px-4 py-2 bg-[#1C3144] text-white'):
        dark = ui.dark_mode(value=bool(get_ui_pref(StorageKeys.DARK_MODE, False)))

        # Refresh the shared title on each page build so config changes are reflected.
        set_ui_pref(StorageKeys.GUI_TITLE, compute_gui_title())

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
                title_label.text = compute_gui_title()

        # --- Rechte Seite ------------------------------------------
        def toggle_dark() -> None:
            dark.toggle()
            new_icon = 'light_mode' if dark.value else 'dark_mode'
            btn.props(f'icon={new_icon}')
            set_ui_pref(StorageKeys.DARK_MODE, bool(dark.value))

        with ui.row().classes('items-center gap-4'):
            ui.button(icon='help', on_click=lambda: ui.navigate.to('/help'))\
                .props('flat round dense').classes('text-xl').tooltip('Help')
            
            btn = ui.button(
                icon='light_mode' if dark.value else 'dark_mode',
                on_click=toggle_dark,
            ).props('flat round dense').classes('text-xl').tooltip('Toggle dark mode')

            def _go_home() -> None:
                set_ui_pref(StorageKeys.LAST_ROUTE, '/')
                ui.navigate.to('/', new_tab=False)

            def _go_settings() -> None:
                set_ui_pref(StorageKeys.LAST_ROUTE, '/settings')
                ui.navigate.to('/settings', new_tab=False)

            ui.button(icon='home', on_click=_go_home)\
              .props('flat round dense id=cvd-header-home').classes('text-xl').tooltip('Home')

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
