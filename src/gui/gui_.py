from typing import Any
from datetime import datetime
import json
import threading

_settings_page: Any = None
try:
    from src.gui.settings_page import settings_page as _settings_page  # noqa: F401
except Exception:
    pass

from nicegui import ui, app
import sys

from src.config import get_logger
import src.gui.cleanup as gui_cleanup
import src.gui.init as gui_init
import src.gui.instances as gui_instances

from src.alert_history import HISTORY_STATIC_ROUTE, get_history_dir

# Register help and default page routes via import side effect
from .help.help import help_page  # noqa: F401
from src.gui.default_page import index_page as default_page  # noqa: F401
from .power_actions import get_power_action_spec

logger = get_logger("gui")

from .layout import build_header, build_footer, compute_gui_title, install_overlay_styles
from .util import (
    favicon_check_circle_green,
    favicon_radio_button_checked_neutral,
    favicon_sensors_off_orange,
    get_default_favicon_url,
    register_client_disconnect_handler,
    schedule_bg,
    set_tab,
)

_title_sync_registered = False
_RUNTIME_STATE_UNSET = object()
_runtime_measurement_state_lock = threading.RLock()
_runtime_measurement_state: dict[str, Any] = {
    'is_active': False,
    'session_id': None,
    'session_start_time': None,
    'recent_motion_detected': None,
}

# Register signal handlers for graceful shutdown
gui_cleanup.register_signal_handlers()


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


def _normalize_runtime_measurement_state(
    *,
    state: dict[str, Any] | None = None,
    is_active: Any = None,
    session_id: Any = None,
    session_start_time: Any = None,
    recent_motion_detected: Any = _RUNTIME_STATE_UNSET,
) -> dict[str, Any]:
    raw_state = dict(state or {})
    normalized_start = raw_state.get('session_start_time') if session_start_time is None else session_start_time
    if normalized_start is not None and not isinstance(normalized_start, datetime):
        logger.debug('Discarding non-datetime session_start_time: %r', normalized_start)
        normalized_start = None

    normalized_active = bool(raw_state.get('is_active', False) if is_active is None else is_active)
    raw_motion = (
        raw_state.get('recent_motion_detected', None)
        if recent_motion_detected is _RUNTIME_STATE_UNSET
        else recent_motion_detected
    )
    normalized_motion = None if raw_motion is None else bool(raw_motion)
    if not normalized_active:
        normalized_motion = None

    return {
        'is_active': normalized_active,
        'session_id': raw_state.get('session_id') if session_id is None else session_id,
        'session_start_time': normalized_start,
        'recent_motion_detected': normalized_motion,
    }


def _get_runtime_measurement_state() -> dict[str, Any]:
    with _runtime_measurement_state_lock:
        return dict(_runtime_measurement_state)


def _set_runtime_measurement_state(
    *,
    state: dict[str, Any] | None = None,
    is_active: Any = None,
    session_id: Any = None,
    session_start_time: Any = None,
    recent_motion_detected: Any = _RUNTIME_STATE_UNSET,
) -> dict[str, Any]:
    merged_state = _get_runtime_measurement_state()
    if state:
        merged_state.update(dict(state))

    normalized = _normalize_runtime_measurement_state(
        state=merged_state,
        is_active=is_active,
        session_id=session_id,
        session_start_time=session_start_time,
        recent_motion_detected=recent_motion_detected,
    )
    with _runtime_measurement_state_lock:
        _runtime_measurement_state.update(normalized)
        return dict(_runtime_measurement_state)


def _build_browser_display_title(base_title: str, measurement_state: dict[str, Any] | None = None) -> str:
    state = measurement_state or _get_runtime_measurement_state()
    if not bool(state.get('is_active', False)):
        return base_title

    recent_motion_detected = state.get('recent_motion_detected')
    if recent_motion_detected is True:
        icon_prefix = '\U0001F7E2'
    elif recent_motion_detected is False:
        icon_prefix = '\U0001F7E0'
    else:
        icon_prefix = '\u26AA'

    return f'{icon_prefix} Messung aktiv | {base_title}'


def _build_header_display_title(base_title: str, measurement_state: dict[str, Any] | None = None) -> str:
    state = measurement_state or _get_runtime_measurement_state()
    return f'Messung aktiv | {base_title}' if bool(state.get('is_active', False)) else base_title


