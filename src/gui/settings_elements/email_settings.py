from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, fields
import re
from typing import Optional, Any, Callable

from nicegui import ui, events, Client

from src.config import EmailConfig, get_global_config, save_global_config, get_logger
from src.gui.email_visibility import (
    get_visible_active_groups as get_gui_visible_active_groups,
    get_visible_groups as get_gui_visible_groups,
)
from src.notify import EMailSystem
from src.gui.util import notify_user, schedule_bg
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row

logger = get_logger("gui.email_settings")

EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")
GROUP_NAME_MAX_LEN = 20
EVENT_KEYS = ("on_start", "on_end", "on_stop")
EVENT_LABELS = {
    "on_start": "Start",
    "on_end": "End",
    "on_stop": "Stop",
}
OVERVIEW_CARD_TOOLTIPS = {
    "recipients": "All known email addresses in the shared address book.",
    "groups": "Configured recipient groups used to organize non-static recipients.",
    "active_groups": "Groups currently active for the running measurement selection.",
    "static_recipients": "Recipients who always receive emails in addition to any active groups.",
    "effective_total": "Union of static recipients and members of currently active groups.",
    "start_count": "Recipients who would receive a measurement start email right now.",
    "end_count": "Recipients who would receive a measurement end email right now.",
    "stop_count": "Recipients who would receive a measurement stop email right now.",
}
EMAIL_TOOLTIP_TEXTS = {
    "routing_hint": "Routing logic is configured in Measurement settings. This page manages addresses, static recipients, groups, and SMTP.",
    "test_email": "Send a test email to the currently effective recipient set.",
    "overview_table": "Preview of the recipients that are currently reachable through static delivery and active groups.",
    "delete_selected": "Remove the selected addresses from the address book, all groups, and the static recipient list.",
    "address_input": "Add an address to the shared address book. Static delivery can be toggled in the table below.",
    "add_address": "Add the typed address to the shared address book.",
    "recipient_table": "Shared address book. You can rename addresses inline, toggle static delivery, and inspect group membership.",
    "rename_address": "Click the address to rename this entry.",
    "static_toggle": "Always send lifecycle and alert emails to this address, regardless of which groups are active.",
    "group_select": "Browse existing groups and choose which one to edit.",
    "group_load": "Select this existing group for editing.",
    "group_unload": "Deselect the currently selected group and return the edit flow to its default state.",
    "group_new": "Start creating a new recipient group.",
    "group_name": "Name of the recipient group used to organize addresses.",
    "group_next": "Continue to member selection for the current group definition.",
    "group_members": "Choose which address-book entries belong to this group.",
    "group_back_select": "Return to the naming step for the new group.",
    "group_back_choose": "Return to the group-selection step.",
    "group_back_delete": "Return to the delete-group step for the selected group.",
    "group_back_events": "Return to the member selection step.",
    "group_events": "Choose which lifecycle emails this group may receive when it is active.",
    "group_back_review": "Return to the event-permission step.",
    "group_review": "Review the current group definition before saving or deleting it.",
    "group_reset": "Discard unsaved changes in the current tab and restore its default state.",
    "group_delete": "Delete the currently loaded existing group and remove it from the active group selection.",
    "group_save": "Save the group shown in the current tab.",
    "smtp_sender": "Email address used as the sender of outgoing emails.",
    "smtp_server": "Hostname or IP address of the SMTP server.",
    "smtp_port": "SMTP port used for the outgoing connection.",
    "smtp_status": "Validation state of the current SMTP input values.",
    "smtp_save": "Persist the current SMTP settings to the configuration file.",
}


@dataclass(frozen=True)
class PersistResult:
    ok: bool
    message: str


def sanitize_group_addresses(addresses: list[str]) -> list[str]:
    """Return a deduplicated list of valid email addresses."""
    seen: dict[str, None] = {}
    for addr in addresses or []:
        candidate = (addr or "").strip()
        if candidate and EMAIL_RE.match(candidate) and candidate not in seen:
            seen[candidate] = None
    return list(seen.keys())


