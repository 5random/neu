from __future__ import annotations

from datetime import datetime
import os
import threading
from typing import TYPE_CHECKING, Optional, Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app, ui

from src.cam.camera import Camera
from src.config import get_logger
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row

if TYPE_CHECKING:
    from src.measurement import MeasurementController

logger = get_logger('gui.motion_status')

_api_state_lock = threading.RLock()
_api_motion_detected = False
_api_last_changed: Optional[datetime] = None
_api_motion_listeners: list[Callable[[bool, datetime], None]] = []
_api_route_registered = False


def _register_api_motion_listener(listener: Callable[[bool, datetime], None]) -> None:
    with _api_state_lock:
        if listener not in _api_motion_listeners:
            _api_motion_listeners.append(listener)


def _unregister_api_motion_listener(listener: Callable[[bool, datetime], None]) -> None:
    with _api_state_lock:
        try:
            _api_motion_listeners.remove(listener)
        except ValueError:
            return


def _get_api_motion_state() -> tuple[bool, Optional[datetime]]:
    with _api_state_lock:
        return _api_motion_detected, _api_last_changed


def _apply_api_motion_update(new_motion: bool, changed_at: Optional[datetime] = None) -> tuple[bool, datetime]:
    global _api_motion_detected, _api_last_changed

    change_time = changed_at or datetime.now()
    listeners_snapshot: list[Callable[[bool, datetime], None]] = []

    with _api_state_lock:
        updated = new_motion != _api_motion_detected
        if updated or _api_last_changed is None:
            _api_motion_detected = new_motion
            _api_last_changed = change_time
        listeners_snapshot = list(_api_motion_listeners)
        current_motion = _api_motion_detected
        current_last_changed = _api_last_changed or change_time

    for listener in listeners_snapshot:
        try:
            listener(current_motion, current_last_changed)
        except Exception:
            logger.exception('Failed to notify API motion listener')

    return updated, current_last_changed


def _is_motion_update_authorized(request: Request) -> tuple[bool, str]:
    expected_bearer = os.getenv('CVD_API_TOKEN') or os.getenv('API_TOKEN')
    expected_api_key = os.getenv('CVD_API_KEY') or os.getenv('API_KEY')

    authz = request.headers.get('authorization') or request.headers.get('Authorization')
    api_key = request.headers.get('x-api-key') or request.headers.get('X-API-Key')

    if not expected_bearer and not expected_api_key:
        return False, 'server_not_configured'

    if expected_bearer and authz:
        parts = authz.split()
        if len(parts) == 2 and parts[0].lower() == 'bearer' and parts[1] == expected_bearer:
            return True, 'ok'

    if expected_api_key and api_key and api_key == expected_api_key:
        return True, 'ok'

    return False, 'invalid_credentials'


def _parse_motion_query(request: Request) -> tuple[bool, bool]:
    raw = request.query_params.get('motion')
    if raw is None:
        return False, False

    val = raw.strip().lower()
    truthy = {'1', 'true', 't', 'yes', 'y', 'on'}
    falsy = {'0', 'false', 'f', 'no', 'n', 'off'}
    if val in truthy:
        return True, True
    if val in falsy:
        return False, True
    raise ValueError(f'invalid motion value: {raw}')


def _ensure_motion_update_route_registered() -> None:
    global _api_route_registered

    if _api_route_registered:
        return

    @app.get('/api/motion/update')
    async def update_motion_status(request: Request) -> JSONResponse:
        try:
            ok, reason = _is_motion_update_authorized(request)
            client_ip = getattr(request.client, 'host', 'unknown')
            if not ok:
                if reason == 'server_not_configured':
                    logger.error(
                        'Unauthorized attempt but server has no API secret configured; client=%s',
                        client_ip,
                    )
                    return JSONResponse(
                        status_code=401,
                        content={
                            'status': 'error',
                            'error': 'unauthorized',
                            'message': 'API secret not configured on server. Set CVD_API_TOKEN or CVD_API_KEY.',
                        },
                    )
                logger.warning('Unauthorized request to motion update; client=%s', client_ip)
                return JSONResponse(
                    status_code=401,
                    content={
                        'status': 'error',
                        'error': 'unauthorized',
                        'message': 'Invalid or missing credentials',
                    },
                )

            new_motion, present = _parse_motion_query(request)
            if not present:
                return JSONResponse(
                    status_code=400,
                    content={
                        'status': 'error',
                        'error': 'bad_request',
                        'message': "Missing required query parameter 'motion'",
                    },
                )

            updated, last_changed = _apply_api_motion_update(new_motion)
            return JSONResponse(
                status_code=200,
                content={
                    'status': 'success',
                    'updated': updated,
                    'motion': new_motion if updated else _get_api_motion_state()[0],
                    'last_changed': last_changed.isoformat(),
                },
            )
        except ValueError as exc:
            logger.warning('Bad request in motion update: %s', exc)
            return JSONResponse(
                status_code=400,
                content={'status': 'error', 'error': 'bad_request', 'message': str(exc)},
            )
        except Exception:
            logger.exception('Unexpected error in motion update handler')
            return JSONResponse(
                status_code=500,
                content={'status': 'error', 'error': 'server_error', 'message': 'Internal server error'},
            )

    _api_route_registered = True


