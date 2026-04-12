from __future__ import annotations

from datetime import datetime
import threading
from typing import TYPE_CHECKING, Optional, Any, Callable, Literal

from nicegui import ui

from src.cam.camera import Camera
from src.config import get_logger
from src.gui.default_elements.motion_sensitivity_card import create_motion_sensitivity_controls
from src.gui.motion_runtime import (
    _ensure_motion_update_route_registered,
    register_combined_motion_listener,
    resolve_combined_motion_state,
)
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row

if TYPE_CHECKING:
    from src.measurement import MeasurementController

logger = get_logger('gui.motion_status')


def create_motion_status_element(
    camera: Camera | None,
    measurement_controller: Optional['MeasurementController'] = None,
    *,
    header_action: Literal['settings', 'refresh'] = 'settings',
    anchor_id: Optional[str] = None,
) -> None:
    del measurement_controller  # kept for compatibility with existing call sites

    _ensure_motion_update_route_registered()

    def _render_header_button(*, on_refresh: Optional[Callable[[], None]] = None, enabled: bool = True) -> None:
        if header_action == 'refresh':
            refresh_button = ui.button(icon='refresh', on_click=on_refresh or (lambda: None))
            refresh_button.props('flat round dense').tooltip('Refresh motion status')
            if not enabled:
                refresh_button.disable()
            return

        ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')) \
            .props('flat round dense').tooltip('Open camera & motion settings')

    if camera is None:
        logger.warning("Camera not available - motion detection disabled")
        with ui.card().classes('w-full shadow-2 q-pa-sm'):
            with ui.row().classes('items-center justify-between w-full'):
                create_heading_row(
                    'Motion Detection Status',
                    icon=SECTION_ICONS['motion'],
                    anchor_id=anchor_id,
                    title_classes='text-h6 font-semibold mb-1',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-xl shrink-0',
                )
                _render_header_button(enabled=False)
            ui.label('Camera not available - motion detection disabled').classes('text-warning')
        return

    logger.info("Creating motion status element")
    motion_detected = False
    last_changed: Optional[datetime] = None
    state_lock = threading.RLock()
    icon: Any = None
    status_label: Any = None
    timestamp_label: Any = None

    def refresh_view() -> None:
        with state_lock:
            if icon is None or status_label is None or timestamp_label is None:
                return
            if motion_detected:
                icon.props('name=check_circle color=green')
                status_label.text = 'Motion detected'
            else:
                icon.props('name=highlight_off color=red')
                status_label.text = 'No motion detected'
            timestamp_label.text = (
                f'Last changed: {last_changed.strftime("%Y-%m-%d %H:%M:%S")}'
                if last_changed is not None
                else 'Last changed: -'
            )
            icon.update()
            status_label.update()
            timestamp_label.update()

    def _apply_motion_state(
        new_motion_detected: bool,
        changed_at: Optional[datetime],
        *,
        allow_change_time_backfill: bool = False,
    ) -> bool:
        nonlocal motion_detected, last_changed
        should_refresh = False
        with state_lock:
            if new_motion_detected != motion_detected:
                motion_detected = new_motion_detected
                if changed_at is not None:
                    last_changed = changed_at
                should_refresh = True
            elif allow_change_time_backfill and last_changed is None and changed_at is not None:
                last_changed = changed_at
                should_refresh = True
        if should_refresh:
            refresh_view()
        return should_refresh

    def _sync_current_motion_state() -> None:
        combined_motion, combined_changed_at = resolve_combined_motion_state(camera)
        updated = _apply_motion_state(
            combined_motion,
            combined_changed_at,
            allow_change_time_backfill=combined_changed_at is not None,
        )
        if not updated:
            refresh_view()

    def _handle_motion_state_change(new_motion: bool, changed_at: Optional[datetime]) -> None:
        _apply_motion_state(
            new_motion,
            changed_at,
            allow_change_time_backfill=changed_at is not None,
        )

    with ui.card().classes('w-full shadow-2 q-pa-sm'):
        with ui.row().classes('items-center justify-between w-full'):
            create_heading_row(
                'Motion Detection Status',
                icon=SECTION_ICONS['motion'],
                anchor_id=anchor_id,
                title_classes='text-h6 font-semibold mb-1',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-xl shrink-0',
            )
            _render_header_button(on_refresh=_sync_current_motion_state)
        with ui.column().classes('w-full items-start q-gutter-y-xs'):
            with ui.row().classes('items-center q-gutter-x-md') \
                        .style('white-space: nowrap'):
                icon = ui.icon('highlight_off', color='red', size='2rem')
                status_label = ui.label('No motion detected') \
                                .classes('text-h6')
            timestamp_label = ui.label('').classes('text-caption') \
                                .style('white-space: nowrap')
            with ui.expansion('Sensitivity', value=False, icon='tune').props('expand-separator').classes('w-full mt-2'):
                with ui.column().classes('w-full gap-2 pt-2'):
                    create_motion_sensitivity_controls(
                        camera=camera,
                        show_header=False,
                        show_description=True,
                    )

    try:
        client = ui.context.client
    except Exception:
        client = None

    register_combined_motion_listener(
        camera,
        client=client,
        callback=_handle_motion_state_change,
        cleanup_attr_name='cvd_motion_status_listener_cleanup',
        disconnect_attr_name='cvd_motion_status_disconnect_handler',
        logger=logger,
    )
    _sync_current_motion_state()