def sanitize_groups_dict(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Sanitize an entire groups mapping (name -> addresses)."""
    clean: dict[str, list[str]] = {}
    for name, addrs in (groups or {}).items():
        clean[str(name)] = sanitize_group_addresses(list(addrs or []))
    return clean


def extract_rename_addresses(args: Any) -> tuple[Optional[str], Optional[str]]:
    """Normalize rename event payloads from NiceGUI/Quasar into two addresses."""
    if isinstance(args, (list, tuple)) and len(args) == 1:
        args = args[0]

    if isinstance(args, dict):
        old_addr = args.get("oldAddress") or args.get("address")
        new_addr = args.get("newAddress") or args.get("value")
    elif isinstance(args, (list, tuple)) and len(args) >= 2:
        old_addr, new_addr = args[0], args[1]
    else:
        return None, None

    old_text = old_addr.strip() if isinstance(old_addr, str) else None
    new_text = new_addr.strip() if isinstance(new_addr, str) else None
    return old_text or None, new_text or None


def _event_prefs(source: Optional[dict[str, bool]] = None, *, default: bool = True) -> dict[str, bool]:
    prefs = source or {}
    return {key: bool(prefs.get(key, default)) for key in EVENT_KEYS}


def _iterable_str_list(value: object) -> list[str]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [item for item in value if isinstance(item, str)]
    return []


def _event_model_value(event: Any) -> Any:
    if event is None:
        return None
    if hasattr(event, "value"):
        value = getattr(event, "value")
        if value is not None:
            return value
    args = getattr(event, "args", None)
    if isinstance(args, dict):
        return args.get("value")
    if isinstance(args, (list, tuple)) and len(args) == 1:
        return args[0]
    return args


def _known_recipients(email_cfg: Any) -> list[str]:
    getter = getattr(email_cfg, "get_known_recipients", None)
    if callable(getter):
        return _iterable_str_list(getter())
    return _iterable_str_list(getattr(email_cfg, "recipients", []))


def _default_group_editor_state() -> dict[str, Any]:
    return {
        "selected": None,
        "name": "",
        "members": [],
        "event_prefs": _event_prefs(default=True),
    }


def _resolve_group_editor_state(
    groups: dict[str, list[str]],
    group_prefs: dict[str, dict[str, bool]],
    selected_name: Optional[str],
) -> tuple[dict[str, Any], str]:
    if not selected_name or selected_name not in groups:
        return _default_group_editor_state(), "select"

    return (
        {
            "selected": selected_name,
            "name": selected_name,
            "members": sanitize_group_addresses(list(groups.get(selected_name, []) or [])),
            "event_prefs": _event_prefs(group_prefs.get(selected_name), default=True),
        },
        "members",
    )


def _validate_group_name(
    name: str,
    existing_groups: dict[str, list[str]],
    *,
    selected_name: Optional[str] = None,
) -> Optional[str]:
    candidate = (name or "").strip()
    if not candidate:
        return "Group name must not be empty"
    if len(candidate) > GROUP_NAME_MAX_LEN:
        return f"Group name must be at most {GROUP_NAME_MAX_LEN} characters"
    try:
        EmailConfig.ensure_group_name_allowed(candidate, context="group")
    except ValueError as exc:
        return str(exc)
    if selected_name != candidate and candidate in existing_groups:
        return "Group name already exists"
    return None


def _describe_group_name_status(
    name: str,
    existing_groups: dict[str, list[str]],
    *,
    selected_name: Optional[str] = None,
) -> tuple[str, str]:
    candidate = (name or "").strip()
    if not candidate:
        return ("Enter a unique name for the new group.", "text-grey-7")
    validation_error = _validate_group_name(candidate, existing_groups, selected_name=selected_name)
    if validation_error == "Group name already exists":
        return ("This group name is already taken. Choose another one.", "text-warning")
    if validation_error is not None:
        return (validation_error, "text-warning")
    if selected_name == candidate and candidate in existing_groups:
        return ("Editing existing group.", "text-positive")
    remaining = GROUP_NAME_MAX_LEN - len(candidate)
    remaining_text = "1 character remaining" if remaining == 1 else f"{remaining} characters remaining"
    return (f"Name available. {remaining_text}.", "text-positive")


def _visible_groups(email_cfg: Any) -> dict[str, list[str]]:
    return sanitize_groups_dict(get_gui_visible_groups(email_cfg))


def _visible_active_groups(email_cfg: Any) -> list[str]:
    return get_gui_visible_active_groups(email_cfg)


def _get_effective_recipients_from_config(cfg: Any, state: dict[str, Any]) -> list[str]:
    """Return effective recipients from cfg.email if available; fallback to local state."""
    try:
        if cfg and getattr(cfg, "email", None):
            getter = getattr(cfg.email, "get_target_recipients", None)
            if callable(getter):
                return _iterable_str_list(getter())
    except Exception:
        logger.debug("Falling back to local recipients for effective list", exc_info=True)
    local = list(state.get("static_recipients", []) or [])
    for group_name in state.get("active_groups", []) or []:
        local.extend((state.get("groups", {}) or {}).get(group_name, []) or [])
    effective = sanitize_group_addresses(local)
    if effective:
        return effective
    return list(state.get("recipients", []) or [])


def _get_live_email_cfg(cfg: Any, fallback: EmailConfig) -> EmailConfig:
    if cfg and getattr(cfg, "email", None):
        return cfg.email
    return fallback


def _delete_group_routing_refs(email_cfg: EmailConfig, group_name: str) -> None:
    email_cfg.group_prefs.pop(group_name, None)
    email_cfg.active_groups = [group for group in email_cfg.active_groups if group != group_name]


def _rename_recipient_routing_refs(email_cfg: EmailConfig, old_addr: str, new_addr: str) -> None:
    if old_addr == new_addr:
        return
    email_cfg.static_recipients = [new_addr if addr == old_addr else addr for addr in email_cfg.static_recipients]
    if old_addr in email_cfg.recipient_prefs:
        email_cfg.recipient_prefs[new_addr] = email_cfg.recipient_prefs.pop(old_addr)


def _delete_recipient_routing_refs(email_cfg: EmailConfig, recipients: list[str]) -> None:
    removed = set(recipients)
    if not removed:
        return
    email_cfg.static_recipients = [addr for addr in email_cfg.static_recipients if addr not in removed]
    for addr in removed:
        email_cfg.recipient_prefs.pop(addr, None)


def _finalize_structural_email_config(email_cfg: EmailConfig) -> None:
    email_cfg.groups = _visible_groups(email_cfg)
    email_cfg.active_groups = [group for group in email_cfg.active_groups if group in email_cfg.groups]
    email_cfg.group_prefs = {
        name: _event_prefs((email_cfg.group_prefs or {}).get(name), default=True)
        for name in email_cfg.groups.keys()
    }
    email_cfg.enable_explicit_targeting(materialize_legacy_targets=True)
    email_cfg.recipient_prefs = {
        addr: _event_prefs((email_cfg.recipient_prefs or {}).get(addr), default=True)
        for addr in email_cfg.static_recipients
    }
    email_cfg.recipients = email_cfg.get_known_recipients()


def _build_email_preview_cfg(
    cfg: Any,
    fallback: EmailConfig,
    *,
    recipients: list[str],
    groups: dict[str, list[str]],
    static_recipients: list[str],
    group_prefs: dict[str, dict[str, bool]],
) -> EmailConfig:
    current_email_cfg = _get_live_email_cfg(cfg, fallback)
    active_groups = [group for group in _iterable_str_list(getattr(current_email_cfg, "active_groups", [])) if group in groups]
    return EmailConfig(
        website_url=current_email_cfg.website_url,
        recipients=list(recipients),
        smtp_server=current_email_cfg.smtp_server,
        smtp_port=current_email_cfg.smtp_port,
        sender_email=current_email_cfg.sender_email,
        templates={name: dict(template_cfg) for name, template_cfg in current_email_cfg.templates.items()},
        groups={name: list(members) for name, members in groups.items()},
        active_groups=active_groups,
        static_recipients=list(static_recipients),
        explicit_targeting=bool(getattr(current_email_cfg, "explicit_targeting", False)),
        notifications=dict(getattr(current_email_cfg, "notifications", {}) or {}),
        group_prefs={name: _event_prefs(group_prefs.get(name), default=True) for name in groups.keys()},
        recipient_prefs={
            addr: _event_prefs(prefs, default=True)
            for addr, prefs in (getattr(current_email_cfg, "recipient_prefs", {}) or {}).items()
        },
    )


def _snapshot_email_config_state(email_cfg: EmailConfig) -> dict[str, Any]:
    return {
        config_field.name: deepcopy(getattr(email_cfg, config_field.name))
        for config_field in fields(EmailConfig)
    }


def _restore_email_config_state(email_cfg: EmailConfig, snapshot: dict[str, Any]) -> None:
    for config_field in fields(EmailConfig):
        if config_field.name in snapshot:
            setattr(email_cfg, config_field.name, deepcopy(snapshot[config_field.name]))


def _snapshot_group_editor_state(group_editor: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected": group_editor.get("selected"),
        "name": (group_editor.get("name") or "").strip(),
        "members": sanitize_group_addresses(list(group_editor.get("members", []) or [])),
        "event_prefs": _event_prefs(group_editor.get("event_prefs"), default=True),
    }


def _restore_group_editor_state(group_editor: dict[str, Any], snapshot: dict[str, Any]) -> None:
    group_editor.clear()
    group_editor.update(_snapshot_group_editor_state(snapshot))


def _group_editor_baseline_state(
    groups: dict[str, list[str]],
    group_prefs: dict[str, dict[str, bool]],
    selected_name: Optional[str],
) -> dict[str, Any]:
    baseline_state, _ = _resolve_group_editor_state(groups, group_prefs, selected_name)
    return baseline_state


def _is_group_editor_dirty(
    group_editor: dict[str, Any],
    groups: dict[str, list[str]],
    group_prefs: dict[str, dict[str, bool]],
) -> bool:
    current_state = _snapshot_group_editor_state(group_editor)
    baseline_state = _group_editor_baseline_state(groups, group_prefs, current_state.get("selected"))
    return current_state != baseline_state


def _resolve_group_delete_target(
    group_editor: dict[str, Any],
    groups: dict[str, list[str]],
) -> Optional[str]:
    selected_name = group_editor.get("selected")
    if isinstance(selected_name, str) and selected_name in groups:
        return selected_name
    return None


def create_emailcard(*, email_system: Optional[EMailSystem] = None) -> None:
    """Render email setup, address book, and guided group management."""
    config = get_global_config()
    if not config:
        ui.label("Configuration not available").classes("text-red")
        return

    email_cfg = config.email
    recipients = _known_recipients(email_cfg)

    state: dict[str, Any] = {
        "recipients": list(recipients),
        "smtp": {
            "server": email_cfg.smtp_server,
            "port": email_cfg.smtp_port,
            "sender": email_cfg.sender_email,
        },
        "groups": _visible_groups(email_cfg),
        "static_recipients": list(email_cfg.get_static_recipients_for_editor()),
        "group_prefs": {
            group_name: _event_prefs((getattr(email_cfg, "group_prefs", {}) or {}).get(group_name), default=True)
            for group_name in _visible_groups(email_cfg).keys()
        },
    }
    create_group_editor: dict[str, Any] = _default_group_editor_state()
    edit_group_editor: dict[str, Any] = _default_group_editor_state()
    current_group_tab = "create"
    loaded_group_name: Optional[str] = None

    recipient_table: Optional[ui.table] = None
    overview_table: Optional[ui.table] = None
    delete_btn: Optional[ui.button] = None
    email_inp: Optional[ui.input] = None
    group_mode_tabs: Optional[Any] = None
    create_group_stepper: Optional[Any] = None
    edit_group_stepper: Optional[Any] = None
    create_group_tab: Optional[Any] = None
    create_group_name_input: Optional[ui.input] = None
    create_group_name_status_label: Optional[ui.label] = None
    create_group_next_btn: Optional[ui.button] = None
    create_group_members_select: Optional[ui.select] = None
    create_group_review_label: Optional[ui.label] = None
    create_group_members_list: Optional[ui.list] = None
    create_group_event_toggles: dict[str, ui.checkbox] = {}
    edit_group_browser: Optional[ui.column] = None
    edit_group_loaded_label: Optional[ui.label] = None
    edit_group_hint_label: Optional[ui.label] = None
    edit_group_add_members_select: Optional[ui.select] = None
    edit_group_add_members_btn: Optional[ui.button] = None
    edit_group_member_editor_list: Optional[ui.column] = None
    edit_group_review_label: Optional[ui.label] = None
    edit_group_members_list: Optional[ui.list] = None
    edit_group_event_toggles: dict[str, ui.checkbox] = {}
    edit_group_select_next_btn: Optional[ui.button] = None
    edit_group_deselect_btn: Optional[ui.button] = None
    edit_group_delete_next_btn: Optional[ui.button] = None
    edit_group_reset_btn: Optional[ui.button] = None
    edit_group_save_btn: Optional[ui.button] = None
    overview_counts: dict[str, ui.label] = {}
    notification_labels: dict[str, ui.label] = {}
    discard_group_dialog: Optional[ui.dialog] = None
    discard_group_title_label: Optional[ui.label] = None
    discard_group_message_label: Optional[ui.label] = None
    discard_group_confirm_btn: Optional[ui.button] = None
    edit_delete_group_btn: Optional[ui.button] = None
    edit_delete_group_tooltip: Optional[ui.tooltip] = None
    _pending_group_editor_action: Optional[Callable[[], None]] = None

    if email_system is None:
        logger.error("Email system is not initialized, email functionality will be disabled.")

    def _ensure_address_book() -> None:
        merged = list(state["recipients"])
        merged.extend(state["static_recipients"])
        for members in state["groups"].values():
            merged.extend(members or [])
        state["recipients"] = sanitize_group_addresses(merged)

    def _snapshot_state() -> dict[str, Any]:
        return {
            "recipients": list(state["recipients"]),
            "smtp": dict(state["smtp"]),
            "groups": {name: list(members) for name, members in state["groups"].items()},
            "static_recipients": list(state["static_recipients"]),
            "group_prefs": {name: dict(prefs) for name, prefs in state["group_prefs"].items()},
        }

    def _restore_state(snapshot: dict[str, Any]) -> None:
        for key, value in snapshot.items():
            state[key] = value

    def _sync_state_from_email_cfg(email_cfg_obj: EmailConfig) -> None:
        state["recipients"] = list(_known_recipients(email_cfg_obj))
        state["smtp"] = {
            "server": email_cfg_obj.smtp_server,
            "port": email_cfg_obj.smtp_port,
            "sender": email_cfg_obj.sender_email,
        }
        state["groups"] = _visible_groups(email_cfg_obj)
        state["static_recipients"] = list(email_cfg_obj.get_static_recipients_for_editor())
        state["group_prefs"] = {
            name: _event_prefs((email_cfg_obj.group_prefs or {}).get(name), default=True)
            for name in state["groups"].keys()
        }

    def _preview_email_cfg() -> EmailConfig:
        return _build_email_preview_cfg(
            get_global_config(),
            email_cfg,
            recipients=list(state["recipients"]),
            groups={name: list(members) for name, members in state["groups"].items()},
            static_recipients=list(state["static_recipients"]),
            group_prefs={name: dict(prefs) for name, prefs in state["group_prefs"].items()},
        )

    def _recipient_groups(addr: str) -> list[str]:
        return sorted([name for name, members in state["groups"].items() if addr in (members or [])])

    def _recipient_row(addr: str) -> dict[str, Any]:
        groups = _recipient_groups(addr)
        visible_active_groups = _visible_active_groups(_get_live_email_cfg(get_global_config(), email_cfg))
        return {
            "address": addr,
            "static_enabled": addr in state["static_recipients"],
            "groups": ", ".join(groups) if groups else "-",
            "active_groups": ", ".join([name for name in groups if name in visible_active_groups]) or "-",
        }

    def _overview_rows() -> list[dict[str, str]]:
        preview_cfg = _preview_email_cfg()
        effective_all = set(preview_cfg.get_target_recipients())
        event_targets = {key: set(preview_cfg.get_measurement_event_recipients(key)) for key in EVENT_KEYS}
        rows: list[dict[str, str]] = []
        for addr in preview_cfg.get_known_recipients():
            if addr not in effective_all and not any(addr in members for members in event_targets.values()):
                continue
            sources = []
            if addr in state["static_recipients"]:
                sources.append("static")
            sources.extend([name for name in preview_cfg.active_groups if addr in (state["groups"].get(name, []) or [])])
            rows.append(
                {
                    "address": addr,
                    "sources": ", ".join(sources) if sources else "-",
                    "on_start": "yes" if addr in event_targets["on_start"] else "-",
                    "on_end": "yes" if addr in event_targets["on_end"] else "-",
                    "on_stop": "yes" if addr in event_targets["on_stop"] else "-",
                }
            )
        return rows

    def refresh_recipient_table() -> None:
        if recipient_table is not None:
            recipient_table.rows = [_recipient_row(addr) for addr in state["recipients"]]
            recipient_table.update()

    def refresh_overview() -> None:
        preview_cfg = _preview_email_cfg()
        live_email_cfg = _get_live_email_cfg(get_global_config(), email_cfg)
        counts = {
            "recipients": len(state["recipients"]),
            "groups": len(state["groups"]),
            "active_groups": len(preview_cfg.active_groups),
            "static_recipients": len(state["static_recipients"]),
            "effective_total": len(preview_cfg.get_target_recipients()),
            "start_count": len(preview_cfg.get_measurement_event_recipients("on_start")),
            "end_count": len(preview_cfg.get_measurement_event_recipients("on_end")),
            "stop_count": len(preview_cfg.get_measurement_event_recipients("on_stop")),
        }
        for key, label in overview_counts.items():
            label.text = str(counts.get(key, 0))
            label.update()
        for event_key, label in notification_labels.items():
            enabled = bool((getattr(live_email_cfg, "notifications", {}) or {}).get(event_key, False))
            label.text = "enabled" if enabled else "disabled"
            label.classes(remove="text-positive text-grey", add=("text-positive" if enabled else "text-grey"))
            label.update()
        if overview_table is not None:
            overview_table.rows = _overview_rows()
            overview_table.update()

    def _refresh_live_routing_snapshot() -> None:
        refresh_recipient_table()
        refresh_overview()

    def _sync_create_group_name_candidate(raw_value: Any = None) -> str:
        candidate = raw_value if isinstance(raw_value, str) else getattr(create_group_name_input, "value", create_group_editor["name"])
        normalized = (candidate or "").strip()
        create_group_editor["selected"] = None
        create_group_editor["name"] = normalized
        return normalized

    def _refresh_create_group_name_status(*, candidate: Optional[str] = None) -> bool:
        candidate = _sync_create_group_name_candidate(candidate)
        is_valid = _validate_group_name(candidate, state["groups"]) is None
        status_text, status_class = _describe_group_name_status(candidate, state["groups"])
        if create_group_name_status_label is not None:
            create_group_name_status_label.text = status_text
            create_group_name_status_label.classes(
                remove="text-grey-7 text-warning text-positive",
                add=status_class,
            )
            create_group_name_status_label.update()
        if create_group_next_btn is not None:
            if is_valid:
                create_group_next_btn.enable()
            else:
                create_group_next_btn.disable()
            create_group_next_btn.update()
        return is_valid

    def _on_create_group_name_input(event: Any) -> None:
        raw_value = _event_model_value(event)
        if raw_value is None:
            raw_value = getattr(create_group_name_input, "value", create_group_editor["name"])
        _refresh_create_group_name_status(candidate=raw_value)

    def _finalize_create_group_name_input() -> None:
        candidate = _sync_create_group_name_candidate()
        if create_group_name_input is not None and (create_group_name_input.value or "") != candidate:
            create_group_name_input.value = candidate
            create_group_name_input.update()
        _refresh_create_group_name_status(candidate=candidate)

    def _set_group_tab(name: str) -> None:
        nonlocal current_group_tab
        current_group_tab = name
        if group_mode_tabs is not None:
            group_mode_tabs.value = name
            group_mode_tabs.update()

    def _run_pending_group_action() -> None:
        nonlocal _pending_group_editor_action
        pending_action = _pending_group_editor_action
        _pending_group_editor_action = None
        if discard_group_dialog is not None:
            discard_group_dialog.close()
        if pending_action is not None:
            pending_action()

    def _cancel_pending_group_action() -> None:
        nonlocal _pending_group_editor_action
        _pending_group_editor_action = None
        if discard_group_dialog is not None:
            discard_group_dialog.close()

    def _confirm_group_editor_action(
        *,
        title: str,
        message: str,
        confirm_label: str,
        action: Callable[[], None],
    ) -> None:
        nonlocal _pending_group_editor_action
        _pending_group_editor_action = action
        if discard_group_title_label is not None:
            discard_group_title_label.text = title
            discard_group_title_label.update()
        if discard_group_message_label is not None:
            discard_group_message_label.text = message
            discard_group_message_label.update()
        if discard_group_confirm_btn is not None:
            discard_group_confirm_btn.set_text(confirm_label)
            discard_group_confirm_btn.update()
        if discard_group_dialog is not None:
            discard_group_dialog.open()

    def _set_create_group_step(name: str) -> None:
        if create_group_stepper is None:
            return
        try:
            create_group_stepper.set_value(name)
        except Exception:
            create_group_stepper.value = name
            create_group_stepper.update()

    def _set_edit_group_step(name: str) -> None:
        if edit_group_stepper is None:
            return
        try:
            edit_group_stepper.set_value(name)
        except Exception:
            edit_group_stepper.value = name
            edit_group_stepper.update()

    def _collect_create_group_form() -> tuple[str, list[str], dict[str, bool]]:
        name = (getattr(create_group_name_input, "value", create_group_editor["name"]) or "").strip()
        raw_members = getattr(create_group_members_select, "value", create_group_editor["members"]) or []
        members = sanitize_group_addresses(list(raw_members))
        event_prefs = {
            event_key: bool(
                getattr(create_group_event_toggles.get(event_key), "value", create_group_editor["event_prefs"].get(event_key, True))
            )
            for event_key in EVENT_KEYS
        }
        create_group_editor["selected"] = None
        create_group_editor["name"] = name
        create_group_editor["members"] = members
        create_group_editor["event_prefs"] = _event_prefs(event_prefs, default=True)
        return name, members, dict(create_group_editor["event_prefs"])

    def _collect_edit_group_form() -> tuple[str, list[str], dict[str, bool]]:
        name = loaded_group_name or ""
        members = sanitize_group_addresses(list(edit_group_editor["members"]))
        event_prefs = {
            event_key: bool(
                getattr(edit_group_event_toggles.get(event_key), "value", edit_group_editor["event_prefs"].get(event_key, True))
            )
            for event_key in EVENT_KEYS
        }
        edit_group_editor["selected"] = loaded_group_name
        edit_group_editor["name"] = name
        edit_group_editor["members"] = members
        edit_group_editor["event_prefs"] = _event_prefs(event_prefs, default=True)
        return name, members, dict(edit_group_editor["event_prefs"])

    def _remove_member_from_edit_group(addr: str) -> None:
        _collect_edit_group_form()
        edit_group_editor["members"] = [member for member in edit_group_editor["members"] if member != addr]
        _refresh_edit_group_editor()

    def _refresh_edit_group_add_members_button() -> None:
        if edit_group_add_members_btn is None:
            return
        available_members_to_add = [addr for addr in state["recipients"] if addr not in edit_group_editor["members"]]
        selected_members = getattr(edit_group_add_members_select, "value", []) or []
        can_add = bool(loaded_group_name in state["groups"]) and bool(available_members_to_add) and bool(selected_members)
        if can_add:
            edit_group_add_members_btn.enable()
        else:
            edit_group_add_members_btn.disable()
        edit_group_add_members_btn.update()

    def _add_members_to_edit_group() -> None:
        _collect_edit_group_form()
        raw_members = getattr(edit_group_add_members_select, "value", []) or []
        members_to_add = sanitize_group_addresses(list(raw_members))
        if not members_to_add:
            notify_user("Choose at least one recipient to add", kind="warning")
            return
        edit_group_editor["members"] = sanitize_group_addresses(edit_group_editor["members"] + members_to_add)
        if edit_group_add_members_select is not None:
            edit_group_add_members_select.value = []
            edit_group_add_members_select.update()
        _refresh_edit_group_editor()

    def _refresh_create_group_editor() -> None:
        if create_group_name_input is not None:
            create_group_name_input.value = create_group_editor["name"]
            create_group_name_input.update()
        _refresh_create_group_name_status()
        if create_group_members_select is not None:
            create_group_members_select.options = list(state["recipients"])
            create_group_members_select.value = list(create_group_editor["members"])
            create_group_members_select.update()
        for event_key, toggle in create_group_event_toggles.items():
            toggle.value = bool(create_group_editor["event_prefs"].get(event_key, True))
            toggle.update()
        if create_group_review_label is not None:
            name = create_group_editor["name"] or "<new group>"
            enabled_events = [
                EVENT_LABELS[event_key]
                for event_key in EVENT_KEYS
                if bool(create_group_editor["event_prefs"].get(event_key, True))
            ]
            event_summary = ", ".join(enabled_events) if enabled_events else "none"
            create_group_review_label.text = (
                f"New Group: {name} | Members: {len(create_group_editor['members'])} | Lifecycle: {event_summary}"
            )
            create_group_review_label.update()
        if create_group_members_list is not None:
            create_group_members_list.clear()
            with create_group_members_list:
                for addr in create_group_editor["members"]:
                    ui.item(addr)

    def _refresh_edit_group_editor() -> None:
        nonlocal loaded_group_name
        if loaded_group_name not in state["groups"]:
            loaded_group_name = None
            _restore_group_editor_state(edit_group_editor, _default_group_editor_state())
        if edit_group_browser is not None:
            edit_group_browser.clear()
            with edit_group_browser:
                if not state["groups"]:
                    ui.label("No groups configured yet. Create one in the 'Create Group' tab first.").classes(
                        "text-body2 text-grey-7"
                    )
                else:
                    for group_name in sorted(state["groups"].keys()):
                        members = sanitize_group_addresses(list(state["groups"].get(group_name, [])))
                        group_events = _event_prefs(state["group_prefs"].get(group_name), default=True)
                        enabled_events = [
                            EVENT_LABELS[event_key]
                            for event_key in EVENT_KEYS
                            if bool(group_events.get(event_key, True))
                        ]
                        event_summary = ", ".join(enabled_events) if enabled_events else "none"
                        with ui.card().classes("w-full p-3 gap-2"):
                            with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
                                with ui.column().classes("gap-1"):
                                    ui.label(group_name).classes("text-subtitle2")
                                    ui.label(
                                        f"{len(members)} member(s) | Lifecycle: {event_summary}"
                                    ).classes("text-caption text-grey-7")
                                with ui.row().classes("items-center gap-2 flex-wrap"):
                                    if group_name == loaded_group_name:
                                        ui.badge("Selected").props("color=primary")
                                    select_btn = ui.button(
                                        "Select Group",
                                        icon="group_work",
                                        on_click=_make_edit_group_load_handler(group_name),
                                    ).props("outline").tooltip(EMAIL_TOOLTIP_TEXTS["group_load"])
                                    if group_name == loaded_group_name:
                                        select_btn.disable()
                                        select_btn.update()
        target = _resolve_group_delete_target(edit_group_editor, state["groups"])
        edit_enabled = target is not None
        if edit_group_loaded_label is not None:
            edit_group_loaded_label.text = f"Selected Group: {target}" if target else "Selected Group: -"
            edit_group_loaded_label.update()
        if edit_group_hint_label is not None:
            if not state["groups"]:
                hint_text = "No groups configured yet. Create one in the 'Create Group' tab first."
            elif target is None:
                hint_text = "Use the browser below to select a group for editing."
            else:
                hint_text = "You are editing the currently selected group. Use the browser below to switch groups or deselect it."
            edit_group_hint_label.text = hint_text
            edit_group_hint_label.update()
        if edit_group_select_next_btn is not None:
            if edit_enabled:
                edit_group_select_next_btn.enable()
            else:
                edit_group_select_next_btn.disable()
            edit_group_select_next_btn.update()
        if edit_group_deselect_btn is not None:
            if edit_enabled:
                edit_group_deselect_btn.enable()
            else:
                edit_group_deselect_btn.disable()
            edit_group_deselect_btn.update()
        available_members_to_add = [
            addr for addr in state["recipients"] if addr not in edit_group_editor["members"]
        ]
        if edit_group_add_members_select is not None:
            current_add_selection = getattr(edit_group_add_members_select, "value", []) or []
            filtered_selection = [addr for addr in current_add_selection if addr in available_members_to_add]
            edit_group_add_members_select.options = available_members_to_add
            edit_group_add_members_select.value = filtered_selection
            if edit_enabled:
                edit_group_add_members_select.enable()
            else:
                edit_group_add_members_select.disable()
            edit_group_add_members_select.update()
        _refresh_edit_group_add_members_button()
        if edit_group_member_editor_list is not None:
            edit_group_member_editor_list.clear()
            with edit_group_member_editor_list:
                if not edit_enabled:
                    ui.label("Select an existing group to manage its members.").classes("text-body2 text-grey-7")
                elif not edit_group_editor["members"]:
                    ui.label("This group currently has no members.").classes("text-body2 text-grey-7")
                else:
                    for addr in edit_group_editor["members"]:
                        with ui.card().classes("w-full p-2"):
                            with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon("mail").classes("text-primary")
                                    ui.label(addr).classes("text-body2")
                                ui.button(
                                    "Remove",
                                    icon="person_remove",
                                    color="warning",
                                    on_click=lambda _=None, target_addr=addr: _remove_member_from_edit_group(target_addr),
                                ).props("outline")
        for event_key, toggle in edit_group_event_toggles.items():
            toggle.value = bool(edit_group_editor["event_prefs"].get(event_key, True))
            if edit_enabled:
                toggle.enable()
            else:
                toggle.disable()
            toggle.update()
        if edit_group_review_label is not None:
            enabled_events = [
                EVENT_LABELS[event_key]
                for event_key in EVENT_KEYS
                if bool(edit_group_editor["event_prefs"].get(event_key, True))
            ]
            event_summary = ", ".join(enabled_events) if enabled_events else "none"
            if target is None:
                edit_group_review_label.text = "Select an existing group to review and save changes."
            else:
                edit_group_review_label.text = (
                    f"Selected Group: {target} | Members: {len(edit_group_editor['members'])} | Lifecycle: {event_summary}"
                )
            edit_group_review_label.update()
        if edit_group_members_list is not None:
            edit_group_members_list.clear()
            with edit_group_members_list:
                for addr in edit_group_editor["members"]:
                    ui.item(addr)
        if edit_group_reset_btn is not None:
            if edit_enabled:
                edit_group_reset_btn.enable()
            else:
                edit_group_reset_btn.disable()
            edit_group_reset_btn.update()
        if edit_group_save_btn is not None:
            if edit_enabled:
                edit_group_save_btn.enable()
            else:
                edit_group_save_btn.disable()
            edit_group_save_btn.update()
        if edit_delete_group_btn is not None:
            edit_delete_group_btn.set_text(f"Delete Selected Group ({target})" if target else "Delete Selected Group")
            if edit_enabled:
                edit_delete_group_btn.enable()
            else:
                edit_delete_group_btn.disable()
            edit_delete_group_btn.update()
        if edit_group_delete_next_btn is not None:
            if edit_enabled:
                edit_group_delete_next_btn.enable()
            else:
                edit_group_delete_next_btn.disable()
            edit_group_delete_next_btn.update()
        if edit_delete_group_tooltip is not None:
            if target is None:
                edit_delete_group_tooltip.text = "Select an existing group to delete it."
            else:
                edit_delete_group_tooltip.text = (
                    f"Delete selected group '{target}' and remove it from the active group selection."
                )
            edit_delete_group_tooltip.update()

    def _refresh_group_editors() -> None:
        _refresh_create_group_editor()
        _refresh_edit_group_editor()

    def _reset_create_group_editor() -> None:
        _restore_group_editor_state(create_group_editor, _default_group_editor_state())
        _refresh_create_group_editor()
        _set_create_group_step("name")

    def _load_edit_group(name: Optional[str], *, step_name: str = "select") -> None:
        nonlocal loaded_group_name
        if not name or name not in state["groups"]:
            loaded_group_name = None
            _restore_group_editor_state(edit_group_editor, _default_group_editor_state())
        else:
            next_state, _ = _resolve_group_editor_state(state["groups"], state["group_prefs"], name)
            loaded_group_name = name
            _restore_group_editor_state(edit_group_editor, next_state)
        _refresh_edit_group_editor()
        _set_edit_group_step(step_name)

    def _reset_edit_group_editor() -> None:
        _load_edit_group(None, step_name="select")

    def _request_group_tab_change(target_tab: str) -> None:
        source_tab = current_group_tab
        if target_tab == current_group_tab:
            _set_group_tab(target_tab)
            return
        if source_tab == "create":
            _collect_create_group_form()
            dirty = _is_group_editor_dirty(create_group_editor, state["groups"], state["group_prefs"])
        else:
            _collect_edit_group_form()
            dirty = _is_group_editor_dirty(edit_group_editor, state["groups"], state["group_prefs"])
        if not dirty:
            _set_group_tab(target_tab)
            return
        _set_group_tab(source_tab)

        def _discard_and_switch_group_tab() -> None:
            if source_tab == "create":
                _reset_create_group_editor()
            else:
                _reset_edit_group_editor()
            _set_group_tab(target_tab)

        _confirm_group_editor_action(
            title="Discard unsaved group changes?",
            message="Switching tabs will discard the current unsaved draft.",
            confirm_label="Discard and switch",
            action=_discard_and_switch_group_tab,
        )

    def _on_group_tab_change(event: events.GenericEventArguments) -> None:
        target_tab = str(_event_model_value(event) or current_group_tab)
        _request_group_tab_change(target_tab)

    def _request_create_group_reset() -> None:
        _collect_create_group_form()
        if not _is_group_editor_dirty(create_group_editor, state["groups"], state["group_prefs"]):
            _reset_create_group_editor()
            return
        _confirm_group_editor_action(
            title="Discard unsaved group changes?",
            message="Resetting the new-group form will discard the current draft.",
            confirm_label="Discard and reset",
            action=_reset_create_group_editor,
        )

    def _request_edit_group_reset() -> None:
        _collect_edit_group_form()
        if not _is_group_editor_dirty(edit_group_editor, state["groups"], state["group_prefs"]):
            _reset_edit_group_editor()
            return
        _confirm_group_editor_action(
            title="Discard unsaved group changes?",
            message="Resetting the editor will discard the current changes to the selected group.",
            confirm_label="Discard and reset",
            action=_reset_edit_group_editor,
        )

    def _request_edit_group_load(target: Optional[str]) -> None:
        if not target or target not in state["groups"]:
            notify_user("Choose an existing group to select", kind="warning")
            return
        _collect_edit_group_form()
        dirty = _is_group_editor_dirty(edit_group_editor, state["groups"], state["group_prefs"])
        if not dirty:
            _load_edit_group(target, step_name="select")
            return
        load_message = (
            f"Selecting '{target}' will discard the current unsaved changes."
            if target != loaded_group_name
            else f"Selecting '{target}' again will discard the current unsaved changes."
        )
        _confirm_group_editor_action(
            title="Discard unsaved group changes?",
            message=load_message,
            confirm_label="Discard and select",
            action=lambda target=target: _load_edit_group(target, step_name="select"),
        )

    def _request_edit_group_unload() -> None:
        if loaded_group_name is None:
            _load_edit_group(None, step_name="select")
            return
        _collect_edit_group_form()
        dirty = _is_group_editor_dirty(edit_group_editor, state["groups"], state["group_prefs"])
        if not dirty:
            _load_edit_group(None, step_name="select")
            return
        _confirm_group_editor_action(
            title="Discard unsaved group changes?",
            message="Deselecting the current group will discard the unsaved draft.",
            confirm_label="Discard and deselect",
            action=lambda: _load_edit_group(None, step_name="select"),
        )

    def _make_edit_group_load_handler(target: str) -> Callable[[events.ClickEventArguments], None]:
        def _handle_click(_: events.ClickEventArguments) -> None:
            _request_edit_group_load(target)

        return _handle_click

    def _advance_create_group_from_name() -> None:
        _finalize_create_group_name_input()
        name, _, _ = _collect_create_group_form()
        validation_error = _validate_group_name(name, state["groups"])
        _refresh_create_group_name_status(candidate=name)
        if validation_error is not None:
            notify_user(validation_error, kind="warning")
            return
        _set_create_group_step("members")

    def _summarize_email_config(email_cfg_obj: Any) -> str:
        try:
            return (
                "EmailConfig(summary): "
                f"recipients={len(getattr(email_cfg_obj, 'recipients', []) or [])}, "
                f"groups={len(getattr(email_cfg_obj, 'groups', {}) or {})}, "
                f"active_groups={len(getattr(email_cfg_obj, 'active_groups', []) or [])}, "
                f"static_recipients={len(getattr(email_cfg_obj, 'static_recipients', []) or [])}, "
                f"smtp_server={getattr(email_cfg_obj, 'smtp_server', '<unknown>')}, "
                f"sender={getattr(email_cfg_obj, 'sender_email', '<unknown>')}"
            )
        except Exception:
            return "EmailConfig(summary): <error building summary>"

    def _notify_persist_failure(result: PersistResult) -> None:
        notify_user(result.message, kind="negative")

    def persist_state(*, routing_mutator: Optional[Callable[[EmailConfig], None]] = None) -> PersistResult:
        current_cfg = get_global_config()
        if not current_cfg:
            logger.error("Global config not available for saving email settings")
            return PersistResult(False, "Configuration not available")
        email_snapshot = _snapshot_email_config_state(current_cfg.email)
        try:
            _ensure_address_book()
            current_cfg.email.recipients = list(state["recipients"])
            current_cfg.email.smtp_server = state["smtp"]["server"]
            current_cfg.email.smtp_port = int(state["smtp"]["port"])
            current_cfg.email.sender_email = state["smtp"]["sender"]
            current_cfg.email.groups = {name: list(members) for name, members in state["groups"].items()}
            current_cfg.email.static_recipients = list(state["static_recipients"])
            current_cfg.email.group_prefs = {
                name: _event_prefs(state["group_prefs"].get(name), default=True)
                for name in state["groups"].keys()
            }
            if routing_mutator is not None:
                routing_mutator(current_cfg.email)
            _finalize_structural_email_config(current_cfg.email)
            _sync_state_from_email_cfg(current_cfg.email)

            if save_global_config():
                if email_system is not None:
                    email_system.refresh_config()
                logger.info(_summarize_email_config(current_cfg.email))
                return PersistResult(True, "Email settings saved")
            _restore_email_config_state(current_cfg.email, email_snapshot)
            _sync_state_from_email_cfg(current_cfg.email)
            logger.error("Failed to save configuration")
            return PersistResult(False, "Failed to save email settings")
        except Exception as exc:
            _restore_email_config_state(current_cfg.email, email_snapshot)
            _sync_state_from_email_cfg(current_cfg.email)
            logger.error("Failed to save configuration: %s", exc, exc_info=True)
            return PersistResult(False, f"Failed to save email settings: {exc}")

    def is_valid_email(addr: str) -> bool:
        return bool(EMAIL_RE.match(addr))

    def validate_smtp(cfg_obj: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not is_valid_email(cfg_obj.get("sender", "")):
            errors.append("Email address is invalid.")
        if not cfg_obj.get("server"):
            errors.append("SMTP server must not be empty.")
        port = cfg_obj.get("port")
        if not isinstance(port, (int, float)) or not 1 <= int(port) <= 65535:
            errors.append("Port must be between 1 and 65535.")
        return errors

    async def send_async_test_email(client: Client, btn: ui.button) -> None:
        btn.props("loading")
        try:
            if email_system is None:
                with client:
                    notify_user("Email system not initialized", kind="negative")
                return
            persist_result = persist_state()
            if not persist_result.ok:
                with client:
                    notify_user(persist_result.message, kind="negative")
                return
            cfg = get_global_config()
            recipients_to_log = _get_effective_recipients_from_config(cfg, state)
            with client:
                notify_user(f"Sending test email to {', '.join(recipients_to_log)}", kind="info")
            success = await email_system.send_test_email_async()
            with client:
                if success:
                    notify_user("Test email sent successfully", kind="positive")
                else:
                    notify_user("Error sending test email", kind="negative")
        finally:
            btn.props(remove="loading")

    def send_test_email(btn: ui.button) -> None:
        cfg = get_global_config()
        effective = _get_effective_recipients_from_config(cfg, state)
        if not effective:
            notify_user("Can't send test email: no effective recipients configured", kind="warning")
            return
        client: Client = ui.context.client
        schedule_bg(send_async_test_email(client, btn), name="send_test_email")

    def _toggle_static_recipient(addr: str, enabled: Any) -> None:
        snapshot = _snapshot_state()
        is_enabled = bool(enabled)
        if is_enabled:
            if addr not in state["static_recipients"]:
                state["static_recipients"].append(addr)
        else:
            state["static_recipients"] = [item for item in state["static_recipients"] if item != addr]

        result = persist_state()
        if result.ok:
            refresh_recipient_table()
            refresh_overview()
            action = "marked as static" if is_enabled else "removed from static recipients"
            notify_user(f"{addr} {action}", kind="positive")
            return

        _restore_state(snapshot)
        refresh_recipient_table()
        refresh_overview()
        _notify_persist_failure(result)

    def add_recipient() -> None:
        if email_inp is None:
            return
        addr = (email_inp.value or "").strip()
        if not is_valid_email(addr):
            notify_user("Invalid email address", kind="negative")
            return
        if addr in state["recipients"]:
            notify_user("Address already exists", kind="warning")
            return
        snapshot = _snapshot_state()
        state["recipients"].append(addr)
        email_inp.value = ""
        result = persist_state()
        if result.ok:
            refresh_recipient_table()
            _refresh_group_editors()
            refresh_overview()
            notify_user(f"Added recipient {addr}", kind="positive")
            return

        _restore_state(snapshot)
        refresh_recipient_table()
        _refresh_group_editors()
        refresh_overview()
        _notify_persist_failure(result)

    def delete_selected() -> None:
        if recipient_table is None:
            return
        selected_rows = recipient_table.selected or []
        if not selected_rows:
            return
        snapshot = _snapshot_state()
        create_editor_snapshot = _snapshot_group_editor_state(create_group_editor)
        edit_editor_snapshot = _snapshot_group_editor_state(edit_group_editor)
        selected = [row["address"] for row in selected_rows]
        state["recipients"] = [addr for addr in state["recipients"] if addr not in selected]
        state["static_recipients"] = [addr for addr in state["static_recipients"] if addr not in selected]
        create_group_editor["members"] = [addr for addr in create_group_editor["members"] if addr not in selected]
        edit_group_editor["members"] = [addr for addr in edit_group_editor["members"] if addr not in selected]
        for group_name, members in list(state["groups"].items()):
            state["groups"][group_name] = [addr for addr in members if addr not in selected]
        result = persist_state(routing_mutator=lambda email_cfg_obj: _delete_recipient_routing_refs(email_cfg_obj, selected))
        if result.ok:
            refresh_recipient_table()
            recipient_table.selected = []
            _refresh_group_editors()
            refresh_overview()
            notify_user(f"Deleted {len(selected)} recipient(s)", kind="positive")
            return

        _restore_state(snapshot)
        _restore_group_editor_state(create_group_editor, create_editor_snapshot)
        _restore_group_editor_state(edit_group_editor, edit_editor_snapshot)
        refresh_recipient_table()
        _refresh_group_editors()
        refresh_overview()
        _notify_persist_failure(result)

    def rename_recipient(e: events.GenericEventArguments) -> None:
        try:
            old_addr, new_addr = extract_rename_addresses(e.args)
            if not old_addr or not new_addr:
                return
            if not is_valid_email(new_addr):
                notify_user(f"Invalid email address: {new_addr}", kind="negative")
                refresh_recipient_table()
                return
            if new_addr in state["recipients"] and new_addr != old_addr:
                notify_user(f"Address already exists: {new_addr}", kind="warning")
                refresh_recipient_table()
                return
            snapshot = _snapshot_state()
            create_editor_snapshot = _snapshot_group_editor_state(create_group_editor)
            edit_editor_snapshot = _snapshot_group_editor_state(edit_group_editor)
            idx = state["recipients"].index(old_addr)
            state["recipients"][idx] = new_addr
            state["static_recipients"] = [new_addr if addr == old_addr else addr for addr in state["static_recipients"]]
            create_group_editor["members"] = [new_addr if addr == old_addr else addr for addr in create_group_editor["members"]]
            edit_group_editor["members"] = [new_addr if addr == old_addr else addr for addr in edit_group_editor["members"]]
            for group_name, members in state["groups"].items():
                state["groups"][group_name] = [new_addr if addr == old_addr else addr for addr in members]
            result = persist_state(
                routing_mutator=lambda email_cfg_obj: _rename_recipient_routing_refs(email_cfg_obj, old_addr, new_addr)
            )
            if result.ok:
                refresh_recipient_table()
                _refresh_group_editors()
                refresh_overview()
                notify_user(f"Renamed {old_addr} to {new_addr}", kind="positive")
                return

            _restore_state(snapshot)
            _restore_group_editor_state(create_group_editor, create_editor_snapshot)
            _restore_group_editor_state(edit_group_editor, edit_editor_snapshot)
            refresh_recipient_table()
            _refresh_group_editors()
            refresh_overview()
            _notify_persist_failure(result)
        except ValueError:
            refresh_recipient_table()
        except Exception:
            logger.exception("Error renaming recipient")
            refresh_recipient_table()

    def save_new_group() -> None:
        name, members, event_prefs = _collect_create_group_form()
        validation_error = _validate_group_name(name, state["groups"])
        _refresh_create_group_name_status()
        if validation_error is not None:
            notify_user(validation_error, kind="warning")
            return
        snapshot = _snapshot_state()
        create_editor_snapshot = _snapshot_group_editor_state(create_group_editor)
        state["groups"][name] = members
        state["group_prefs"][name] = _event_prefs(event_prefs, default=True)
        result = persist_state()
        if result.ok:
            _reset_create_group_editor()
            refresh_recipient_table()
            refresh_overview()
            _refresh_group_editors()
            notify_user(f"Group '{name}' created", kind="positive")
            return

        _restore_state(snapshot)
        _restore_group_editor_state(create_group_editor, create_editor_snapshot)
        _refresh_group_editors()
        refresh_recipient_table()
        refresh_overview()
        _notify_persist_failure(result)

    def save_existing_group() -> None:
        nonlocal loaded_group_name
        target = loaded_group_name
        if not target or target not in state["groups"]:
            notify_user("Load an existing group before saving changes", kind="warning")
            return
        _, members, event_prefs = _collect_edit_group_form()
        snapshot = _snapshot_state()
        edit_editor_snapshot = _snapshot_group_editor_state(edit_group_editor)
        loaded_snapshot = loaded_group_name
        state["groups"][target] = members
        state["group_prefs"][target] = _event_prefs(event_prefs, default=True)
        result = persist_state()
        if result.ok:
            _load_edit_group(None, step_name="select")
            refresh_recipient_table()
            refresh_overview()
            notify_user(f"Group '{target}' updated", kind="positive")
            return

        _restore_state(snapshot)
        loaded_group_name = loaded_snapshot
        _restore_group_editor_state(edit_group_editor, edit_editor_snapshot)
        _refresh_group_editors()
        refresh_recipient_table()
        refresh_overview()
        _notify_persist_failure(result)

    def _execute_delete_existing_group(target: str) -> None:
        nonlocal loaded_group_name
        snapshot = _snapshot_state()
        edit_editor_snapshot = _snapshot_group_editor_state(edit_group_editor)
        loaded_snapshot = loaded_group_name
        state["groups"].pop(target, None)
        state["group_prefs"].pop(target, None)
        result = persist_state(routing_mutator=lambda email_cfg_obj: _delete_group_routing_refs(email_cfg_obj, target))
        if result.ok:
            _load_edit_group(None, step_name="select")
            refresh_recipient_table()
            refresh_overview()
            notify_user(f"Group '{target}' deleted", kind="positive")
            return

        _restore_state(snapshot)
        loaded_group_name = loaded_snapshot
        _restore_group_editor_state(edit_group_editor, edit_editor_snapshot)
        _refresh_group_editors()
        refresh_recipient_table()
        refresh_overview()
        _notify_persist_failure(result)

    def delete_existing_group() -> None:
        _collect_edit_group_form()
        target = _resolve_group_delete_target(edit_group_editor, state["groups"])
        if not target or target not in state["groups"]:
            notify_user("No existing group loaded", kind="warning")
            return
        if _is_group_editor_dirty(edit_group_editor, state["groups"], state["group_prefs"]):
            _confirm_group_editor_action(
                title="Delete selected group?",
                message=f"Delete selected group '{target}'. Unsaved changes to this group will be discarded.",
                confirm_label=f"Delete '{target}'",
                action=lambda target=target: _execute_delete_existing_group(target),
            )
            return
        _execute_delete_existing_group(target)

    with ui.column().classes("w-full gap-6"):
        with ui.card().classes("w-full p-4 gap-4"):
            create_heading_row(
                "Overview",
                icon="dashboard",
                title_classes="text-h6 font-bold",
                row_classes="items-center gap-2",
                icon_classes="text-primary text-xl shrink-0",
            )
            ui.label(
                "Static recipients are managed here as an always-active background target. Active groups for the current run are selected in Measurement settings or the dashboard."
            ).classes("text-body2 text-grey-7").tooltip(EMAIL_TOOLTIP_TEXTS["routing_hint"])
            with ui.grid(columns=4).classes("w-full gap-3").style("grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));"):
                summary_cards = [
                    ("Address Book", "recipients"),
                    ("Groups", "groups"),
                    ("Active Groups", "active_groups"),
                    ("Static Recipients", "static_recipients"),
                    ("Effective Recipients", "effective_total"),
                    ("Start Targets", "start_count"),
                    ("End Targets", "end_count"),
                    ("Stop Targets", "stop_count"),
                ]
                for title, key in summary_cards:
                    with ui.card().classes("p-3 gap-1") as summary_card:
                        ui.label(title).classes("text-caption text-grey-7")
                        overview_counts[key] = ui.label("0").classes("text-h6 font-semibold")
                    summary_card.tooltip(OVERVIEW_CARD_TOOLTIPS[key])
            with ui.row().classes("items-center gap-4 flex-wrap"):
                for event_key in EVENT_KEYS:
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{EVENT_LABELS[event_key]}:").classes("text-subtitle2 text-grey-7")
                        notification_labels[event_key] = ui.label("").classes("text-caption text-grey")
                ui.space()
                test_email_btn: ui.button = ui.button(
                    "Send test email",
                    icon="send",
                    color="info",
                    on_click=lambda: send_test_email(test_email_btn),
                ).props("unelevated")
                test_email_btn.tooltip(EMAIL_TOOLTIP_TEXTS["test_email"])
            overview_table = ui.table(
                columns=[
                    {"name": "address", "label": "Address", "field": "address", "align": "left"},
                    {"name": "sources", "label": "Active Sources", "field": "sources", "align": "left"},
                    {"name": "on_start", "label": "Start", "field": "on_start", "align": "center"},
                    {"name": "on_end", "label": "End", "field": "on_end", "align": "center"},
                    {"name": "on_stop", "label": "Stop", "field": "on_stop", "align": "center"},
                ],
                rows=[],
                    row_key="address",
                    pagination={"rowsPerPage": 8},
                ).classes("w-full").props("dense flat bordered")
            overview_table.tooltip(EMAIL_TOOLTIP_TEXTS["overview_table"])

        with ui.row().classes("w-full items-center justify-between"):
            create_heading_row(
                "Address Book",
                icon="alternate_email",
                title_classes="text-h6",
                row_classes="items-center gap-2",
                icon_classes="text-primary text-xl shrink-0",
            )
            delete_btn = ui.button("Delete Selected", icon="delete", color="negative", on_click=delete_selected)
            delete_btn.disable()
            delete_btn.tooltip(EMAIL_TOOLTIP_TEXTS["delete_selected"])

        with ui.row().classes("w-full gap-2"):
            email_inp = ui.input("Add Email").classes("flex-grow").on("keydown.enter", add_recipient)
            email_inp.tooltip(EMAIL_TOOLTIP_TEXTS["address_input"])
            ui.button("Add", icon="add", on_click=add_recipient).tooltip(EMAIL_TOOLTIP_TEXTS["add_address"])
        ui.label("Use the Static column to mark recipients that should always receive emails.").classes("text-caption text-grey-7")

        recipient_table = ui.table(
            columns=[
                {"name": "address", "label": "Address", "field": "address", "align": "left", "sortable": True},
                {"name": "static", "label": "Static", "field": "static_enabled", "align": "center"},
                {"name": "groups", "label": "Groups", "field": "groups", "align": "left"},
                {"name": "active_groups", "label": "Active Groups", "field": "active_groups", "align": "left"},
            ],
            rows=[],
            row_key="address",
            selection="multiple",
            pagination={"rowsPerPage": 10},
        ).classes("w-full").props("dense flat bordered")
        recipient_table.tooltip(EMAIL_TOOLTIP_TEXTS["recipient_table"])
        recipient_table.add_slot(
            "body-cell-address",
            r'''
            <q-td :props="props">
                {{ props.row.address }}
                <q-icon name="edit" size="xs" class="q-ml-xs text-grey-6">
                    <q-tooltip>Click the address to rename this entry.</q-tooltip>
                </q-icon>
                <q-popup-edit v-model="props.row.address" v-slot="scope"
                    @save="(val, initialValue) => $parent.$emit('rename', { oldAddress: initialValue, newAddress: val })">
                    <q-input v-model="scope.value" dense autofocus @keyup.enter="scope.set" />
                </q-popup-edit>
            </q-td>
            ''',
        )
        recipient_table.add_slot(
            "body-cell-static",
            r'''
            <q-td :props="props">
                <q-checkbox v-model="props.row.static_enabled" dense
                    @update:model-value="() => $parent.$emit('toggle-static', props.row.address, props.row.static_enabled)">
                    <q-tooltip>Always send emails to this recipient, even when no matching group is active.</q-tooltip>
                </q-checkbox>
            </q-td>
            ''',
        )
        recipient_table.on("selection", lambda e: delete_btn.enable() if e.args else delete_btn.disable())
        recipient_table.on("rename", lambda e: rename_recipient(e))
        recipient_table.on("toggle-static", lambda e: _toggle_static_recipient(e.args[0], e.args[1]))

        ui.separator()

        create_heading_row(
            "Groups",
            icon="groups",
            title_classes="text-h6",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-xl shrink-0",
        )
        ui.label(
            "Create new groups and edit existing groups. Existing groups are loaded explicitly into the edit tab."
        ).classes(
            "text-body2 text-grey-7"
        )

        discard_group_dialog = ui.dialog().classes("items-center justify-center")
        with discard_group_dialog:
            with ui.card().classes("w-[460px] max-w-full"):
                discard_group_title_label = ui.label("Discard unsaved group changes?").classes("text-h6")
                discard_group_message_label = ui.label("").classes("text-body1")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Keep editing", on_click=_cancel_pending_group_action).props("flat")
                    discard_group_confirm_btn = ui.button("Discard changes", on_click=_run_pending_group_action).props(
                        "color=warning"
                    )

        with ui.tabs().classes("w-full") as group_mode_tabs:
            create_group_tab = ui.tab("create", label="Create Group", icon="add_circle")
            ui.tab("edit", label="Edit Existing Group", icon="edit")
        if group_mode_tabs is not None and create_group_tab is not None:
            group_mode_tabs.value = create_group_tab
            group_mode_tabs.update()
        group_mode_tabs.on("update:model-value", _on_group_tab_change)

        with ui.tab_panels(group_mode_tabs, value=create_group_tab).props("animated keep-alive").classes("w-full"):
            with ui.tab_panel("create").classes("w-full px-0"):
                ui.label(
                    "Create new groups. This tab always creates a new group."
                ).classes("text-body2 text-grey-7")
                with ui.stepper(value="name").props("vertical flat animated").classes("w-full") as create_group_stepper:
                    with ui.step("name", title="Group name", icon="badge"):
                        ui.label("Step 1: Define a new unique group name.").classes("text-body2")
                        create_group_name_input = (
                            ui.input("Group Name", value=create_group_editor["name"])
                            .classes("w-full")
                            .props("outlined maxlength=20")
                        )
                        create_group_name_input.tooltip(EMAIL_TOOLTIP_TEXTS["group_name"])
                        create_group_name_input.on("input", _on_create_group_name_input)
                        create_group_name_input.on(
                            "update:model-value",
                            _on_create_group_name_input,
                        )
                        create_group_name_input.on("blur", lambda _: _finalize_create_group_name_input())
                        create_group_name_status_label = ui.label("").classes("text-caption text-grey-7")
                        with ui.row().classes("w-full justify-end gap-2 mt-2"):
                            create_group_next_btn = ui.button("Next", icon="arrow_forward", on_click=_advance_create_group_from_name).props(
                                "color=primary"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_next"])

                    with ui.step("members", title="Select members", icon="group_add"):
                        ui.label("Step 2: Choose the address-book entries that belong to the new group.").classes("text-body2")
                        create_group_members_select = ui.select(
                            options=list(state["recipients"]),
                            value=list(create_group_editor["members"]),
                            label="Group Members",
                            multiple=True,
                        ).classes("w-full").props("outlined use-chips")
                        create_group_members_select.tooltip(EMAIL_TOOLTIP_TEXTS["group_members"])
                        create_group_members_select.on("update:model-value", lambda _: _collect_create_group_form())
                        with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                            ui.button("Back", icon="arrow_back", on_click=lambda: _set_create_group_step("name")).props(
                                "flat no-caps"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_select"])
                            ui.button(
                                "Next",
                                icon="arrow_forward",
                                on_click=lambda: (_collect_create_group_form(), _refresh_create_group_editor(), _set_create_group_step("events")),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_events"])

                    with ui.step("events", title="Lifecycle permissions", icon="event_available"):
                        ui.label("Step 3: Decide which lifecycle emails this new group may receive while active.").classes(
                            "text-body2"
                        )
                        with ui.card().classes("w-full p-3 gap-2"):
                            for event_key in EVENT_KEYS:
                                create_group_event_toggles[event_key] = ui.checkbox(
                                    f"Allow {EVENT_LABELS[event_key]} emails",
                                    value=bool(create_group_editor["event_prefs"].get(event_key, True)),
                                ).classes("self-start")
                                create_group_event_toggles[event_key].tooltip(EMAIL_TOOLTIP_TEXTS["group_events"])
                                create_group_event_toggles[event_key].on(
                                    "update:model-value",
                                    lambda _: _collect_create_group_form(),
                                )
                        with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                            ui.button("Back", icon="arrow_back", on_click=lambda: _set_create_group_step("members")).props(
                                "flat no-caps"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_events"])
                            ui.button(
                                "Review",
                                icon="arrow_forward",
                                on_click=lambda: (_collect_create_group_form(), _refresh_create_group_editor(), _set_create_group_step("review")),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_review"])

                    with ui.step("review", title="Review and create", icon="task_alt"):
                        ui.label("Step 4: Review the new group before saving it.").classes("text-body2")
                        create_group_review_label = ui.label("").classes("text-body2 text-grey-8")
                        with ui.card().classes("w-full p-3 gap-2"):
                            ui.label("Members").classes("text-subtitle2")
                            create_group_members_list = ui.list().props("dense separator")
                        with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                            with ui.row().classes("gap-2 flex-wrap"):
                                ui.button("Back", icon="arrow_back", on_click=lambda: _set_create_group_step("events")).props(
                                    "flat no-caps"
                                ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_review"])
                                ui.button("Reset", icon="restart_alt", on_click=_request_create_group_reset).props(
                                    "outline"
                                ).tooltip(EMAIL_TOOLTIP_TEXTS["group_reset"])
                            ui.button("Create Group", icon="save", color="primary", on_click=save_new_group).tooltip(
                                EMAIL_TOOLTIP_TEXTS["group_save"]
                            )

            with ui.tab_panel("edit").classes("w-full px-0"):
                ui.label(
                    "Edit only existing groups here. The group name stays fixed; you can change members and lifecycle permissions."
                ).classes("text-body2 text-grey-7")
                with ui.stepper(value="select").props("vertical flat animated").classes("w-full") as edit_group_stepper:
                    with ui.step("select", title="Select group", icon="playlist_add_check_circle"):
                        ui.label(
                            "Step 1: Select which existing group you want to edit. You can also deselect the current group here."
                        ).classes("text-body2")
                        with ui.card().classes("w-full p-3 gap-3"):
                            ui.label("Existing Groups").classes("text-subtitle2")
                            ui.label("Use the list below to select a group for editing.").classes("text-caption text-grey-7")
                            edit_group_browser = ui.column().classes("w-full gap-2")
                            edit_group_browser.tooltip(EMAIL_TOOLTIP_TEXTS["group_select"])
                        edit_group_loaded_label = ui.label("Selected Group: -").classes("text-subtitle2")
                        edit_group_hint_label = ui.label("").classes("text-caption text-grey-7")
                        with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                            edit_group_deselect_btn = ui.button(
                                "Deselect Group",
                                icon="close",
                                on_click=_request_edit_group_unload,
                            ).props("outline")
                            edit_group_deselect_btn.tooltip(EMAIL_TOOLTIP_TEXTS["group_unload"])
                            edit_group_select_next_btn = ui.button(
                                "Next",
                                icon="arrow_forward",
                                on_click=lambda: _set_edit_group_step("delete"),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_next"])

                    with ui.step("delete", title="Delete group", icon="delete_forever"):
                        ui.label(
                            "Step 2: Delete the selected group if you no longer need it. Otherwise continue to member changes."
                        ).classes("text-body2")
                        with ui.card().classes("w-full p-3 gap-2"):
                            with ui.row().classes("items-start gap-3"):
                                ui.icon("warning_amber").classes("text-negative text-2xl mt-1")
                                with ui.column().classes("gap-1"):
                                    ui.label("Delete the whole group").classes("text-subtitle2")
                                    ui.label(
                                        "This removes the selected group from the configuration and from the active group selection."
                                    ).classes("text-body2 text-grey-7")
                            with ui.row().classes("w-full justify-end mt-2"):
                                edit_delete_group_btn = ui.button(
                                    "Delete Selected Group",
                                    icon="delete",
                                    color="negative",
                                    on_click=delete_existing_group,
                                )
                                with edit_delete_group_btn:
                                    edit_delete_group_tooltip = ui.tooltip(EMAIL_TOOLTIP_TEXTS["group_delete"])
                        with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                            ui.button("Back", icon="arrow_back", on_click=lambda: _set_edit_group_step("select")).props(
                                "flat no-caps"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_choose"])
                            edit_group_delete_next_btn = ui.button(
                                "Next",
                                icon="arrow_forward",
                                on_click=lambda: _set_edit_group_step("members"),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_next"])

                    with ui.step("members", title="Manage members", icon="groups_2"):
                        ui.label("Step 3: Add new members or remove existing members from the selected group.").classes(
                            "text-body2"
                        )
                        with ui.card().classes("w-full p-3 gap-2"):
                            ui.label("Add Members").classes("text-subtitle2")
                            ui.label(
                                "Choose address-book entries to add to this group. Added members stay in the draft until you save."
                            ).classes("text-caption text-grey-7")
                            edit_group_add_members_select = ui.select(
                                options=[],
                                value=[],
                                label="Recipients to Add",
                                multiple=True,
                            ).classes("w-full").props("outlined use-chips")
                            edit_group_add_members_select.on(
                                "update:model-value",
                                lambda _: _refresh_edit_group_add_members_button(),
                            )
                            with ui.row().classes("w-full justify-end"):
                                edit_group_add_members_btn = ui.button(
                                    "Add Selected Members",
                                    icon="person_add",
                                    color="primary",
                                    on_click=_add_members_to_edit_group,
                                ).props("outline")
                        with ui.card().classes("w-full p-3 gap-2"):
                            ui.label("Current Members").classes("text-subtitle2")
                            ui.label(
                                "Remove individual members with the buttons below. The change stays in the draft until you save it in the review step."
                            ).classes("text-caption text-grey-7")
                            edit_group_member_editor_list = ui.column().classes("w-full gap-2")
                        with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                            ui.button("Back", icon="arrow_back", on_click=lambda: _set_edit_group_step("delete")).props(
                                "flat no-caps"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_delete"])
                            ui.button(
                                "Next",
                                icon="arrow_forward",
                                on_click=lambda: (_collect_edit_group_form(), _refresh_edit_group_editor(), _set_edit_group_step("events")),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_events"])

                    with ui.step("events", title="Notification settings", icon="notifications_active"):
                        ui.label("Step 4: Adjust which lifecycle emails the selected group may receive while active.").classes(
                            "text-body2"
                        )
                        with ui.card().classes("w-full p-3 gap-2"):
                            for event_key in EVENT_KEYS:
                                edit_group_event_toggles[event_key] = ui.checkbox(
                                    f"Allow {EVENT_LABELS[event_key]} emails",
                                    value=bool(edit_group_editor["event_prefs"].get(event_key, True)),
                                ).classes("self-start")
                                edit_group_event_toggles[event_key].tooltip(EMAIL_TOOLTIP_TEXTS["group_events"])
                                edit_group_event_toggles[event_key].on(
                                    "update:model-value",
                                    lambda _: _collect_edit_group_form(),
                                )
                        with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                            ui.button("Back", icon="arrow_back", on_click=lambda: _set_edit_group_step("members")).props(
                                "flat no-caps"
                            ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_events"])
                            ui.button(
                                "Review",
                                icon="arrow_forward",
                                on_click=lambda: (_collect_edit_group_form(), _refresh_edit_group_editor(), _set_edit_group_step("review")),
                            ).props("color=primary").tooltip(EMAIL_TOOLTIP_TEXTS["group_review"])

                    with ui.step("review", title="Review changes", icon="fact_check"):
                        ui.label("Step 5: Review the selected group and either save or discard your changes.").classes("text-body2")
                        edit_group_review_label = ui.label("").classes("text-body2 text-grey-8")
                        with ui.card().classes("w-full p-3 gap-2"):
                            ui.label("Members").classes("text-subtitle2")
                            edit_group_members_list = ui.list().props("dense separator")
                        with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                            with ui.row().classes("gap-2 flex-wrap"):
                                ui.button("Back", icon="arrow_back", on_click=lambda: _set_edit_group_step("events")).props(
                                    "flat no-caps"
                                ).tooltip(EMAIL_TOOLTIP_TEXTS["group_back_review"])
                                edit_group_reset_btn = ui.button(
                                    "Discard Changes", icon="undo", on_click=_request_edit_group_reset
                                ).props("outline")
                                edit_group_reset_btn.tooltip(EMAIL_TOOLTIP_TEXTS["group_reset"])
                            with ui.row().classes("gap-2 flex-wrap"):
                                edit_group_save_btn = ui.button(
                                    "Save Changes",
                                    icon="save",
                                    color="primary",
                                    on_click=save_existing_group,
                                )
                                edit_group_save_btn.tooltip(EMAIL_TOOLTIP_TEXTS["group_save"])

        ui.separator()

        create_heading_row(
            "SMTP",
            icon="mail",
            title_classes="text-subtitle1 font-semibold",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-lg shrink-0",
        )
        with ui.column().classes("w-full gap-2"):
            ui.label("Edit E-Mail-SMTP settings.").classes("text-body2 text-grey-7")
            with ui.row().classes("items-end gap-2 w-full flex-wrap"):
                sender_inp = (
                    ui.input("Sender")
                    .bind_value(state["smtp"], "sender")
                    .tooltip(EMAIL_TOOLTIP_TEXTS["smtp_sender"])
                    .classes("min-w-[220px]")
                )
                server_inp = (
                    ui.input("Server")
                    .bind_value(state["smtp"], "server")
                    .tooltip(EMAIL_TOOLTIP_TEXTS["smtp_server"])
                    .classes("min-w-[220px]")
                )
                port_inp = (
                    ui.number("Port", min=1, max=65535)
                    .bind_value(state["smtp"], "port", forward=int)
                    .tooltip(EMAIL_TOOLTIP_TEXTS["smtp_port"])
                    .classes("w-28")
                )
                with ui.row().classes("items-center gap-1"):
                    with ui.icon("check_circle").props("size=md") as status_icon:
                        status_tt = ui.tooltip("")
                    ui.label("Status").classes("text-caption text-grey-7")
            status_icon.tooltip(EMAIL_TOOLTIP_TEXTS["smtp_status"])

        def update_status_icon() -> None:
            errors = validate_smtp(state["smtp"])
            if errors:
                status_icon.props("name=error_outline color=negative")
                status_tt.text = "\n".join(errors)
            else:
                status_icon.props("name=check_circle color=positive")
                status_tt.text = "All SMTP settings valid"
            status_icon.update()

        def manual_save() -> None:
            errors = validate_smtp(state["smtp"])
            if errors:
                notify_user(" ".join(errors), kind="negative")
                return
            result = persist_state()
            if result.ok:
                update_status_icon()
                refresh_overview()
                _refresh_group_editors()
                notify_user("SMTP settings saved", kind="positive")
                return
            _notify_persist_failure(result)

        for inp in (sender_inp, server_inp, port_inp):
            inp.on("update:model-value", lambda _: update_status_icon())
        update_status_icon()

        with ui.row().classes("w-full justify-end mt-1"):
            create_action_button("save", label="Save SMTP", on_click=manual_save, tooltip=EMAIL_TOOLTIP_TEXTS["smtp_save"])

    _ensure_address_book()
    refresh_recipient_table()
    refresh_overview()
    _refresh_group_editors()
    ui.timer(2.0, _refresh_live_routing_snapshot)
