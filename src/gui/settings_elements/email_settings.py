from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Optional, Any

from nicegui import ui, events, Client

from src.config import EmailConfig, get_global_config, save_global_config, get_logger
from src.notify import EMailSystem
from src.gui.util import schedule_bg
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
TEMPLATE_SPECS = (
    ("alert", "Alert", "alert_template"),
    ("test", "Test", "test_template"),
    ("measurement_start", "Measurement Start", "measurement_start_template"),
    ("measurement_end", "Measurement End", "measurement_end_template"),
    ("measurement_stop", "Measurement Stop", "measurement_stop_template"),
)
SUPPORTED_TEMPLATE_PLACEHOLDERS = (
    "{cvd_id}",
    "{cvd_name}",
    "{timestamp}",
    "{website_url}",
    "{session_id}",
    "{last_motion_time}",
    "{start_time}",
    "{end_time}",
    "{duration}",
    "{reason}",
    "{camera_index}",
    "{sensitivity}",
    "{roi_enabled}",
    "{snapshot_note}",
)


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


def _known_recipients(email_cfg: Any) -> list[str]:
    getter = getattr(email_cfg, "get_known_recipients", None)
    if callable(getter):
        return _iterable_str_list(getter())
    return _iterable_str_list(getattr(email_cfg, "recipients", []))


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


