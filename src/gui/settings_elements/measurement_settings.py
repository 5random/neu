from __future__ import annotations

from collections.abc import Iterable
from typing import Optional, Callable, Any

from nicegui import ui

from src.config import EmailConfig, get_global_config, save_global_config, get_logger
from src.measurement import MeasurementController
from src.gui import instances
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row
from src.gui.util import notify_user

logger = get_logger('gui.measurement')

EVENT_KEYS = ('on_start', 'on_end', 'on_stop')
EVENT_LABELS = {
    'on_start': 'Start',
    'on_end': 'End',
    'on_stop': 'Stop',
}
NOTIFICATION_SUMMARY_TOOLTIPS = {
    'groups_total': 'Total number of configured recipient groups available for routing.',
    'active_groups': 'Groups currently selected for the running measurement context.',
    'static_recipients': 'Recipients managed in E-Mail settings that always receive lifecycle emails.',
    'effective_total': 'Union of static recipients and recipients coming from active groups.',
    'start_count': 'Recipients who would receive a measurement start email right now.',
    'end_count': 'Recipients who would receive a measurement end email right now.',
    'stop_count': 'Recipients who would receive a measurement stop email right now.',
}
NOTIFICATION_TOOLTIPS = {
    'on_start': 'Enable lifecycle emails when a measurement starts.',
    'on_end': 'Enable lifecycle emails when a measurement finishes normally.',
    'on_stop': 'Enable lifecycle emails when a measurement is stopped early.',
    'active_groups': 'Choose which recipient groups are active for the current run. Static recipients are always added automatically.',
    'preview': 'Preview of the recipients that are currently reachable through static recipients and the selected groups.',
    'group_table': 'Per-group lifecycle permissions. These only affect recipients when the group is active.',
    'static_table': 'Per-static-recipient lifecycle permissions for recipients managed in E-Mail settings.',
    'apply': 'Save the current lifecycle routing settings to the configuration file.',
}


def _event_prefs(source: Optional[dict[str, bool]] = None, *, default: bool = True) -> dict[str, bool]:
    prefs = source or {}
    return {key: bool(prefs.get(key, default)) for key in EVENT_KEYS}


def _iterable_str_list(value: object) -> list[str]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [item for item in value if isinstance(item, str)]
    return []


def _get_known_recipients(email_cfg: Any) -> list[str]:
    getter = getattr(email_cfg, 'get_known_recipients', None)
    if callable(getter):
        return _iterable_str_list(getter())
    return _iterable_str_list(getattr(email_cfg, 'recipients', []))