def create_motion_status_element(camera: Camera | None, measurement_controller: Optional['MeasurementController'] = None) -> None:
    del measurement_controller  # kept for compatibility with existing call sites

    _ensure_motion_update_route_registered()

    if camera is None:
        logger.warning("Camera not available - motion detection disabled")
        with ui.card().classes('w-full shadow-2 q-pa-sm'):
            with ui.row().classes('items-center justify-between w-full'):
                create_heading_row(
                    'Motion Detection Status',
                    icon=SECTION_ICONS['motion'],
                    title_classes='text-h6 font-semibold mb-1',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-xl shrink-0',
                )
                ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')) \
                    .props('flat round dense').tooltip('Open camera & motion settings')
            ui.label('Camera not available - motion detection disabled').classes('text-warning')
        return

    logger.info("Creating motion status element")
    motion_detected = False
    last_changed = datetime.now()
    state_lock = threading.RLock()

    with ui.card().classes('w-full shadow-2 q-pa-sm'):
        with ui.row().classes('items-center justify-between w-full'):
            create_heading_row(
                'Motion Detection Status',
                icon=SECTION_ICONS['motion'],
                title_classes='text-h6 font-semibold mb-1',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-xl shrink-0',
            )
            ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')) \
                .props('flat round dense').tooltip('Open camera & motion settings')
        with ui.column().classes('w-full items-start q-gutter-y-xs'):
            with ui.row().classes('items-center q-gutter-x-md') \
                        .style('white-space: nowrap'):
                icon = ui.icon('highlight_off', color='red', size='2rem')
                status_label = ui.label('No motion detected') \
                                .classes('text-h6')
            timestamp_label = ui.label('').classes('text-caption') \
                                .style('white-space: nowrap')

    def refresh_view() -> None:
        with state_lock:
            if motion_detected:
                icon.props('name=check_circle color=green')
                status_label.text = 'Motion detected'
            else:
                icon.props('name=highlight_off color=red')
                status_label.text = 'No motion detected'
            timestamp_label.text = f'Last changed: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'

    def _apply_motion_state(new_motion_detected: bool, changed_at: datetime) -> None:
        nonlocal motion_detected, last_changed
        with state_lock:
            if new_motion_detected != motion_detected:
                motion_detected = new_motion_detected
                last_changed = changed_at
                refresh_view()

    def _apply_motion_result(result: Any) -> None:
        timestamp = getattr(result, 'timestamp', None)
        changed_at = (
            datetime.fromtimestamp(float(timestamp))
            if isinstance(timestamp, (int, float))
            else datetime.now()
        )
        _apply_motion_state(bool(getattr(result, 'motion_detected', False)), changed_at)

    def _camera_motion_callback(_: Any, result: Any) -> None:
        _apply_motion_result(result)

    def _api_motion_listener(new_motion: bool, changed_at: datetime) -> None:
        _apply_motion_state(new_motion, changed_at)

    def _unregister_motion_listeners() -> None:
        try:
            camera.disable_motion_detection(_camera_motion_callback)
        except Exception:
            logger.exception('Failed to unregister motion status camera listener')
        try:
            _unregister_api_motion_listener(_api_motion_listener)
        except Exception:
            logger.exception('Failed to unregister motion status API listener')

    try:
        client = ui.context.client
    except Exception:
        client = None

    if client is not None:
        previous_cleanup = getattr(client, 'cvd_motion_status_listener_cleanup', None)
        if callable(previous_cleanup):
            previous_cleanup()

    camera.enable_motion_detection(_camera_motion_callback)
    _register_api_motion_listener(_api_motion_listener)
    logger.info('Motion status listeners registered')

    if client is not None:
        setattr(client, 'cvd_motion_status_listener_cleanup', _unregister_motion_listeners)

        def _cleanup_on_disconnect() -> None:
            _unregister_motion_listeners()
            try:
                if getattr(client, 'cvd_motion_status_listener_cleanup', None) is _unregister_motion_listeners:
                    delattr(client, 'cvd_motion_status_listener_cleanup')
            except Exception:
                pass

        client.on_disconnect(_cleanup_on_disconnect)

    camera_result = camera.get_last_motion_result()
    api_motion, api_last_changed = _get_api_motion_state()
    camera_changed_at: Optional[datetime] = None

    if camera_result is not None:
        timestamp = getattr(camera_result, 'timestamp', None)
        if isinstance(timestamp, (int, float)):
            camera_changed_at = datetime.fromtimestamp(float(timestamp))

    if camera_result is not None and camera_changed_at is not None and (
        api_last_changed is None or camera_changed_at >= api_last_changed
    ):
        _apply_motion_result(camera_result)
    elif api_last_changed is not None:
        _apply_motion_state(api_motion, api_last_changed)

    refresh_view()