def get_template_overview(email_cfg: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, label, resolver_name in TEMPLATE_SPECS:
        subject = ""
        body = ""
        resolver = getattr(email_cfg, resolver_name, None)
        if callable(resolver):
            template = resolver()
            subject = str(getattr(template, "subject", "") or "")
            body = str(getattr(template, "body", "") or "")
        else:
            template_data = (getattr(email_cfg, "templates", {}) or {}).get(key, {})
            if isinstance(template_data, dict):
                subject = str(template_data.get("subject", "") or "")
                body = str(template_data.get("body", "") or "")
        rows.append({"key": key, "label": label, "subject": subject, "body": body})
    return rows


def create_emailcard(*, email_system: Optional[EMailSystem] = None) -> None:
    """Render email setup, address book, and guided group management."""
    config = get_global_config()
    if not config:
        ui.label("Configuration not available").classes("text-red")
        return

    email_cfg = config.email
    recipients = _known_recipients(email_cfg)
    configured_static = _iterable_str_list(getattr(email_cfg, "static_recipients", []))
    active_groups = _iterable_str_list(getattr(email_cfg, "active_groups", []))
    legacy_static = list(recipients) if not configured_static and not active_groups else []

    state: dict[str, Any] = {
        "recipients": list(recipients),
        "smtp": {
            "server": email_cfg.smtp_server,
            "port": email_cfg.smtp_port,
            "sender": email_cfg.sender_email,
        },
        "groups": sanitize_groups_dict(dict(getattr(email_cfg, "groups", {}) or {})),
        "active_groups": list(active_groups),
        "static_recipients": configured_static or legacy_static,
        "notifications": _event_prefs(dict(getattr(email_cfg, "notifications", {}) or {}), default=False),
        "group_prefs": {
            name: _event_prefs((getattr(email_cfg, "group_prefs", {}) or {}).get(name), default=True)
            for name in (getattr(email_cfg, "groups", {}) or {}).keys()
        },
        "recipient_prefs": {
            addr: _event_prefs((getattr(email_cfg, "recipient_prefs", {}) or {}).get(addr), default=True)
            for addr in recipients
        },
    }
    group_editor: dict[str, Any] = {"selected": None, "name": "", "members": []}

    recipient_table: Optional[ui.table] = None
    overview_table: Optional[ui.table] = None
    delete_btn: Optional[ui.button] = None
    email_inp: Optional[ui.input] = None
    group_stepper: Optional[Any] = None
    existing_group_select: Optional[ui.select] = None
    group_name_input: Optional[ui.input] = None
    group_members_select: Optional[ui.select] = None
    group_review_label: Optional[ui.label] = None
    group_members_list: Optional[ui.list] = None
    overview_counts: dict[str, ui.label] = {}
    notification_labels: dict[str, ui.label] = {}

    if email_system is None:
        logger.error("Email system is not initialized, email functionality will be disabled.")

    def _ensure_address_book() -> None:
        merged = list(state["recipients"])
        merged.extend(state["static_recipients"])
        for members in state["groups"].values():
            merged.extend(members or [])
        merged.extend(state["recipient_prefs"].keys())
        state["recipients"] = sanitize_group_addresses(merged)

    def _preview_email_cfg() -> EmailConfig:
        current_cfg = get_global_config()
        if not current_cfg:
            return email_cfg
        current_email_cfg = current_cfg.email
        return EmailConfig(
            website_url=current_email_cfg.website_url,
            recipients=list(state["recipients"]),
            smtp_server=current_email_cfg.smtp_server,
            smtp_port=current_email_cfg.smtp_port,
            sender_email=current_email_cfg.sender_email,
            templates={name: dict(template_cfg) for name, template_cfg in current_email_cfg.templates.items()},
            groups={name: list(members) for name, members in state["groups"].items()},
            active_groups=list(state["active_groups"]),
            static_recipients=list(state["static_recipients"]),
            notifications=dict(state["notifications"]),
            group_prefs={name: _event_prefs(state["group_prefs"].get(name), default=True) for name in state["groups"].keys()},
            recipient_prefs={addr: _event_prefs(state["recipient_prefs"].get(addr), default=True) for addr in state["recipients"]},
        )

    def _recipient_groups(addr: str) -> list[str]:
        return sorted([name for name, members in state["groups"].items() if addr in (members or [])])

    def _recipient_row(addr: str) -> dict[str, str]:
        groups = _recipient_groups(addr)
        active_groups = [name for name in groups if name in state["active_groups"]]
        return {
            "address": addr,
            "static": "yes" if addr in state["static_recipients"] else "-",
            "groups": ", ".join(groups) if groups else "-",
            "active_groups": ", ".join(active_groups) if active_groups else "-",
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
            sources.extend([name for name in state["active_groups"] if addr in (state["groups"].get(name, []) or [])])
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
        counts = {
            "recipients": len(state["recipients"]),
            "groups": len(state["groups"]),
            "active_groups": len(state["active_groups"]),
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
            enabled = bool(state["notifications"].get(event_key, False))
            label.text = "enabled" if enabled else "disabled"
            label.classes(remove="text-positive text-grey", add=("text-positive" if enabled else "text-grey"))
            label.update()
        if overview_table is not None:
            overview_table.rows = _overview_rows()
            overview_table.update()

    def _refresh_group_editor() -> None:
        if existing_group_select is not None:
            existing_group_select.options = list(state["groups"].keys())
            existing_group_select.value = group_editor["selected"]
            existing_group_select.update()
        if group_name_input is not None:
            group_name_input.value = group_editor["name"]
            group_name_input.update()
        if group_members_select is not None:
            group_members_select.options = list(state["recipients"])
            group_members_select.value = list(group_editor["members"])
            group_members_select.update()
        if group_review_label is not None:
            name = group_editor["name"] or "<new group>"
            group_review_label.text = f"Group: {name} | Members: {len(group_editor['members'])}"
            group_review_label.update()
        if group_members_list is not None:
            group_members_list.clear()
            with group_members_list:
                for addr in group_editor["members"]:
                    ui.item(addr)

    def _set_group_step(name: str) -> None:
        if group_stepper is None:
            return
        try:
            group_stepper.set_value(name)
        except Exception:
            group_stepper.value = name
            group_stepper.update()

    def _reset_group_editor() -> None:
        group_editor["selected"] = None
        group_editor["name"] = ""
        group_editor["members"] = []
        _refresh_group_editor()
        _set_group_step("select")

    def _load_group(name: Optional[str]) -> None:
        if not name or name not in state["groups"]:
            _reset_group_editor()
            return
        group_editor["selected"] = name
        group_editor["name"] = name
        group_editor["members"] = list(state["groups"].get(name, []) or [])
        _refresh_group_editor()

    def _collect_group_form() -> tuple[str, list[str]]:
        name = (getattr(group_name_input, "value", group_editor["name"]) or "").strip()
        raw_members = getattr(group_members_select, "value", group_editor["members"]) or []
        members = sanitize_group_addresses(list(raw_members))
        group_editor["name"] = name
        group_editor["members"] = members
        return name, members

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

    def persist_state() -> bool:
        current_cfg = get_global_config()
        if not current_cfg:
            logger.error("Global config not available for saving email settings")
            return False
        try:
            _ensure_address_book()
            current_cfg.email.recipients = list(state["recipients"])
            current_cfg.email.smtp_server = state["smtp"]["server"]
            current_cfg.email.smtp_port = int(state["smtp"]["port"])
            current_cfg.email.sender_email = state["smtp"]["sender"]
            current_cfg.email.groups = dict(state["groups"])
            current_cfg.email.active_groups = list(state["active_groups"])
            current_cfg.email.static_recipients = list(state["static_recipients"])
            current_cfg.email.notifications = dict(state["notifications"])
            current_cfg.email.group_prefs = {
                name: _event_prefs(state["group_prefs"].get(name), default=True)
                for name in state["groups"].keys()
            }
            current_cfg.email.recipient_prefs = {
                addr: _event_prefs(state["recipient_prefs"].get(addr), default=True)
                for addr in state["static_recipients"]
            }
            current_cfg.email.recipients = current_cfg.email.get_known_recipients()
            state["recipients"] = list(current_cfg.email.recipients)

            if save_global_config():
                if email_system is not None:
                    email_system.refresh_config()
                logger.info(_summarize_email_config(current_cfg.email))
                return True
            logger.error("Failed to save configuration")
            return False
        except Exception as exc:
            logger.error("Failed to save configuration: %s", exc, exc_info=True)
            return False

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
                    ui.notify("Email system not initialized", color="negative", position="bottom-right")
                return
            if not persist_state():
                with client:
                    ui.notify("Failed to persist state before sending email", color="negative", position="bottom-right")
                return
            cfg = get_global_config()
            recipients_to_log = _get_effective_recipients_from_config(cfg, state)
            with client:
                ui.notify(f"Sending test email to {', '.join(recipients_to_log)}", color="info", position="bottom-right")
            success = await email_system.send_test_email_async()
            with client:
                if success:
                    ui.notify("Test email sent successfully", color="positive", position="bottom-right")
                else:
                    ui.notify("Error sending test email", color="negative", position="bottom-right")
        finally:
            btn.props(remove="loading")

    def send_test_email(btn: ui.button) -> None:
        cfg = get_global_config()
        effective = _get_effective_recipients_from_config(cfg, state)
        if not effective:
            ui.notify("Can't send test email: no effective recipients configured", color="warning", position="bottom-right")
            return
        client: Client = ui.context.client
        schedule_bg(send_async_test_email(client, btn), name="send_test_email")

    def add_recipient() -> None:
        if email_inp is None:
            return
        addr = (email_inp.value or "").strip()
        if not is_valid_email(addr):
            ui.notify("Invalid email address", color="negative", position="bottom-right")
            return
        if addr in state["recipients"]:
            ui.notify("Address already exists", color="warning", position="bottom-right")
            return
        state["recipients"].append(addr)
        state["recipient_prefs"].setdefault(addr, _event_prefs(default=True))
        email_inp.value = ""
        if persist_state():
            refresh_recipient_table()
            _refresh_group_editor()
            refresh_overview()
            ui.notify(f"Added recipient {addr}", color="positive", position="bottom-right")

    def delete_selected() -> None:
        if recipient_table is None:
            return
        selected_rows = recipient_table.selected or []
        if not selected_rows:
            return
        selected = [row["address"] for row in selected_rows]
        state["recipients"] = [addr for addr in state["recipients"] if addr not in selected]
        state["static_recipients"] = [addr for addr in state["static_recipients"] if addr not in selected]
        group_editor["members"] = [addr for addr in group_editor["members"] if addr not in selected]
        for group_name, members in list(state["groups"].items()):
            state["groups"][group_name] = [addr for addr in members if addr not in selected]
        for addr in selected:
            state["recipient_prefs"].pop(addr, None)
        if persist_state():
            refresh_recipient_table()
            recipient_table.selected = []
            _refresh_group_editor()
            refresh_overview()
            ui.notify(f"Deleted {len(selected)} recipient(s)", color="positive", position="bottom-right")

    def rename_recipient(e: events.GenericEventArguments) -> None:
        try:
            old_addr, new_addr = extract_rename_addresses(e.args)
            if not old_addr or not new_addr:
                return
            if not is_valid_email(new_addr):
                ui.notify(f"Invalid email address: {new_addr}", color="negative")
                refresh_recipient_table()
                return
            if new_addr in state["recipients"] and new_addr != old_addr:
                ui.notify(f"Address already exists: {new_addr}", color="warning")
                refresh_recipient_table()
                return

            idx = state["recipients"].index(old_addr)
            state["recipients"][idx] = new_addr
            state["static_recipients"] = [new_addr if addr == old_addr else addr for addr in state["static_recipients"]]
            group_editor["members"] = [new_addr if addr == old_addr else addr for addr in group_editor["members"]]
            for group_name, members in state["groups"].items():
                state["groups"][group_name] = [new_addr if addr == old_addr else addr for addr in members]
            if old_addr in state["recipient_prefs"]:
                state["recipient_prefs"][new_addr] = state["recipient_prefs"].pop(old_addr)
            if persist_state():
                refresh_recipient_table()
                _refresh_group_editor()
                refresh_overview()
                ui.notify(f"Renamed {old_addr} to {new_addr}", color="positive")
        except ValueError:
            refresh_recipient_table()
        except Exception:
            logger.exception("Error renaming recipient")
            refresh_recipient_table()

    def save_group() -> None:
        name, members = _collect_group_form()
        original_name = group_editor["selected"]
        if not name:
            ui.notify("Group name must not be empty", color="warning", position="bottom-right")
            return
        if len(name) > GROUP_NAME_MAX_LEN:
            ui.notify(f"Group name must be at most {GROUP_NAME_MAX_LEN} characters", color="warning", position="bottom-right")
            return
        if original_name != name and name in state["groups"]:
            ui.notify("Group name already exists", color="warning", position="bottom-right")
            return

        if original_name and original_name in state["groups"] and original_name != name:
            state["groups"].pop(original_name, None)
            if original_name in state["group_prefs"]:
                state["group_prefs"][name] = state["group_prefs"].pop(original_name)
            state["active_groups"] = [name if group == original_name else group for group in state["active_groups"]]
        state["groups"][name] = members
        state["group_prefs"].setdefault(name, _event_prefs(default=True))
        group_editor["selected"] = name
        group_editor["name"] = name
        group_editor["members"] = members
        if persist_state():
            _refresh_group_editor()
            refresh_recipient_table()
            refresh_overview()
            ui.notify(f"Group '{name}' saved", color="positive", position="bottom-right")

    def delete_group() -> None:
        name, _ = _collect_group_form()
        target = group_editor["selected"] or name
        if not target or target not in state["groups"]:
            ui.notify("No existing group selected", color="warning", position="bottom-right")
            return
        state["groups"].pop(target, None)
        state["group_prefs"].pop(target, None)
        state["active_groups"] = [group for group in state["active_groups"] if group != target]
        if persist_state():
            _reset_group_editor()
            refresh_recipient_table()
            refresh_overview()
            ui.notify(f"Group '{target}' deleted", color="positive", position="bottom-right")

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
                "Notification routing is edited in Measurement settings. This screen focuses on address management, group setup, SMTP, and a compact routing overview."
            ).classes("text-body2 text-grey-7")
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
                    with ui.card().classes("p-3 gap-1"):
                        ui.label(title).classes("text-caption text-grey-7")
                        overview_counts[key] = ui.label("0").classes("text-h6 font-semibold")
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

        with ui.row().classes("w-full gap-2"):
            email_inp = ui.input("Add Email").classes("flex-grow").on("keydown.enter", add_recipient)
            ui.button("Add", icon="add", on_click=add_recipient)

        recipient_table = ui.table(
            columns=[
                {"name": "address", "label": "Address", "field": "address", "align": "left", "sortable": True},
                {"name": "static", "label": "Static", "field": "static", "align": "center"},
                {"name": "groups", "label": "Groups", "field": "groups", "align": "left"},
                {"name": "active_groups", "label": "Active Groups", "field": "active_groups", "align": "left"},
            ],
            rows=[],
            selection="multiple",
            pagination={"rowsPerPage": 10},
        ).classes("w-full").props("dense flat bordered")
        recipient_table.add_slot(
            "body-cell-address",
            r'''
            <q-td :props="props">
                {{ props.row.address }}
                <q-popup-edit v-model="props.row.address" v-slot="scope"
                    @save="(val, initialValue) => $parent.$emit('rename', { oldAddress: initialValue, newAddress: val })">
                    <q-input v-model="scope.value" dense autofocus @keyup.enter="scope.set" />
                </q-popup-edit>
            </q-td>
            ''',
        )
        recipient_table.on("selection", lambda e: delete_btn.enable() if e.args else delete_btn.disable())
        recipient_table.on("rename", lambda e: rename_recipient(e))

        ui.separator()

        create_heading_row(
            "Groups",
            icon="groups",
            title_classes="text-h6",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-xl shrink-0",
        )
        ui.label("Create or revise recipient groups in three guided steps. Group event preferences remain in Measurement settings.").classes(
            "text-body2 text-grey-7"
        )

        with ui.stepper(value="select").props("vertical flat animated").classes("w-full") as group_stepper:
            with ui.step("select", title="Select or create group", icon="group_work"):
                ui.label("Step 1: Load an existing group or define the name of a new group.").classes("text-body2")
                with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                    existing_group_select = ui.select(
                        options=list(state["groups"].keys()),
                        label="Existing Groups",
                        clearable=True,
                    ).classes("min-w-[220px] flex-1").props("outlined")
                    ui.button(
                        "Load",
                        icon="download",
                        on_click=lambda: _load_group(existing_group_select.value if existing_group_select is not None else None),
                    ).props("outline")
                    ui.button("New Group", icon="add", on_click=_reset_group_editor).props("outline")
                group_name_input = ui.input("Group Name", value=group_editor["name"]).classes("w-full").props("outlined maxlength=20")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Next", icon="arrow_forward", on_click=lambda: (_collect_group_form(), _set_group_step("members"))).props("color=primary")

            with ui.step("members", title="Select members", icon="group_add"):
                ui.label("Step 2: Choose the address-book entries that belong to this group.").classes("text-body2")
                group_members_select = ui.select(
                    options=list(state["recipients"]),
                    value=list(group_editor["members"]),
                    label="Group Members",
                    multiple=True,
                ).classes("w-full").props("outlined use-chips")
                group_members_select.on("update:model-value", lambda _: _collect_group_form())
                with ui.row().classes("w-full items-center justify-between gap-2 mt-2"):
                    ui.button("Back", icon="arrow_back", on_click=lambda: _set_group_step("select")).props("flat no-caps")
                    ui.button(
                        "Review",
                        icon="arrow_forward",
                        on_click=lambda: (_collect_group_form(), _refresh_group_editor(), _set_group_step("review")),
                    ).props("color=primary")

            with ui.step("review", title="Review and save", icon="task_alt"):
                ui.label("Step 3: Review the group and save or delete it.").classes("text-body2")
                group_review_label = ui.label("").classes("text-body2 text-grey-8")
                with ui.card().classes("w-full p-3 gap-2"):
                    ui.label("Members").classes("text-subtitle2")
                    group_members_list = ui.list().props("dense separator")
                with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                    with ui.row().classes("gap-2 flex-wrap"):
                        ui.button("Back", icon="arrow_back", on_click=lambda: _set_group_step("members")).props("flat no-caps")
                        ui.button("Reset", icon="restart_alt", on_click=_reset_group_editor).props("outline")
                    with ui.row().classes("gap-2 flex-wrap"):
                        ui.button("Delete Group", icon="delete", color="negative", on_click=delete_group)
                        ui.button("Save Group", icon="save", color="primary", on_click=save_group)

        ui.separator()

        create_heading_row(
            "SMTP",
            icon="mail",
            title_classes="text-subtitle1 font-semibold",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-lg shrink-0",
        )
        with ui.row().classes("items-center gap-2 w-full flex-wrap"):
            sender_inp = (
                ui.input("Sender")
                .bind_value(state["smtp"], "sender")
                .tooltip("Email address of the sender")
                .classes("min-w-[220px]")
            )
            server_inp = (
                ui.input("Server")
                .bind_value(state["smtp"], "server")
                .tooltip("SMTP server address")
                .classes("min-w-[220px]")
            )
            port_inp = (
                ui.number("Port", min=1, max=65535)
                .bind_value(state["smtp"], "port", forward=int)
                .tooltip("Port must be between 1 and 65535.")
                .classes("w-28")
            )
            with ui.icon("check_circle").props("size=md").classes("ml-auto") as status_icon:
                status_tt = ui.tooltip("")

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
                ui.notify(" ".join(errors), color="negative", position="bottom-right")
                return
            if persist_state():
                update_status_icon()
                refresh_overview()
                _refresh_group_editor()

        for inp in (sender_inp, server_inp, port_inp):
            inp.on("update:model-value", lambda _: update_status_icon())
        update_status_icon()

        with ui.row().classes("w-full justify-end mt-1"):
            create_action_button("save", label="Save SMTP", on_click=manual_save)

        ui.separator()

        create_heading_row(
            "Templates",
            icon="article",
            title_classes="text-subtitle1 font-semibold",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-lg shrink-0",
        )
        ui.label(
            "Current effective email templates are shown below. Template editing is not available in the GUI."
        ).classes("text-body2 text-grey-7")
        with ui.card().classes("w-full p-4 gap-3"):
            ui.label("Supported placeholders").classes("text-subtitle2")
            ui.label(", ".join(SUPPORTED_TEMPLATE_PLACEHOLDERS)).classes("text-caption text-grey-7")
            for template_row in get_template_overview(config.email):
                with ui.expansion(template_row["label"], icon="description").classes("w-full"):
                    ui.input("Subject", value=template_row["subject"]).props("readonly outlined").classes("w-full")
                    ui.textarea("Body", value=template_row["body"]).props("readonly outlined autogrow").classes("w-full")

    _ensure_address_book()
    refresh_recipient_table()
    refresh_overview()
    _refresh_group_editor()