def create_measurement_settings_card(
    measurement_controller: Optional[MeasurementController] = None,
    show_header: bool = True,
    **_: object,
) -> None:
    """Render measurement runtime settings and lifecycle notification routing."""
    cfg = get_global_config()
    if not cfg:
        ui.label('Configuration not available').classes('text-red')
        logger.error('Configuration not available - cannot create measurement settings')
        return

    measurement_cfg = cfg.measurement
    email_cfg = cfg.email

    state = {
        'alert_delay_seconds': int(getattr(measurement_cfg, 'alert_delay_seconds', 300)),
        'max_alerts_per_session': int(getattr(measurement_cfg, 'max_alerts_per_session', 5)),
        'alert_check_interval': float(getattr(measurement_cfg, 'alert_check_interval', 5.0)),
        'alert_cooldown_seconds': int(getattr(measurement_cfg, 'alert_cooldown_seconds', 300)),
        'alert_include_snapshot': bool(getattr(measurement_cfg, 'alert_include_snapshot', True)),
        'inactivity_timeout_minutes': int(getattr(measurement_cfg, 'inactivity_timeout_minutes', 60)),
        'motion_summary_interval_seconds': int(getattr(measurement_cfg, 'motion_summary_interval_seconds', 60)),
        'enable_motion_summary_logs': bool(getattr(measurement_cfg, 'enable_motion_summary_logs', True)),
    }

    recipient_pool = _get_known_recipients(email_cfg)
    active_groups = _iterable_str_list(getattr(email_cfg, 'active_groups', []))
    group_names = list((getattr(email_cfg, 'groups', {}) or {}).keys())

    notification_state: dict[str, Any] = {
        'recipient_pool': list(recipient_pool),
        'groups': dict(getattr(email_cfg, 'groups', {}) or {}),
        'notifications': _event_prefs(dict(getattr(email_cfg, 'notifications', {}) or {}), default=False),
        'active_groups': active_groups,
        'static_recipients': list(email_cfg.get_static_recipients_for_editor()),
        'group_prefs': {
            group_name: _event_prefs((getattr(email_cfg, 'group_prefs', {}) or {}).get(group_name), default=True)
            for group_name in group_names
        },
        'recipient_prefs': {
            recipient: _event_prefs((getattr(email_cfg, 'recipient_prefs', {}) or {}).get(recipient), default=True)
            for recipient in email_cfg.get_static_recipients_for_editor()
        },
    }

    if show_header:
        create_heading_row(
            'Measurement Settings',
            icon='straighten',
            title_classes='text-h6 font-semibold mb-2',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
    value_classes = 'w-[7rem] min-w-[7rem] max-w-[7rem] shrink-0'
    slider_classes = 'flex-1 min-w-[10rem] max-w-full'

    from src.gui.bindings import bind_number_slider

    counts_labels: dict[str, ui.label] = {}
    active_groups_select: Optional[ui.select] = None
    group_table: Optional[ui.table] = None
    static_table: Optional[ui.table] = None
    preview_table: Optional[ui.table] = None
    notification_apply_btn: Optional[ui.button] = None

    def _cast_to_int(value: Any, fallback: float) -> Optional[float]:
        try:
            return float(int(float(value)))
        except Exception:
            return fallback

    def _cast_to_float(value: Any, fallback: float) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return fallback

    def _get_control_value(ctrl: Any, default: Any) -> Any:
        try:
            value = getattr(ctrl, 'value', default)
        except Exception:
            return default
        return default if value is None else value

    def _build_numeric_card(
        key: str,
        *,
        title: str,
        tooltip: str,
        min_value: float,
        max_value: float,
        step: float,
        fmt: str,
        suffix: str,
        caster: Callable[[Any, float], Optional[float]],
        as_int: bool,
    ) -> tuple[Any, Any]:
        initial = state[key]
        fallback = float(initial)
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            ui.label(f'{title}:').classes('font-semibold mb-1 self-start')
            with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                number_ctrl = (
                    ui.number(value=initial, min=min_value, max=max_value, step=step, format=fmt)
                    .props(f'dense outlined hide-bottom-space suffix="{suffix}"')
                    .tooltip(tooltip)
                    .classes(value_classes)
                )
                slider_ctrl = (
                    ui.slider(min=min_value, max=max_value, step=step, value=initial)
                    .tooltip(tooltip)
                    .classes(slider_classes)
                )
        bind_number_slider(
            number_ctrl,
            slider_ctrl,
            min_value=min_value,
            max_value=max_value,
            caster=caster,
            fallback_value=fallback,
            as_int=as_int,
            on_change=lambda _: _on_change(),
        )
        return number_ctrl, slider_ctrl

    def _build_toggle_card(title: str, checkbox_label: str, value: bool, tooltip: str) -> Any:
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            ui.label(title).classes('font-semibold mb-1 self-start')
            checkbox = (
                ui.checkbox(checkbox_label, value=value)
                .tooltip(tooltip)
                .classes('self-start')
            )
        return checkbox

    def _normalize_notification_state() -> dict[str, Any]:
        return {
            'notifications': _event_prefs(notification_state['notifications'], default=False),
            'active_groups': sorted([group for group in notification_state['active_groups'] if group in notification_state['groups']]),
            'static_recipients': sorted([addr for addr in notification_state['static_recipients'] if addr in notification_state['recipient_pool']]),
            'group_prefs': {
                group: _event_prefs(notification_state['group_prefs'].get(group), default=True)
                for group in sorted(notification_state['groups'].keys())
            },
            'recipient_prefs': {
                addr: _event_prefs(notification_state['recipient_prefs'].get(addr), default=True)
                for addr in sorted(set(notification_state['static_recipients']))
            },
        }

    notification_saved_state = _normalize_notification_state()

    def _build_preview_email_cfg() -> EmailConfig:
        current_cfg = get_global_config()
        if not current_cfg:
            return email_cfg
        current_email_cfg = current_cfg.email
        return EmailConfig(
            website_url=current_email_cfg.website_url,
            recipients=list(notification_state['recipient_pool']),
            smtp_server=current_email_cfg.smtp_server,
            smtp_port=current_email_cfg.smtp_port,
            sender_email=current_email_cfg.sender_email,
            templates={name: dict(template_cfg) for name, template_cfg in current_email_cfg.templates.items()},
            groups={group: list(members) for group, members in current_email_cfg.groups.items()},
            active_groups=list(notification_state['active_groups']),
            static_recipients=list(notification_state['static_recipients']),
            explicit_targeting=True,
            notifications=dict(notification_state['notifications']),
            group_prefs={
                group: _event_prefs(notification_state['group_prefs'].get(group), default=True)
                for group in notification_state['groups'].keys()
            },
            recipient_prefs={
                addr: _event_prefs(notification_state['recipient_prefs'].get(addr), default=True)
                for addr in notification_state['static_recipients']
            },
        )

    def _refresh_notification_apply_state() -> None:
        try:
            if notification_apply_btn is None:
                return
            if _normalize_notification_state() == notification_saved_state:
                notification_apply_btn.disable()
            else:
                notification_apply_btn.enable()
        except Exception:
            if notification_apply_btn is not None:
                notification_apply_btn.enable()

    def _group_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for group_name in notification_state['groups'].keys():
            prefs = _event_prefs(notification_state['group_prefs'].get(group_name), default=True)
            rows.append(
                {
                    'name': group_name,
                    'members': len(notification_state['groups'].get(group_name, []) or []),
                    'active': 'yes' if group_name in notification_state['active_groups'] else '-',
                    'on_start': prefs['on_start'],
                    'on_end': prefs['on_end'],
                    'on_stop': prefs['on_stop'],
                }
            )
        return rows

    def _static_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for addr in notification_state['static_recipients']:
            prefs = _event_prefs(notification_state['recipient_prefs'].get(addr), default=True)
            rows.append(
                {
                    'address': addr,
                    'on_start': prefs['on_start'],
                    'on_end': prefs['on_end'],
                    'on_stop': prefs['on_stop'],
                }
            )
        return rows

    def _preview_rows() -> list[dict[str, Any]]:
        preview_cfg = _build_preview_email_cfg()
        effective_all = set(preview_cfg.get_target_recipients())
        event_targets = {key: set(preview_cfg.get_measurement_event_recipients(key)) for key in EVENT_KEYS}
        rows: list[dict[str, Any]] = []
        for addr in preview_cfg.get_known_recipients():
            if addr not in effective_all and not any(addr in recipients for recipients in event_targets.values()):
                continue
            active_sources = [group for group in notification_state['active_groups'] if addr in (notification_state['groups'].get(group, []) or [])]
            source_parts: list[str] = []
            if addr in notification_state['static_recipients']:
                source_parts.append('static')
            source_parts.extend(active_sources)
            rows.append(
                {
                    'address': addr,
                    'sources': ', '.join(source_parts) if source_parts else '-',
                    'on_start': 'yes' if addr in event_targets['on_start'] else '-',
                    'on_end': 'yes' if addr in event_targets['on_end'] else '-',
                    'on_stop': 'yes' if addr in event_targets['on_stop'] else '-',
                }
            )
        return rows

    def _refresh_notification_preview() -> None:
        preview_cfg = _build_preview_email_cfg()
        counts = {
            'groups_total': len(notification_state['groups']),
            'active_groups': len(notification_state['active_groups']),
            'static_recipients': len(notification_state['static_recipients']),
            'effective_total': len(preview_cfg.get_target_recipients()),
            'start_count': len(preview_cfg.get_measurement_event_recipients('on_start')),
            'end_count': len(preview_cfg.get_measurement_event_recipients('on_end')),
            'stop_count': len(preview_cfg.get_measurement_event_recipients('on_stop')),
        }
        for key, value in counts.items():
            label = counts_labels.get(key)
            if label is not None:
                label.text = str(value)
                label.update()
        if group_table is not None:
            group_table.rows = _group_rows()
            group_table.update()
        if static_table is not None:
            static_table.rows = _static_rows()
            static_table.update()
        if preview_table is not None:
            preview_table.rows = _preview_rows()
            preview_table.update()
        _refresh_notification_apply_state()

    def _on_notification_selection_change(_: Any = None) -> None:
        if active_groups_select is not None:
            values = getattr(active_groups_select, 'value', []) or []
            notification_state['active_groups'] = [group for group in values if group in notification_state['groups']]
        _refresh_notification_preview()

    def _sync_notification_sources_from_config() -> None:
        nonlocal notification_saved_state
        current_cfg = get_global_config()
        if current_cfg is None:
            return

        current_email_cfg = current_cfg.email
        current_groups = {
            group: list(members)
            for group, members in (getattr(current_email_cfg, 'groups', {}) or {}).items()
        }
        current_recipient_pool = _get_known_recipients(current_email_cfg)
        current_active_groups = _iterable_str_list(getattr(current_email_cfg, 'active_groups', []))
        current_static = current_email_cfg.get_static_recipients_for_editor()

        is_dirty = _normalize_notification_state() != notification_saved_state
        notification_state['recipient_pool'] = list(current_recipient_pool)
        notification_state['groups'] = current_groups

        persisted_group_prefs = getattr(current_email_cfg, 'group_prefs', {}) or {}
        notification_state['group_prefs'] = {
            group: _event_prefs(
                notification_state['group_prefs'].get(group) if is_dirty else persisted_group_prefs.get(group),
                default=True,
            )
            for group in current_groups.keys()
        }

        notification_state['static_recipients'] = [addr for addr in current_static if addr in current_recipient_pool]
        persisted_recipient_prefs = getattr(current_email_cfg, 'recipient_prefs', {}) or {}
        notification_state['recipient_prefs'] = {
            addr: _event_prefs(
                notification_state['recipient_prefs'].get(addr) if is_dirty else persisted_recipient_prefs.get(addr),
                default=True,
            )
            for addr in notification_state['static_recipients']
        }

        if is_dirty:
            notification_state['active_groups'] = [
                group for group in notification_state['active_groups']
                if group in notification_state['groups']
            ]
        else:
            notification_state['notifications'] = _event_prefs(
                dict(getattr(current_email_cfg, 'notifications', {}) or {}),
                default=False,
            )
            notification_state['active_groups'] = [
                group for group in current_active_groups
                if group in notification_state['groups']
            ]
            notification_saved_state = _normalize_notification_state()

        if active_groups_select is not None:
            active_groups_select.options = list(notification_state['groups'].keys())
            active_groups_select.value = list(notification_state['active_groups'])
            active_groups_select.update()

        _refresh_notification_preview()

    def _toggle_group_pref(group_name: str, event_key: str, value: bool) -> None:
        notification_state['group_prefs'].setdefault(group_name, _event_prefs(default=True))
        notification_state['group_prefs'][group_name][event_key] = bool(value)
        _refresh_notification_preview()

    def _toggle_recipient_pref(addr: str, event_key: str, value: bool) -> None:
        notification_state['recipient_prefs'].setdefault(addr, _event_prefs(default=True))
        notification_state['recipient_prefs'][addr][event_key] = bool(value)
        _refresh_notification_preview()

    def _persist_notifications() -> None:
        nonlocal notification_saved_state
        current_cfg = get_global_config()
        if not current_cfg:
            notify_user('Configuration not available', kind='warning')
            return
        try:
            current_email_cfg = current_cfg.email
            current_email_cfg.notifications = dict(notification_state['notifications'])
            current_email_cfg.active_groups = list(notification_state['active_groups'])
            current_email_cfg.static_recipients = list(notification_state['static_recipients'])
            current_email_cfg.group_prefs = {
                group: _event_prefs(notification_state['group_prefs'].get(group), default=True)
                for group in notification_state['groups'].keys()
            }
            current_email_cfg.recipient_prefs = {
                addr: _event_prefs(notification_state['recipient_prefs'].get(addr), default=True)
                for addr in notification_state['static_recipients']
            }
            current_email_cfg.enable_explicit_targeting(materialize_legacy_targets=False)
            current_email_cfg.recipients = current_email_cfg.get_known_recipients()

            if save_global_config():
                email_system = instances.get_email_system()
                if email_system is not None:
                    email_system.refresh_config()
                notification_state['recipient_pool'] = current_email_cfg.get_known_recipients()
                notification_saved_state = _normalize_notification_state()
                _refresh_notification_preview()
                notify_user('Measurement notification routing saved', kind='positive')
            else:
                notify_user('Failed to save notification routing', kind='negative')
        except Exception as exc:
            logger.error('Failed to save notification routing: %s', exc, exc_info=True)
            notify_user(f'Error saving notification routing: {exc}', kind='negative')

    def _on_change(_: Any = None) -> None:
        try:
            current = {
                'alert_delay_seconds': int(_get_control_value(alert_delay_inp, state['alert_delay_seconds']) or 0),
                'max_alerts_per_session': int(_get_control_value(max_alerts_inp, state['max_alerts_per_session']) or 0),
                'alert_check_interval': float(_get_control_value(check_interval_inp, state['alert_check_interval']) or 0),
                'alert_cooldown_seconds': int(_get_control_value(cooldown_inp, state['alert_cooldown_seconds']) or 0),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, state['alert_include_snapshot'])),
                'inactivity_timeout_minutes': int(_get_control_value(inactivity_inp, state['inactivity_timeout_minutes']) or 0),
                'motion_summary_interval_seconds': int(_get_control_value(summary_interval_inp, state['motion_summary_interval_seconds']) or 0),
                'enable_motion_summary_logs': bool(_get_control_value(enable_summary_cb, state['enable_motion_summary_logs'])),
            }
            if current != state:
                apply_btn.enable()
            else:
                apply_btn.disable()
        except Exception:
            apply_btn.enable()

    def _persist() -> None:
        nonlocal state
        current_cfg = get_global_config()
        if not current_cfg:
            notify_user('Configuration not available', kind='warning')
            return
        try:
            violations = []
            new_state = {
                'alert_delay_seconds': max(30, int(_get_control_value(alert_delay_inp, 0) or 0)),
                'max_alerts_per_session': max(1, int(_get_control_value(max_alerts_inp, 1) or 1)),
                'alert_check_interval': max(0.5, float(_get_control_value(check_interval_inp, 0.5) or 0.5)),
                'alert_cooldown_seconds': max(0, int(_get_control_value(cooldown_inp, 0) or 0)),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, False)),
                'inactivity_timeout_minutes': max(0, int(_get_control_value(inactivity_inp, 0) or 0)),
                'motion_summary_interval_seconds': max(5, int(_get_control_value(summary_interval_inp, 5) or 5)),
                'enable_motion_summary_logs': bool(_get_control_value(enable_summary_cb, True)),
            }

            if int(_get_control_value(alert_delay_inp, 0) or 0) < 30:
                violations.append('Alert delay minimum is 30 seconds')

            if violations:
                notify_user('; '.join(violations), kind='warning')

            current_measurement_cfg = current_cfg.measurement
            current_measurement_cfg.alert_delay_seconds = int(new_state['alert_delay_seconds'])
            current_measurement_cfg.max_alerts_per_session = int(new_state['max_alerts_per_session'])
            current_measurement_cfg.alert_check_interval = float(new_state['alert_check_interval'])
            current_measurement_cfg.alert_cooldown_seconds = int(new_state['alert_cooldown_seconds'])
            current_measurement_cfg.alert_include_snapshot = bool(new_state['alert_include_snapshot'])
            current_measurement_cfg.inactivity_timeout_minutes = int(new_state['inactivity_timeout_minutes'])
            current_measurement_cfg.motion_summary_interval_seconds = int(new_state['motion_summary_interval_seconds'])
            current_measurement_cfg.enable_motion_summary_logs = bool(new_state['enable_motion_summary_logs'])

            if save_global_config():
                if measurement_controller is not None:
                    measurement_controller.update_config(current_measurement_cfg)
                email_system = instances.get_email_system()
                if email_system is not None:
                    email_system.refresh_config()
                state = new_state
                apply_btn.disable()
                notify_user('Measurement settings saved', kind='positive')
            else:
                notify_user('Failed to save settings', kind='negative')
        except Exception as exc:
            logger.error('Failed to save measurement settings: %s', exc, exc_info=True)
            notify_user(f'Error saving settings: {exc}', kind='negative')

    with ui.grid(columns=2).classes('w-full gap-3 mb-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        alert_delay_inp, alert_delay_slider = _build_numeric_card(
            'alert_delay_seconds',
            title='Alert Delay',
            tooltip='Seconds without motion before the first alert is sent',
            min_value=30,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )
        max_alerts_inp, max_alerts_slider = _build_numeric_card(
            'max_alerts_per_session',
            title='Max Alerts Per Session',
            tooltip='Upper bound on alerts within a single session (spam protection)',
            min_value=1,
            max_value=50,
            step=1,
            fmt='%.0f',
            suffix='',
            caster=_cast_to_int,
            as_int=True,
        )
        check_interval_inp, check_interval_slider = _build_numeric_card(
            'alert_check_interval',
            title='Check Interval',
            tooltip='How often the controller evaluates alert conditions',
            min_value=0.5,
            max_value=120.0,
            step=0.5,
            fmt='%.1f',
            suffix='s',
            caster=_cast_to_float,
            as_int=False,
        )
        cooldown_inp, cooldown_slider = _build_numeric_card(
            'alert_cooldown_seconds',
            title='Alert Cooldown',
            tooltip='Minimum time between two alerts',
            min_value=0,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )
        inactivity_inp, inactivity_slider = _build_numeric_card(
            'inactivity_timeout_minutes',
            title='Inactivity Timeout',
            tooltip='Stop session after prolonged inactivity (0 = disabled)',
            min_value=0,
            max_value=720,
            step=5,
            fmt='%.0f',
            suffix='min',
            caster=_cast_to_int,
            as_int=True,
        )
        summary_interval_inp, summary_interval_slider = _build_numeric_card(
            'motion_summary_interval_seconds',
            title='Summary Interval',
            tooltip='Period for motion summary logs (>= 5 seconds)',
            min_value=5,
            max_value=3600,
            step=5,
            fmt='%.0f',
            suffix='s',
            caster=_cast_to_int,
            as_int=True,
        )

    with ui.grid(columns=2).classes('w-full gap-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        include_snapshot_cb = _build_toggle_card(
            'Alert Snapshot',
            'Include snapshot in alert',
            bool(state['alert_include_snapshot']),
            'Attach a snapshot to alert emails when an alert is sent',
        )
        enable_summary_cb = _build_toggle_card(
            'Motion Summary Logs',
            'Enable motion summary logs',
            bool(state['enable_motion_summary_logs']),
            'Master switch for periodic motion summary logging',
        )

    with ui.row().classes('items-center q-gutter-sm q-mt-sm justify-end'):
        apply_btn = create_action_button(
            'apply',
            on_click=lambda _: _persist(),
            tooltip='Save the measurement timing and alert behavior to the configuration file.',
        )
        apply_btn.disable()

    for ctrl in [
        alert_delay_inp,
        max_alerts_inp,
        check_interval_inp,
        cooldown_inp,
        include_snapshot_cb,
        inactivity_inp,
        summary_interval_inp,
        enable_summary_cb,
    ]:
        ctrl.on('update:model-value', _on_change)
        if hasattr(ctrl, 'on') and ctrl is not include_snapshot_cb and ctrl is not enable_summary_cb:
            ctrl.on('blur', _on_change)

    ui.separator().classes('my-6')

    with ui.column().classes('w-full gap-4'):
        create_heading_row(
            'Measurement Email Notifications',
            icon='notifications_active',
            title_classes='text-h6',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
        ui.label(
            'Configure lifecycle emails, choose the active groups for the current run, and review static-recipient permissions managed in E-Mail settings.'
        ).classes('text-body2 text-grey-7')

        with ui.grid(columns=4).classes('w-full gap-3').style(
            'grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));'
        ):
            summary_cards = [
                ('Configured Groups', 'groups_total'),
                ('Active Groups', 'active_groups'),
                ('Static Recipients', 'static_recipients'),
                ('Effective Recipients', 'effective_total'),
                ('Start Targets', 'start_count'),
                ('End Targets', 'end_count'),
                ('Stop Targets', 'stop_count'),
            ]
            for title, key in summary_cards:
                with ui.card().classes('p-3 gap-1') as summary_card:
                    ui.label(title).classes('text-caption text-grey-7')
                    counts_labels[key] = ui.label('0').classes('text-h6 font-semibold')
                summary_card.tooltip(NOTIFICATION_SUMMARY_TOOLTIPS[key])

        with ui.card().classes('w-full p-4 gap-4'):
            create_heading_row(
                'Lifecycle Switches',
                icon='tune',
                title_classes='text-subtitle1 font-semibold',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-lg shrink-0',
            )
            with ui.row().classes('items-center gap-4 flex-wrap'):
                start_toggle = ui.checkbox(
                    'Send email on measurement start',
                    value=bool(notification_state['notifications']['on_start']),
                ).tooltip(NOTIFICATION_TOOLTIPS['on_start'])
                end_toggle = ui.checkbox(
                    'Send email on measurement end',
                    value=bool(notification_state['notifications']['on_end']),
                ).tooltip(NOTIFICATION_TOOLTIPS['on_end'])
                stop_toggle = ui.checkbox(
                    'Send email on measurement stop',
                    value=bool(notification_state['notifications']['on_stop']),
                ).tooltip(NOTIFICATION_TOOLTIPS['on_stop'])

            def _sync_global_notification_state() -> None:
                notification_state['notifications'] = {
                    'on_start': bool(start_toggle.value),
                    'on_end': bool(end_toggle.value),
                    'on_stop': bool(stop_toggle.value),
                }
                _refresh_notification_preview()

            for ctrl in (start_toggle, end_toggle, stop_toggle):
                ctrl.on('update:model-value', lambda _: _sync_global_notification_state())

        with ui.grid(columns=2).classes('w-full gap-4 items-start').style(
            'grid-template-columns:repeat(auto-fit, minmax(360px, 1fr));'
        ):
            with ui.card().classes('w-full p-4 gap-3'):
                create_heading_row(
                    'Active Groups For This Run',
                    icon='route',
                    title_classes='text-subtitle1 font-semibold',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-lg shrink-0',
                )
                active_groups_select = (
                    ui.select(
                        options=list(notification_state['groups'].keys()),
                        value=list(notification_state['active_groups']),
                        label='Active Groups',
                        multiple=True,
                    )
                    .classes('w-full')
                    .props('outlined use-chips')
                    .tooltip(NOTIFICATION_TOOLTIPS['active_groups'])
                )
                ui.label('Static recipients are managed in E-Mail settings and are always added automatically to lifecycle delivery.').classes(
                    'text-caption text-grey-7'
                )
                active_groups_select.on('update:model-value', _on_notification_selection_change)

            with ui.card().classes('w-full p-4 gap-3'):
                create_heading_row(
                    'Effective Preview',
                    icon='visibility',
                    title_classes='text-subtitle1 font-semibold',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-lg shrink-0',
                )
                preview_table = ui.table(
                    columns=[
                        {'name': 'address', 'label': 'Address', 'field': 'address', 'align': 'left'},
                        {'name': 'sources', 'label': 'Active Sources', 'field': 'sources', 'align': 'left'},
                        {'name': 'on_start', 'label': 'Start', 'field': 'on_start', 'align': 'center'},
                        {'name': 'on_end', 'label': 'End', 'field': 'on_end', 'align': 'center'},
                        {'name': 'on_stop', 'label': 'Stop', 'field': 'on_stop', 'align': 'center'},
                    ],
                    rows=[],
                    row_key='address',
                    pagination={'rowsPerPage': 8},
                ).classes('w-full').props('dense flat bordered')
                preview_table.tooltip(NOTIFICATION_TOOLTIPS['preview'])

        with ui.grid(columns=2).classes('w-full gap-4 items-start').style(
            'grid-template-columns:repeat(auto-fit, minmax(360px, 1fr));'
        ):
            with ui.card().classes('w-full p-4 gap-3'):
                create_heading_row(
                    'Group Event Preferences',
                    icon='groups',
                    title_classes='text-subtitle1 font-semibold',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-lg shrink-0',
                )
                group_table = ui.table(
                    columns=[
                        {'name': 'name', 'label': 'Group', 'field': 'name', 'align': 'left'},
                        {'name': 'members', 'label': 'Members', 'field': 'members', 'align': 'center'},
                        {'name': 'active', 'label': 'Active', 'field': 'active', 'align': 'center'},
                        {'name': 'on_start', 'label': 'Start', 'field': 'on_start', 'align': 'center'},
                        {'name': 'on_end', 'label': 'End', 'field': 'on_end', 'align': 'center'},
                        {'name': 'on_stop', 'label': 'Stop', 'field': 'on_stop', 'align': 'center'},
                    ],
                    rows=[],
                    row_key='name',
                    pagination={'rowsPerPage': 8},
                ).classes('w-full').props('dense flat bordered')
                group_table.tooltip(NOTIFICATION_TOOLTIPS['group_table'])
                for event_key in EVENT_KEYS:
                    group_table.add_slot(
                        f'body-cell-{event_key}',
                        rf'''
                        <q-td :props="props">
                            <q-checkbox v-model="props.row.{event_key}" dense
                                @update:model-value="() => $parent.$emit('toggle', props.row.name, '{event_key}', props.row.{event_key})" />
                        </q-td>
                        ''',
                    )
                group_table.on('toggle', lambda e: _toggle_group_pref(e.args[0], e.args[1], e.args[2]))

            with ui.card().classes('w-full p-4 gap-3'):
                create_heading_row(
                    'Static Recipient Preferences',
                    icon='alternate_email',
                    title_classes='text-subtitle1 font-semibold',
                    row_classes='items-center gap-2',
                    icon_classes='text-primary text-lg shrink-0',
                )
                ui.label('Manage which addresses are static in E-Mail settings. This table only controls which lifecycle events those static recipients receive.').classes(
                    'text-caption text-grey-7'
                )
                static_table = ui.table(
                    columns=[
                        {'name': 'address', 'label': 'Address', 'field': 'address', 'align': 'left'},
                        {'name': 'on_start', 'label': 'Start', 'field': 'on_start', 'align': 'center'},
                        {'name': 'on_end', 'label': 'End', 'field': 'on_end', 'align': 'center'},
                        {'name': 'on_stop', 'label': 'Stop', 'field': 'on_stop', 'align': 'center'},
                    ],
                    rows=[],
                    row_key='address',
                    pagination={'rowsPerPage': 8},
                ).classes('w-full').props('dense flat bordered')
                static_table.tooltip(NOTIFICATION_TOOLTIPS['static_table'])
                for event_key in EVENT_KEYS:
                    static_table.add_slot(
                        f'body-cell-{event_key}',
                        rf'''
                        <q-td :props="props">
                            <q-checkbox v-model="props.row.{event_key}" dense
                                @update:model-value="() => $parent.$emit('toggle', props.row.address, '{event_key}', props.row.{event_key})" />
                        </q-td>
                        ''',
                    )
                static_table.on('toggle', lambda e: _toggle_recipient_pref(e.args[0], e.args[1], e.args[2]))

        with ui.row().classes('items-center justify-end gap-2'):
            apply_button = create_action_button(
                'apply',
                label='Apply Notification Routing',
                on_click=_persist_notifications,
                tooltip=NOTIFICATION_TOOLTIPS['apply'],
            )
            apply_button.disable()
            notification_apply_btn = apply_button

    _on_change()
    _refresh_notification_preview()
    ui.timer(2.0, _sync_notification_sources_from_config)
