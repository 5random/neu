from typing import Any
import json

_settings_page: Any = None
try:
    from src.gui.settings_page import settings_page as _settings_page  # noqa: F401
except Exception:
    pass

from nicegui import ui, app
import sys

from src.config import load_config, set_global_config, get_logger
from src.gui import init, cleanup

from src.alert_history import HISTORY_STATIC_ROUTE, get_history_dir

# Register help and default page routes via import side effect
from .help.help import help_page  # noqa: F401
from src.gui.default_page import index_page as default_page  # noqa: F401
from .power_actions import get_power_action_spec

logger = get_logger("gui")

from .layout import build_header, build_footer, compute_gui_title, install_overlay_styles
from .util import set_tab

_title_sync_registered = False

# Register signal handlers for graceful shutdown
cleanup.register_signal_handlers()


def refresh_connected_clients(*, client: Any = None, broadcast: bool = False, delay_ms: int = 150) -> None:
    """Trigger a one-time page reload for the given or all connected clients."""
    reload_delay = max(0, int(delay_ms))
    reload_code = f'window.setTimeout(() => window.location.reload(), {reload_delay});'

    try:
        if client is not None:
            if hasattr(client, 'run_javascript'):
                client.run_javascript(reload_code)
        elif broadcast:
            clients_dict = getattr(app, 'clients', {})
            try:
                clients = list(getattr(clients_dict, 'values', lambda: [])())
            except Exception:
                clients = []
            for connected_client in clients:
                refresh_connected_clients(client=connected_client, delay_ms=reload_delay)
    except Exception:
        logger.debug('Failed to refresh client(s)', exc_info=True)


def sync_runtime_gui_title(*, title: str | None = None, client: Any = None, broadcast: bool = False) -> str:
    """Sync the current metadata-based GUI title into NiceGUI runtime state and clients."""
    resolved_title = str(title or compute_gui_title())

    try:
        app.config.title = resolved_title
    except Exception:
        pass

    try:
        app.storage.general['cvd.runtime_title'] = resolved_title
    except Exception:
        pass

    label_sync_code = f"""
    const title = {json.dumps(resolved_title)};
    for (const id of ['cvd-header-title', 'cvd-footer-title']) {{
        const element = document.getElementById(id);
        if (element) {{
            element.textContent = title;
        }}
    }}
    """

    try:
        if client is not None:
            if hasattr(client, 'title'):
                client.title = resolved_title
            set_tab(title=resolved_title, client=client)
            if hasattr(client, 'run_javascript'):
                client.run_javascript(label_sync_code)
        elif broadcast:
            clients_dict = getattr(app, 'clients', {})
            try:
                clients = list(getattr(clients_dict, 'values', lambda: [])())
            except Exception:
                clients = []
            for connected_client in clients:
                sync_runtime_gui_title(title=resolved_title, client=connected_client)
    except Exception:
        logger.debug('Failed to sync GUI title to client(s)', exc_info=True)

    return resolved_title


def _sync_title_on_connect(client: Any = None) -> None:
    sync_runtime_gui_title(client=client)


def _ensure_title_sync_registered() -> None:
    global _title_sync_registered
    if _title_sync_registered:
        return
    app.on_connect(_sync_title_on_connect)
    _title_sync_registered = True


def build_post_restart_redirect_script(
    *,
    marker_key: str,
    target_route: str = '/',
) -> str:
    """Return browser-side logic that redirects to the dashboard after a restart rebuild."""
    return f"""
    (function() {{
        const markerKey = {json.dumps(marker_key)};
        const targetRoute = {json.dumps(target_route)};
        try {{
            const isPending = window.sessionStorage.getItem(markerKey) === 'pending';
            if (isPending) {{
                window.sessionStorage.removeItem(markerKey);
                window.location.replace(targetRoute);
                return;
            }}
            window.sessionStorage.setItem(markerKey, 'pending');
        }} catch (error) {{
            console.warn('CVD restart redirect setup failed', error);
        }}
    }})();
    """


def install_post_restart_redirect(
    *,
    marker_key: str,
    target_route: str = '/',
) -> None:
    """Install a one-time redirect that returns the browser to the dashboard after restart."""
    ui.run_javascript(
        build_post_restart_redirect_script(
            marker_key=marker_key,
            target_route=target_route,
        )
    )

