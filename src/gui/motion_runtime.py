from __future__ import annotations

from datetime import datetime
import hmac
import logging
import os
import threading
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app

from src.cam.camera import Camera
from src.gui.util import register_client_disconnect_handler

MotionStateListener = Callable[[bool, Optional[datetime]], None]
ApiMotionListener = Callable[[bool, datetime], None]

_api_state_lock = threading.RLock()
_api_motion_detected = False
_api_last_changed: Optional[datetime] = None
_api_motion_listeners: list[ApiMotionListener] = []
_api_route_registered = False
_api_route_registration_lock = threading.Lock()


def _register_api_motion_listener(listener: ApiMotionListener) -> None:
    with _api_state_lock:
        if listener not in _api_motion_listeners:
            _api_motion_listeners.append(listener)


def _unregister_api_motion_listener(listener: ApiMotionListener) -> None:
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
    listeners_snapshot: list[ApiMotionListener] = []

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
            logging.getLogger('gui.motion_runtime').exception('Failed to notify API motion listener')

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
        if len(parts) == 2 and parts[0].lower() == 'bearer' and hmac.compare_digest(parts[1], expected_bearer):
            return True, 'ok'

    if expected_api_key and api_key and hmac.compare_digest(api_key, expected_api_key):
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

    with _api_route_registration_lock:
        if _api_route_registered:
            return

        @app.get('/api/motion/update')
        async def update_motion_status(request: Request) -> JSONResponse:
            logger = logging.getLogger('gui.motion_runtime')
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
                                'message': 'Invalid or missing credentials',
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


def _timestamp_to_datetime(raw_timestamp: Any) -> Optional[datetime]:
    if isinstance(raw_timestamp, (int, float)):
        return datetime.fromtimestamp(float(raw_timestamp))
    return None


def resolve_combined_motion_state(camera: Camera | None) -> tuple[bool, Optional[datetime]]:
    camera_result = camera.get_last_motion_result() if camera is not None else None
    api_motion, api_last_changed = _get_api_motion_state()

    camera_motion = bool(getattr(camera_result, 'motion_detected', False)) if camera_result is not None else False
    camera_changed_at = _timestamp_to_datetime(getattr(camera_result, 'timestamp', None))

    if camera_changed_at is not None and (api_last_changed is None or camera_changed_at >= api_last_changed):
        return camera_motion, camera_changed_at
    if api_last_changed is not None:
        return api_motion, api_last_changed
    return camera_motion, camera_changed_at


def register_combined_motion_listener(
    camera: Camera | None,
    *,
    client: Any | None,
    callback: MotionStateListener,
    cleanup_attr_name: str,
    disconnect_attr_name: str,
    logger: logging.Logger,
) -> bool:
    if camera is None:
        logger.debug('Skipping combined motion listener registration without camera')
        return False
    if client is None:
        logger.debug('Skipping combined motion listener registration without client context')
        return False

    previous_cleanup = getattr(client, cleanup_attr_name, None)
    if callable(previous_cleanup):
        try:
            previous_cleanup()
        except Exception:
            logger.exception('Failed to run previous combined motion listener cleanup')

    def _camera_motion_callback(_: Any, result: Any) -> None:
        callback(
            bool(getattr(result, 'motion_detected', False)),
            _timestamp_to_datetime(getattr(result, 'timestamp', None)) or datetime.now(),
        )

    def _api_motion_listener(new_motion: bool, changed_at: datetime) -> None:
        callback(new_motion, changed_at)

    try:
        camera.enable_motion_detection(_camera_motion_callback)
        _register_api_motion_listener(_api_motion_listener)
    except Exception:
        logger.exception('Failed to register combined motion listener')
        try:
            camera.disable_motion_detection(_camera_motion_callback)
        except Exception:
            logger.debug('Failed to roll back combined motion listener registration', exc_info=True)
        try:
            _unregister_api_motion_listener(_api_motion_listener)
        except Exception:
            logger.debug('Failed to roll back API motion listener registration', exc_info=True)
        return False

    def _cleanup() -> None:
        try:
            camera.disable_motion_detection(_camera_motion_callback)
        except Exception:
            logger.exception('Failed to unregister combined camera motion listener')
        try:
            _unregister_api_motion_listener(_api_motion_listener)
        except Exception:
            logger.exception('Failed to unregister combined API motion listener')

    setattr(client, cleanup_attr_name, _cleanup)

    disconnect_handler = getattr(client, disconnect_attr_name, None)
    if not callable(disconnect_handler):
        def _cleanup_on_disconnect() -> None:
            cleanup_listener = getattr(client, cleanup_attr_name, None)
            if callable(cleanup_listener):
                try:
                    cleanup_listener()
                except Exception:
                    logger.exception('Failed to run combined motion listener cleanup on disconnect')
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

    return True
