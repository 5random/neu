from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Optional, Callable, Any

from nicegui import ui

from src.config import EmailConfig, get_global_config, save_global_config, get_logger
from src.gui.easter_egg import sync_game_of_life_activation_from_config
from src.gui.email_visibility import (
    get_visible_active_groups as get_gui_visible_active_groups,
    get_visible_groups as get_gui_visible_groups,
)
from src.gui.duration_utils import (
    DURATION_UNIT_OPTIONS,
    DurationDisplayConfig,
    build_duration_display_config,
    coerce_duration_value,
    duration_value_to_seconds,
    normalize_duration_unit,
    pick_duration_unit,
)
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


def _visible_groups(email_cfg: Any) -> dict[str, list[str]]:
    return get_gui_visible_groups(email_cfg)


def _visible_active_groups(email_cfg: Any) -> list[str]:
    return get_gui_visible_active_groups(email_cfg)


def _apply_duration_control_display(number_ctrl: Any, display: DurationDisplayConfig) -> None:
    number_ctrl.min = display.min_value
    number_ctrl.max = display.max_value
    number_ctrl.format = display.format
    number_ctrl.props(remove='step suffix')
    number_ctrl.props(f'step={display.step} suffix={json.dumps(display.suffix)}')
    number_ctrl.value = display.display_value
    number_ctrl.update()


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
    groups = _visible_groups(email_cfg)
    active_groups = _visible_active_groups(email_cfg)
    group_names = list(groups.keys())

    notification_state: dict[str, Any] = {
        'recipient_pool': list(recipient_pool),
        'groups': groups,
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

    TIME_CONTROL_CONFIGS: dict[str, dict[str, Any]] = {
        'alert_delay_seconds': {
            'title': 'Alert Delay',
            'tooltip': 'How long no motion may be detected before the first alert is sent.',
            'description': 'Wait time without motion before the first inactivity alert is triggered.',
            'min_seconds': 30.0,
            'max_seconds': 3600.0,
            'allowed_units': ('s', 'min', 'h'),
            'default_unit': 'min',
            'allow_zero': False,
            'integer_seconds': True,
        },
        'alert_check_interval': {
            'title': 'Check Interval',
            'tooltip': 'How often the controller evaluates alert conditions.',
            'description': 'How frequently the no-motion condition is checked in the background.',
            'min_seconds': 0.5,
            'max_seconds': 120.0,
            'allowed_units': ('s', 'min'),
            'default_unit': 's',
            'allow_zero': False,
            'integer_seconds': False,
        },
        'alert_cooldown_seconds': {
            'title': 'Alert Cooldown',
            'tooltip': 'Minimum time between two alerts.',
            'description': 'Pause between repeated alerts after one has already been sent.',
            'min_seconds': 0.0,
            'max_seconds': 3600.0,
            'allowed_units': ('s', 'min', 'h'),
            'default_unit': 'min',
            'allow_zero': True,
            'integer_seconds': True,
        },
        'inactivity_timeout_minutes': {
            'title': 'Inactivity Timeout',
            'tooltip': 'Stop session after prolonged inactivity (0 = disabled).',
            'description': 'Optional hard stop for a session that remains idle for a longer period.',
            'min_seconds': 0.0,
            'max_seconds': 43200.0,
            'allowed_units': ('min', 'h'),
            'default_unit': 'min',
            'allow_zero': True,
            'integer_seconds': True,
        },
        'motion_summary_interval_seconds': {
            'title': 'Summary Interval',
            'tooltip': 'Period for motion summary logs (>= 5 seconds).',
            'description': 'How often periodic motion summaries are written to the logs.',
            'min_seconds': 5.0,
            'max_seconds': 3600.0,
            'allowed_units': ('s', 'min', 'h'),
            'default_unit': 'min',
            'allow_zero': False,
            'integer_seconds': True,
        },
    }

    counts_labels: dict[str, ui.label] = {}
    active_groups_select: Optional[ui.select] = None
    group_table: Optional[ui.table] = None
    static_table: Optional[ui.table] = None
    preview_table: Optional[ui.table] = None
    notification_apply_btn: Optional[ui.button] = None
    duration_controls: dict[str, dict[str, Any]] = {}

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
        description: str | None,
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
            if description:
                ui.label(description).classes('text-caption text-grey-7 mb-2 self-start')
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

    def _state_value_to_seconds(key: str, source_state: Optional[dict[str, Any]] = None) -> float:
        current_state = source_state or state
        raw_value = current_state[key]
        if key == 'inactivity_timeout_minutes':
            return float(int(raw_value or 0) * 60)
        return float(raw_value or 0)

    def _seconds_to_state_value(key: str, seconds: float) -> Any:
        if key == 'alert_check_interval':
            return float(seconds)
        if key == 'inactivity_timeout_minutes':
            return int(round(float(seconds) / 60.0))
        return int(round(float(seconds)))

    def _build_duration_display(key: str, *, seconds_value: float, unit: str) -> DurationDisplayConfig:
        config_meta = TIME_CONTROL_CONFIGS[key]
        return build_duration_display_config(
            seconds_value,
            unit,
            min_seconds=config_meta['min_seconds'],
            max_seconds=config_meta['max_seconds'],
            allowed_units=config_meta['allowed_units'],
            allow_zero=config_meta['allow_zero'],
        )

    def _refresh_duration_control(key: str, *, seconds_value: Optional[float] = None) -> None:
        control = duration_controls[key]
        config_meta = TIME_CONTROL_CONFIGS[key]
        unit = normalize_duration_unit(
            getattr(control['unit'], 'value', config_meta['default_unit']),
            allowed_units=config_meta['allowed_units'],
            default=config_meta['default_unit'],
        )
        if control['meta'].get('unit') != unit:
            control['meta']['unit'] = unit
            try:
                control['unit'].value = unit
            except Exception:
                pass

        current_seconds = _state_value_to_seconds(key) if seconds_value is None else float(seconds_value)
        display = _build_duration_display(key, seconds_value=current_seconds, unit=unit)
        _apply_duration_control_display(control['number'], display)
        control['unit'].update()

    def _read_duration_control_seconds(key: str) -> float:
        control = duration_controls[key]
        config_meta = TIME_CONTROL_CONFIGS[key]
        unit = normalize_duration_unit(
            getattr(control['unit'], 'value', config_meta['default_unit']),
            allowed_units=config_meta['allowed_units'],
            default=config_meta['default_unit'],
        )
        raw_value = coerce_duration_value(
            getattr(control['number'], 'value', 0.0),
            default=0.0,
        )
        seconds = duration_value_to_seconds(
            raw_value,
            unit,
            minimum_seconds=config_meta['min_seconds'],
            allowed_units=config_meta['allowed_units'],
            allow_zero=config_meta['allow_zero'],
        )
        seconds = min(float(config_meta['max_seconds']), float(seconds))
        if config_meta['allow_zero'] and raw_value <= 0:
            seconds = 0.0
        if config_meta['integer_seconds']:
            return float(int(round(seconds)))
        return round(float(seconds), 3)

    def _build_duration_card(key: str, *, wrap_card: bool = True) -> tuple[Any, Any]:
        config_meta = TIME_CONTROL_CONFIGS[key]
        initial_seconds = _state_value_to_seconds(key)
        selected_unit = pick_duration_unit(
            initial_seconds,
            allowed_units=config_meta['allowed_units'],
            default=config_meta['default_unit'],
        )
        if config_meta['allow_zero'] and initial_seconds <= 0:
            selected_unit = normalize_duration_unit(
                config_meta['default_unit'],
                allowed_units=config_meta['allowed_units'],
                default=config_meta['default_unit'],
            )
        initial_display = _build_duration_display(key, seconds_value=initial_seconds, unit=selected_unit)
        selected_unit = initial_display.unit

        parent = ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;') if wrap_card else ui.column().classes('w-full gap-2')
        with parent:
            if wrap_card:
                ui.label(config_meta['title']).classes('font-semibold mb-1 self-start')
                description = str(config_meta.get('description') or '').strip()
                if description:
                    ui.label(description).classes('text-caption text-grey-7 mb-2 self-start')
            with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                number_ctrl = (
                    ui.number(
                        value=initial_display.display_value,
                        min=initial_display.min_value,
                        max=initial_display.max_value,
                        step=initial_display.step,
                        format=initial_display.format,
                    )
                    .props(f'dense outlined hide-bottom-space suffix={json.dumps(initial_display.suffix)}')
                    .tooltip(config_meta['tooltip'])
                    .classes(value_classes)
                )
                unit_ctrl = (
                    ui.select(
                        options={unit: DURATION_UNIT_OPTIONS[unit] for unit in config_meta['allowed_units']},
                        value=selected_unit,
                    )
                    .props('dense outlined options-dense')
                    .tooltip(config_meta['tooltip'])
                    .classes('w-32 shrink-0')
                )

        duration_controls[key] = {
            'number': number_ctrl,
            'unit': unit_ctrl,
            'meta': {'unit': selected_unit},
        }

        def _on_number_change(_: Any = None) -> None:
            _on_change()

        def _on_unit_change(_: Any = None) -> None:
            previous_unit = duration_controls[key]['meta']['unit']
            new_unit = normalize_duration_unit(
                getattr(unit_ctrl, 'value', selected_unit),
                allowed_units=config_meta['allowed_units'],
                default=config_meta['default_unit'],
            )
            current_seconds = duration_value_to_seconds(
                coerce_duration_value(
                    getattr(number_ctrl, 'value', 0.0),
                    default=0.0,
                ),
                previous_unit,
                minimum_seconds=config_meta['min_seconds'],
                allowed_units=config_meta['allowed_units'],
                allow_zero=config_meta['allow_zero'],
            )
            if config_meta['allow_zero'] and coerce_duration_value(getattr(number_ctrl, 'value', 0.0), default=0.0) <= 0:
                current_seconds = 0.0
            duration_controls[key]['meta']['unit'] = new_unit
            unit_ctrl.value = new_unit
            _refresh_duration_control(key, seconds_value=current_seconds)
            _on_change()

        number_ctrl.on('update:model-value', _on_number_change)
        number_ctrl.on('blur', _on_number_change)
        unit_ctrl.on('update:model-value', _on_unit_change)
        _refresh_duration_control(key, seconds_value=initial_seconds)
        return number_ctrl, unit_ctrl

    def _build_toggle_card(
        title: str,
        checkbox_label: str,
        value: bool,
        tooltip: str,
        description: str | None = None,
    ) -> Any:
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full').style('align-items:stretch;'):
            ui.label(title).classes('font-semibold mb-1 self-start')
            if description:
                ui.label(description).classes('text-caption text-grey-7 mb-2 self-start')
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
            send_as_html=bool(getattr(current_email_cfg, 'send_as_html', False)),
            groups={group: list(members) for group, members in notification_state['groups'].items()},
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
        sync_game_of_life_activation_from_config()

        current_email_cfg = current_cfg.email
        current_groups = _visible_groups(current_email_cfg)
        current_recipient_pool = _get_known_recipients(current_email_cfg)
        current_active_groups = _visible_active_groups(current_email_cfg)
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
                sync_game_of_life_activation_from_config()
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
                'alert_delay_seconds': int(round(_read_duration_control_seconds('alert_delay_seconds'))),
                'max_alerts_per_session': int(_get_control_value(max_alerts_inp, state['max_alerts_per_session']) or 0),
                'alert_check_interval': float(_read_duration_control_seconds('alert_check_interval')),
                'alert_cooldown_seconds': int(round(_read_duration_control_seconds('alert_cooldown_seconds'))),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, state['alert_include_snapshot'])),
                'inactivity_timeout_minutes': int(_seconds_to_state_value('inactivity_timeout_minutes', _read_duration_control_seconds('inactivity_timeout_minutes'))),
                'motion_summary_interval_seconds': int(round(_read_duration_control_seconds('motion_summary_interval_seconds'))),
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
                'alert_delay_seconds': max(30, int(round(_read_duration_control_seconds('alert_delay_seconds')))),
                'max_alerts_per_session': max(1, int(_get_control_value(max_alerts_inp, 1) or 1)),
                'alert_check_interval': max(0.5, float(_read_duration_control_seconds('alert_check_interval'))),
                'alert_cooldown_seconds': max(0, int(round(_read_duration_control_seconds('alert_cooldown_seconds')))),
                'alert_include_snapshot': bool(_get_control_value(include_snapshot_cb, False)),
                'inactivity_timeout_minutes': max(
                    0,
                    int(_seconds_to_state_value('inactivity_timeout_minutes', _read_duration_control_seconds('inactivity_timeout_minutes'))),
                ),
                'motion_summary_interval_seconds': max(5, int(round(_read_duration_control_seconds('motion_summary_interval_seconds')))),
                'enable_motion_summary_logs': bool(_get_control_value(enable_summary_cb, True)),
            }

            if float(_read_duration_control_seconds('alert_delay_seconds')) < 30:
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
                for duration_key in TIME_CONTROL_CONFIGS.keys():
                    _refresh_duration_control(duration_key, seconds_value=_state_value_to_seconds(duration_key, state))
                apply_btn.disable()
                notify_user('Measurement settings saved', kind='positive')
            else:
                notify_user('Failed to save settings', kind='negative')
        except Exception as exc:
            logger.error('Failed to save measurement settings: %s', exc, exc_info=True)
            notify_user(f'Error saving settings: {exc}', kind='negative')

    ui.label(
        'These controls define when inactivity alerts start, how often follow-up alerts may be sent, and which extra context is included.'
    ).classes('text-body2 text-grey-7 mb-1')

    with ui.grid(columns=2).classes('w-full gap-3 mb-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        alert_delay_inp, alert_delay_unit = _build_duration_card('alert_delay_seconds')
        max_alerts_inp, max_alerts_slider = _build_numeric_card(
            'max_alerts_per_session',
            title='Max Alerts Per Session',
            description='Limits the number of inactivity alerts within one session to avoid spamming.',
            tooltip='Upper bound on alerts within a single session (spam protection)',
            min_value=1,
            max_value=50,
            step=1,
            fmt='%.0f',
            suffix='',
            caster=_cast_to_int,
            as_int=True,
        )
        check_interval_inp, check_interval_unit = _build_duration_card('alert_check_interval')
        cooldown_inp, cooldown_unit = _build_duration_card('alert_cooldown_seconds')
        inactivity_inp, inactivity_unit = _build_duration_card('inactivity_timeout_minutes')

    with ui.grid(columns=2).classes('w-full gap-3 items-start').style(
        'grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); grid-auto-rows:min-content;'
    ):
        include_snapshot_cb = _build_toggle_card(
            'Alert Snapshot',
            'Include snapshot in alert',
            bool(state['alert_include_snapshot']),
            'Attach a snapshot to alert emails when an alert is sent',
            description='Adds the current camera frame to alert emails for faster diagnosis.',
        )
        with ui.card().tight().classes('p-3 flex flex-col self-start w-full gap-3').style('align-items:stretch;'):
            ui.label('Motion Summary Logs').classes('font-semibold mb-1 self-start')
            ui.label(
                'Enable periodic log entries to track long-running sessions even when no alert is sent.'
            ).classes('text-caption text-grey-7 self-start')
            enable_summary_cb = (
                ui.checkbox(
                    'Enable motion summary logs',
                    value=bool(state['enable_motion_summary_logs']),
                )
                .tooltip('Master switch for periodic motion summary logging')
                .classes('self-start')
            )
            ui.label('Summary Interval').classes('text-caption text-grey-7')
            ui.label(
                'Sets how often the summary logger writes the latest motion status to the logs.'
            ).classes('text-caption text-grey-7 self-start')
            summary_interval_inp, summary_interval_unit = _build_duration_card(
                'motion_summary_interval_seconds',
                wrap_card=False,
            )
            summary_interval_inp.bind_enabled_from(enable_summary_cb, 'value')
            summary_interval_unit.bind_enabled_from(enable_summary_cb, 'value')

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
            'Configure lifecycle emails, choose the active groups for the current run, and review the always-active static-recipient permissions managed in E-Mail settings.'
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
                'Global Notification Preferences',
                icon='tune',
                title_classes='text-subtitle1 font-semibold',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-lg shrink-0',
            )
            ui.label(
            'Global notification preferences: overrides individual group settings.'
            ).classes('text-body2 text-grey-7')
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