def create_gui(config_path: str = "config/config.yaml") -> None:
    """Initialisierung vor ui.run(): Konfiguration laden und App initialisieren.

    Die eigentlichen Seiten sind per @ui.page deklariert.
    """
    try:
        # Centralized initialization
        init.init_application(config_path)
        _ensure_title_sync_registered()
        sync_runtime_gui_title()
        
        # Optional: statische Pfade einmalig mounten
        try:
            history_dir = get_history_dir()
            history_dir.mkdir(parents=True, exist_ok=True)
            app.add_static_files(HISTORY_STATIC_ROUTE, str(history_dir))
        except Exception:
            pass
        try:
            app.add_static_files('/pics', 'pics')
        except Exception:
            pass
        try:
            app.add_static_files('/logs', 'logs')
        except Exception:
            pass
            
        logger.info('GUI initialized; config loaded')
    except Exception as e:
        logger.error(f"Failed to initialize GUI: {e}")
        # Nicht crashen, ui.run darf weiterlaufen

def _build_power_action_status_page(action_key: str) -> None:
    spec = get_power_action_spec(action_key)
    install_overlay_styles()
    with ui.column().classes('absolute-center items-center gap-6'):
        ui.icon(spec.status_icon).classes(spec.status_icon_classes)
        ui.label(spec.status_title).classes('text-h4 font-medium text-center')
        ui.label(spec.status_message).classes('text-body1 text-center max-w-[32rem]')


@ui.page('/shutdown')
def shutdown_page() -> None:
    _build_power_action_status_page('app_shutdown')


@ui.page('/restart')
def restart_page() -> None:
    _build_power_action_status_page('app_restart')
    install_post_restart_redirect(marker_key='cvd.app_restart.pending_redirect')


@ui.page('/pi-restart')
def pi_restart_page() -> None:
    _build_power_action_status_page('pi_restart')
    install_post_restart_redirect(marker_key='cvd.pi_restart.pending_redirect')


@ui.page('/pi-shutdown')
def pi_shutdown_page() -> None:
    _build_power_action_status_page('pi_shutdown')

@ui.page('/updating')
def updating_page() -> None:
    from src.update import check_update, perform_update, restart_self
    import queue
    import asyncio
    
    logger.info('Opening updating page...')
    install_overlay_styles()
    with ui.column().classes('absolute-center items-center gap-4'):
        ui.icon('system_update').classes('text-6xl text-primary')
        ui.label('Update wird installiert...').classes('text-h5 font-medium')
        status = ui.label('').classes('text-body2')
        log = ui.log(max_lines=500).classes('w-[800px] h-[360px] bg-black text-green-400 rounded')

        # Thread-safe progress queue for background thread messages
        q: queue.Queue[str] = queue.Queue()

        def drain_progress() -> None:
            try:
                while True:
                    msg = q.get_nowait()
                    log.push(msg)
                    logger.info(msg)
            except queue.Empty:
                pass

        async def run_update() -> None:
            try:
                # 1) Status prüfen
                logger.info('Checking update status...')
                stat = await asyncio.to_thread(check_update)
                status.text = f"Lokaler Commit {stat.get('local')} → Remote {stat.get('remote') or ''} (behind={stat.get('behind', 0)})"
                logger.info(f"Update status: behind={stat.get('behind', 0)}, local={stat.get('local')}, remote={stat.get('remote')}")

                # 2) Update im Hintergrund durchführen
                logger.info('Starting update...')
                ok = await asyncio.to_thread(perform_update, q.put)

                if ok:
                    logger.info('Update completed successfully; restarting...')
                    ui.notify('Update abgeschlossen. Neustart...', type='positive', position='bottom-right')
                    # 3) Sauberes Cleanup + Self-Restart (cleanup on event loop thread)
                    cleanup.cleanup_application()
                    await asyncio.sleep(0.3)
                    await asyncio.to_thread(restart_self)
                else:
                    logger.warning('Update failed or not available.')
                    ui.notify('Update fehlgeschlagen oder nicht verfügbar.', type='warning', position='bottom-right')
                    ui.button('Zurück', on_click=lambda: ui.navigate.to('/')).props('flat').classes('q-mt-md')
            except Exception as e:
                logger.exception('Update process failed')
                ui.notify(f'Update failed: {e}', type='negative', position='bottom-right')
                ui.button('Zurück', on_click=lambda: ui.navigate.to('/')).props('flat').classes('q-mt-md')

        # Drain progress queue on UI thread
        ui.timer(0.1, drain_progress)
        ui.timer(0.05, run_update, once=True)

