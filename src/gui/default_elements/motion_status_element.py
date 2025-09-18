from nicegui import ui
from fastapi import Request
from fastapi.responses import JSONResponse
import os
from datetime import datetime

from src.measurement import MeasurementController
from src.cam.camera import Camera
from src.config import get_logger

logger = get_logger('gui.motion_status')

def create_motion_status_element(camera: Camera | None, measurement_controller: MeasurementController | None = None):
    if camera is None:
        logger.warning("Camera not available - motion detection disabled")
        # Fallback-UI ohne Kamera-Integration
        with ui.card().classes('w-full h-full shadow-2 q-pa-md').style('align-self:stretch;'):
            ui.label('Motion Detection Status').classes('text-h6 font-semibold mb-2')
            ui.label('Camera not available - motion detection disabled').classes('text-warning')
        return
    
    # ---------- interne Statusvariablen ----------
    logger.info("Creating motion status element")
    motion_detected: bool = False           # Start: keine Bewegung
    last_changed: datetime = datetime.now() # Zeitstempel der letzten Änderung

    # ---------- UI ----------
    with ui.card().classes('w-full h-full shadow-2 q-pa-md').style('align-self:stretch;'):
        ui.label('Motion Detection Status').classes('text-h6 font-semibold mb-2')
        # Karte mit fester, breiterem Layout ------------------------------
        with ui.column().classes('w-full items-start q-gutter-y-md'):
            with ui.row().classes('items-center q-gutter-x-md')\
                        .style('white-space: nowrap'):
                icon = ui.icon('highlight_off', color='red', size='2rem')
                status_label = ui.label('No motion detected')\
                                .classes('text-h6')
            timestamp_label = ui.label('').classes('text-body2')\
                                .style('white-space: nowrap')

    def refresh_view() -> None:
        """Icon, Text und Zeitstempel aktualisieren."""
        if motion_detected:
            icon.props('name=check_circle color=green')
            status_label.text = 'Motion detected'
        else:
            icon.props('name=highlight_off color=red')
            status_label.text = 'No motion detected'
        timestamp_label.text = f'Last changed: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'

    def _motion_callback(frame, result):
        nonlocal motion_detected, last_changed

        if result.motion_detected != motion_detected:
            motion_detected = result.motion_detected
            last_changed = datetime.fromtimestamp(result.timestamp)
            refresh_view()
        if measurement_controller is not None:
            measurement_controller.on_motion_detected(result)

    camera.enable_motion_detection(_motion_callback)
    logger.info("Motion detection callback registered")
    # ---------- REST endpoint for analysis script -------------------
    @ui.page('/api/motion/update')
    async def update(request: Request):
        nonlocal motion_detected, last_changed

        def _is_authorized(req: Request) -> tuple[bool, str]:
            """Validate Authorization header (Bearer) or X-API-Key.

            Returns (ok, reason). Uses environment variables:
            - CVD_API_TOKEN or API_TOKEN for Bearer token
            - CVD_API_KEY or API_KEY for X-API-Key
            """
            expected_bearer = os.getenv('CVD_API_TOKEN') or os.getenv('API_TOKEN')
            expected_api_key = os.getenv('CVD_API_KEY') or os.getenv('API_KEY')

            authz = req.headers.get('authorization') or req.headers.get('Authorization')
            api_key = req.headers.get('x-api-key') or req.headers.get('X-API-Key')

            # Require at least one secret to be configured
            if not expected_bearer and not expected_api_key:
                return False, 'server_not_configured'

            if expected_bearer and authz:
                parts = authz.split()
                if len(parts) == 2 and parts[0].lower() == 'bearer' and parts[1] == expected_bearer:
                    return True, 'ok'

            if expected_api_key and api_key and api_key == expected_api_key:
                return True, 'ok'

            return False, 'invalid_credentials'

        def _parse_motion_param(req: Request) -> tuple[bool, bool]:
            """Parse and validate the 'motion' query parameter.

            Returns (parsed_value, present). Raises ValueError if invalid when present.
            Accepted values (case-insensitive): 1, 0, true, false, yes, no, on, off
            """
            raw = req.query_params.get('motion')
            if raw is None:
                return False, False  # not provided
            val = raw.strip().lower()
            truthy = {'1', 'true', 't', 'yes', 'y', 'on'}
            falsy = {'0', 'false', 'f', 'no', 'n', 'off'}
            if val in truthy:
                return True, True
            if val in falsy:
                return False, True
            raise ValueError(f"invalid motion value: {raw}")

        try:
            ok, reason = _is_authorized(request)
            client_ip = getattr(request.client, 'host', 'unknown')
            if not ok:
                if reason == 'server_not_configured':
                    logger.error("Unauthorized attempt but server has no API secret configured; client=%s", client_ip)
                    return JSONResponse(
                        status_code=401,
                        content={
                            'status': 'error',
                            'error': 'unauthorized',
                            'message': 'API secret not configured on server. Set CVD_API_TOKEN or CVD_API_KEY.'
                        },
                    )
                logger.warning("Unauthorized request to motion update; client=%s", client_ip)
                return JSONResponse(
                    status_code=401,
                    content={'status': 'error', 'error': 'unauthorized', 'message': 'Invalid or missing credentials'},
                )

            new_motion, present = _parse_motion_param(request)
            if not present:
                return JSONResponse(
                    status_code=400,
                    content={'status': 'error', 'error': 'bad_request', 'message': "Missing required query parameter 'motion'"},
                )

            updated = False
            if new_motion != motion_detected:
                motion_detected = new_motion
                last_changed = datetime.now()
                refresh_view()
                updated = True

            return JSONResponse(
                status_code=200,
                content={
                    'status': 'success',
                    'updated': updated,
                    'motion': motion_detected,
                    'last_changed': last_changed.isoformat(),
                },
            )
        except ValueError as ve:
            logger.warning("Bad request in motion update: %s", ve)
            return JSONResponse(
                status_code=400,
                content={'status': 'error', 'error': 'bad_request', 'message': str(ve)},
            )
        except Exception as e:
            logger.exception("Unexpected error in motion update handler")
            return JSONResponse(
                status_code=500,
                content={'status': 'error', 'error': 'server_error', 'message': 'Internal server error'},
            )