def _resolve_runtime_title_visual_state(base_title: str, measurement_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = measurement_state or _get_runtime_measurement_state()
    browser_title = _build_browser_display_title(base_title, state)
    header_title = _build_header_display_title(base_title, state)
    is_active = bool(state.get('is_active', False))
    recent_motion_detected = state.get('recent_motion_detected')

    if not is_active:
        return {
            'browser_title': browser_title,
            'header_title': header_title,
            'header_icon_name': 'radio_button_checked',
            'header_icon_status_class': '',
            'header_icon_visible': False,
            'favicon_url': get_default_favicon_url(),
        }

    if recent_motion_detected is True:
        return {
            'browser_title': browser_title,
            'header_title': header_title,
            'header_icon_name': 'sensors',
            'header_icon_status_class': 'cvd-measurement-motion-detected',
            'header_icon_visible': True,
            'favicon_url': favicon_check_circle_green(),
        }

    if recent_motion_detected is False:
        return {
            'browser_title': browser_title,
            'header_title': header_title,
            'header_icon_name': 'sensors_off',
            'header_icon_status_class': 'cvd-measurement-no-motion',
            'header_icon_visible': True,
            'favicon_url': favicon_sensors_off_orange(),
        }

    return {
        'browser_title': browser_title,
        'header_title': header_title,
        'header_icon_name': 'radio_button_checked',
        'header_icon_status_class': 'cvd-measurement-pending',
        'header_icon_visible': True,
        'favicon_url': favicon_radio_button_checked_neutral(),
    }


def _build_runtime_title_sync_script(base_title: str, measurement_state: dict[str, Any]) -> str:
    visual_state = _resolve_runtime_title_visual_state(base_title, measurement_state)
    return f"""
    (function() {{
        const baseTitle = {json.dumps(base_title)};
        const headerTitle = {json.dumps(visual_state['header_title'])};
        const headerIconName = {json.dumps(visual_state['header_icon_name'])};
        const headerIconVisible = {json.dumps(bool(visual_state['header_icon_visible']))};
        const headerIconStatusClass = {json.dumps(visual_state['header_icon_status_class'])};
        const statusClasses = [
            'cvd-measurement-active',
            'cvd-measurement-motion-detected',
            'cvd-measurement-no-motion',
            'cvd-measurement-pending',
        ];

        const apply = () => {{
            const headerLabel = document.getElementById('cvd-header-title');
            const footerLabel = document.getElementById('cvd-footer-title');
            const headerIcon = document.getElementById('cvd-header-title-icon');

            if (footerLabel) {{
                footerLabel.textContent = baseTitle;
            }}
            if (headerLabel) {{
                headerLabel.textContent = headerTitle;
            }}
            if (headerIcon) {{
                headerIcon.style.display = headerIconVisible ? '' : 'none';
                headerIcon.textContent = headerIconName;
                headerIcon.classList.remove(...statusClasses);
                if (headerIconVisible) {{
                    headerIcon.classList.add('cvd-measurement-active');
                    if (headerIconStatusClass) {{
                        headerIcon.classList.add(headerIconStatusClass);
                    }}
                }}
            }}
            return Boolean(headerLabel || footerLabel || headerIcon);
        }};

        if (apply()) {{
            return;
        }}

        let attempts = 0;
        const timer = window.setInterval(() => {{
            attempts += 1;
            if (apply() || attempts >= 12) {{
                window.clearInterval(timer);
            }}
        }}, 75);
    }})();
    """


def sync_runtime_gui_title(
    *,
    title: str | None = None,
    client: Any = None,
    broadcast: bool = False,
    measurement_state: dict[str, Any] | None = None,
) -> str:
    """Sync the current metadata-based base title into NiceGUI runtime state and clients."""
    base_title = str(title or compute_gui_title())
    resolved_state = dict(measurement_state or _get_runtime_measurement_state())
    visual_state = _resolve_runtime_title_visual_state(base_title, resolved_state)
    browser_title = str(visual_state['browser_title'])
    favicon_url = str(visual_state['favicon_url'])

    try:
        app.config.title = base_title
    except Exception:
        pass

    try:
        app.storage.general['cvd.runtime_title'] = base_title
    except Exception:
        pass

    label_sync_code = _build_runtime_title_sync_script(base_title, resolved_state)

    try:
        if client is not None:
            if hasattr(client, 'title'):
                client.title = browser_title
            set_tab(title=browser_title, icon_url=favicon_url, client=client)
            if hasattr(client, 'run_javascript'):
                client.run_javascript(label_sync_code)
        elif broadcast:
            clients_dict = getattr(app, 'clients', {})
            try:
                clients = list(getattr(clients_dict, 'values', lambda: [])())
            except Exception:
                clients = []
            for connected_client in clients:
                sync_runtime_gui_title(
                    title=base_title,
                    client=connected_client,
                    measurement_state=resolved_state,
                )
    except Exception:
        logger.debug('Failed to sync GUI title to client(s)', exc_info=True)

    return base_title


def _schedule_runtime_gui_title_sync(
    *,
    title: str | None = None,
    client: Any = None,
    broadcast: bool = False,
    measurement_state: dict[str, Any] | None = None,
) -> Any:
    async def _sync() -> None:
        sync_runtime_gui_title(
            title=title,
            client=client,
            broadcast=broadcast,
            measurement_state=measurement_state,
        )

    return schedule_bg(_sync(), name='sync_runtime_gui_title')


def sync_runtime_measurement_state(
    *,
    state: dict[str, Any] | None = None,
    is_active: Any = None,
    session_id: Any = None,
    session_start_time: Any = None,
    recent_motion_detected: Any = _RUNTIME_STATE_UNSET,
    client: Any = None,
    broadcast: bool = False,
    schedule_gui_sync: bool = False,
) -> dict[str, Any]:
    normalized = _set_runtime_measurement_state(
        state=state,
        is_active=is_active,
        session_id=session_id,
        session_start_time=session_start_time,
        recent_motion_detected=recent_motion_detected,
    )
    if schedule_gui_sync:
        _schedule_runtime_gui_title_sync(
            client=client,
            broadcast=broadcast,
            measurement_state=normalized,
        )
    else:
        sync_runtime_gui_title(
            client=client,
            broadcast=broadcast,
            measurement_state=normalized,
        )
    return normalized


def register_client_runtime_title_sync(
    *,
    measurement_controller: Any | None,
    client: Any = None,
) -> None:
    if client is None:
        return

    cleanup_attr_name = 'cvd_runtime_title_listener_cleanup'
    disconnect_attr_name = 'cvd_runtime_title_disconnect_handler'
    previous_cleanup = getattr(client, cleanup_attr_name, None)
    if callable(previous_cleanup):
        try:
            previous_cleanup()
        except Exception:
            logger.exception('Failed to run previous runtime title listener cleanup')

    if measurement_controller is None or not hasattr(measurement_controller, 'register_session_state_callback'):
        sync_runtime_measurement_state(
            is_active=False,
            session_id=None,
            session_start_time=None,
            recent_motion_detected=None,
            client=client,
        )
        return

    def _session_listener(payload: dict[str, Any]) -> None:
        sync_runtime_measurement_state(
            state=payload,
            recent_motion_detected=None,
            client=client,
            schedule_gui_sync=True,
        )

    def _motion_listener(result: Any) -> None:
        if isinstance(result, dict):
            raw_motion = result.get('motion_detected')
        else:
            raw_motion = getattr(result, 'motion_detected', result if isinstance(result, bool) else None)
        sync_runtime_measurement_state(
            recent_motion_detected=None if raw_motion is None else bool(raw_motion),
            client=client,
            schedule_gui_sync=True,
        )

    def _cleanup() -> None:
        try:
            measurement_controller.unregister_session_state_callback(_session_listener)
        except Exception:
            logger.exception('Failed to unregister runtime title listener')
        unregister_motion_callback = getattr(measurement_controller, 'unregister_motion_callback', None)
        if callable(unregister_motion_callback):
            try:
                unregister_motion_callback(_motion_listener)
            except Exception:
                logger.exception('Failed to unregister runtime motion listener')

    measurement_controller.register_session_state_callback(_session_listener)
    register_motion_callback = getattr(measurement_controller, 'register_motion_callback', None)
    if callable(register_motion_callback):
        register_motion_callback(_motion_listener)
    setattr(client, cleanup_attr_name, _cleanup)

    disconnect_handler = getattr(client, disconnect_attr_name, None)
    if not callable(disconnect_handler):
        def _cleanup_on_disconnect() -> None:
            cleanup_listener = getattr(client, cleanup_attr_name, None)
            if callable(cleanup_listener):
                try:
                    cleanup_listener()
                except Exception:
                    logger.exception('Failed to run runtime title listener cleanup on disconnect')
            for attr_name in (cleanup_attr_name, disconnect_attr_name):
                try:
                    if hasattr(client, attr_name):
                        delattr(client, attr_name)
                except Exception:
                    pass

        register_client_disconnect_handler(
            client,
            _cleanup_on_disconnect,
            logger=logger,
            attr_name=disconnect_attr_name,
        )

    try:
        sync_runtime_measurement_state(state=measurement_controller.get_session_status(), client=client)
    except Exception:
        logger.debug('Failed to initialize runtime title sync from measurement controller', exc_info=True)
        sync_runtime_gui_title(client=client)


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


def create_gui(config_path: str = "config/config.yaml") -> gui_instances.InitializationReport:
    """Initialisierung vor ui.run(): Konfiguration laden und App initialisieren.

    Die eigentlichen Seiten sind per @ui.page deklariert.
    """
    report = gui_init.init_application(config_path)
    if report.fatal:
        logger.error("Failed to initialize GUI: %s", report.summary())
        raise RuntimeError(report.summary())

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

    if report.degraded:
        logger.warning('GUI initialized in degraded mode: %s', report.summary())
    else:
        logger.info('GUI initialized; config loaded')
    return report


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
                status.text = f"Lokaler Commit {stat.get('local')} -> Remote {stat.get('remote') or ''} (behind={stat.get('behind', 0)})"
                logger.info(
                    "Update status: behind=%s, local=%s, remote=%s",
                    stat.get('behind', 0),
                    stat.get('local'),
                    stat.get('remote'),
                )

                # 2) Update im Hintergrund durchführen
                logger.info('Starting update...')
                ok = await asyncio.to_thread(perform_update, q.put)

                if ok:
                    logger.info('Update completed successfully; restarting...')
                    ui.notify('Update abgeschlossen. Neustart...', type='positive', position='bottom-right')
                    # 3) Sauberes Cleanup + Self-Restart (cleanup in thread pool to avoid blocking)
                    await asyncio.to_thread(gui_cleanup.cleanup_application)
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
