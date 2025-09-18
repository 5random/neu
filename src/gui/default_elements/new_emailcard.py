from typing import Optional
import asyncio

from nicegui import ui
from nicegui import events
from nicegui.client import Client

from src.config import get_global_config, save_global_config, get_logger
from src.config import EmailConfig as _EmailConfig
from src.notify import EMailSystem

logger = get_logger('gui.email')

# Reuse the central email regex from EmailConfig for consistency
EMAIL_RE = _EmailConfig.EMAIL_RE

# --- helpers (module-level) -------------------------------------------------
def sanitize_group_addresses(addresses: list[str]) -> list[str]:
    """Return addresses filtered to valid emails, preserving order and de-duplicated.

    This performs one-time validation for group storage so later consumers
    don't need to re-validate on each merge.
    """
    seen: dict[str, None] = {}
    for addr in addresses or []:
        a = (addr or '').strip()
        if a and EMAIL_RE.match(a) and a not in seen:
            seen[a] = None
    return list(seen.keys())


def sanitize_groups_dict(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Sanitize an entire groups mapping (name -> addresses)."""
    clean: dict[str, list[str]] = {}
    for name, addrs in (groups or {}).items():
        clean[name] = sanitize_group_addresses(list(addrs or []))
    return clean


def _get_effective_recipients_from_config(cfg, state: dict) -> list[str]:
    """Return effective recipients from cfg.email if available; fallback to state['recipients'].

    This wrapper is defensive and will never raise, always returning a list.
    """
    try:
        if cfg and getattr(cfg, 'email', None):
            getter = getattr(cfg.email, 'get_target_recipients', None)
            if callable(getter):
                result = getter()
                if isinstance(result, (list, tuple)):
                    return list(result)
                if result is None:
                    return []
                logger.debug("Unexpected type from get_target_recipients: %r", type(result))
                return list(state.get('recipients', []) or [])
    except Exception:
        # fall through to state fallback on any error
        logger.debug("Falling back to local recipients for effective list", exc_info=True)
    return list(state.get('recipients', []) or [])

def create_emailcard(*, alert_system: Optional[EMailSystem] = None) -> None:
    """
    Karte mit drei Tabs:
    1) Übersicht   - read-only Konfig & Test-Mail
    2) Recipients  - Empfänger verwalten
    3) SMTP        - SMTP-Settings inkl. Live-Validierung
    """
    config = get_global_config()

    if not config:
        ui.label('⚠️ Configuration not available').classes('text-red')
        return

    # ------------------------------------------------------------------ #
    # interner Zustand                                                   #
    # ------------------------------------------------------------------ #
    state: dict = {
        "recipients": list(config.email.recipients),
        "smtp": {
            "server": config.email.smtp_server,
            "port": config.email.smtp_port,
            "sender": config.email.sender_email,
        },
        # Groups feature
        "groups": dict(getattr(config.email, "groups", {}) or {}),
        "active_groups": list(getattr(config.email, "active_groups", []) or []),
        "current_group": None,
        # Measurement notifications
        "notifications": dict(getattr(config.email, "notifications", {}) or {}),
    }    # Sanitize any pre-existing groups loaded from config (once on init)
    try:
        original_groups = state.get("groups", {})
        sanitized_groups = sanitize_groups_dict(original_groups)
        # If anything changed, log a warning for visibility and replace in state
        if sanitized_groups != original_groups:
            for gname, addrs in original_groups.items():
                sanitized = sanitized_groups.get(gname, [])
                if set(addrs or []) != set(sanitized):
                    removed = [a for a in (addrs or []) if a not in sanitized]
                    if removed:
                        logger.warning(
                            "Removed invalid email(s) from group '%s': %s",
                            gname,
                            ", ".join(removed),
                        )
        state["groups"] = sanitized_groups
    except Exception:  # noqa: BLE001
        # Don't break UI init if sanitization fails unexpectedly
        logger.exception("Failed to sanitize groups on init; continuing with raw data")

    recipient_list: Optional[ui.list] = None
    eff_recipient_list: Optional[ui.list] = None
    table: Optional[ui.table] = None
    group_table: Optional[ui.table] = None
    active_groups_select: Optional[ui.select] = None
    email_inp: Optional[ui.input] = None
    smtp_labels: dict[str, ui.label] = {}
    overview_counts_base: Optional[ui.label] = None
    overview_counts_eff: Optional[ui.label] = None
    notif_labels: dict[str, ui.label] = {}

    if alert_system is None:
        logger.error("Alert system is not initialized, email functionality will be disabled.")

    # ------------------------------------------------------------------ #
    # Hilfsfunktionen                                                    #
    # ------------------------------------------------------------------ #
    def persist_state() -> bool:
        """Speichert Konfiguration, meldet Fehler im UI."""
        config = get_global_config()
        if not config:
            logger.error("Global config not available for saving email settings")
            return False
        
        try:
            config.email.recipients = list(state["recipients"])
            config.email.smtp_server = state["smtp"]["server"]
            config.email.smtp_port = int(state["smtp"]["port"])
            config.email.sender_email = state["smtp"]["sender"]
            # groups
            if hasattr(config.email, 'groups'):
                config.email.groups = dict(state.get("groups", {}))
            if hasattr(config.email, 'active_groups'):
                config.email.active_groups = list(state.get("active_groups", []))
            # notifications
            if hasattr(config.email, 'notifications'):
                config.email.notifications = dict(state.get("notifications", {}))
            if save_global_config():
                if alert_system:
                    alert_system.refresh_config()
                    logger.info("Alert system configuration refreshed")
                logger.info(f"Configuration saved successfully: {config.email}")
                return True
            else:
                logger.error("Failed to save configuration")
                return False
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to save configuration: {exc}", exc_info=True)
            return False

    

    def is_valid_email(addr: str) -> bool:
        return bool(EMAIL_RE.match(addr))

    def validate_smtp(cfg: dict) -> list[str]:
        errors: list[str] = []
        if not is_valid_email(cfg.get("sender", "")):
            errors.append("Email address is invalid.")
        if not cfg.get("server"):
            errors.append("SMTP server must not be empty.")
        port = cfg.get("port")
        if not isinstance(port, (int, float)) or not 1 <= int(port) <= 65535:
            errors.append("Port must be between 1 and 65535.")
        return errors

    async def send_async_test_email(client: Client) -> None:
        """Asynchrone Funktion zum Senden einer Test-E-Mail."""
        # Alle Variablen außerhalb der try-Blöcke deklarieren
        success = False
        error_msg = ""
        
        # Logging am Anfang
        logger.info("Sending test email...")
        logger.info(f"Alert system: {alert_system}")
        logger.info(f'Recipients: {state["recipients"]}')
        logger.info(f'Total recipients: {len(state["recipients"])}')
        
        if alert_system is None:
            logger.error("Alert system not initialized")
            with client:
                ui.notify("Alert system not initialized", color="negative", position='bottom-right')
            return
        
        # Persist and then compute effective recipients for an accurate UI message
        if not persist_state():
            logger.error("Failed to persist state before sending email")
            with client:
                ui.notify("Failed to persist state before sending email", color="negative", position='bottom-right')
            return
        # UI-Updates after persist: show effective recipients (groups aware)
        cfg = get_global_config()
        effective = _get_effective_recipients_from_config(cfg, state)
        with client:
            ui.notify(f"Sending test email to {', '.join(effective)}", color="info", position='bottom-right')
        
        # E-Mail senden
        try:
            success = await alert_system.send_test_email_async()
        except Exception as e:
            error_msg = f"Failed to send test email: {e}"
            logger.error(error_msg)
            with client:
                ui.notify(error_msg, color="negative", position='bottom-right')
            return

        # Logging der Empfänger (effective recipients)
        eff_logged = _get_effective_recipients_from_config(get_global_config(), state)
        for i, recipient in enumerate(eff_logged):
            logger.info(f"Recipient {i+1}/{len(eff_logged)}: {recipient}")

        # Finale UI-Updates
        with client:
            if success:
                # Recompute to be robust and avoid depending on prior vars
                count = len(_get_effective_recipients_from_config(get_global_config(), state))
                ui.notify(f"Test email sent successfully to all {count} recipients", color="positive", position='bottom-right')
            else:
                ui.notify("Error sending test email", color="negative", position='bottom-right')
        
        # Finales Logging
        if success:
            count = len(_get_effective_recipients_from_config(get_global_config(), state))
            logger.info(f"Test email sent successfully to all {count} recipients")
        else:
            logger.error("Error sending test email")

    def send_test_email() -> None:
        """Sende Test-E-Mail an alle Empfänger."""
        if not state["recipients"]:
            logger.warning("No recipients configured for test email")
            ui.notify("Can't send test email: No recipients configured", color="warning", position='bottom-right')
            return
        
        logger.info(f"Starting test email to {len(state['recipients'])} recipients")
        for recipient in state["recipients"]:
            logger.info(f"Will send to: {recipient}")
        
        client: Client = ui.context.client
        
        asyncio.create_task(send_async_test_email(client))

    # ------------------------------------------------------------------ #
    # UI-Helper                                                          #
    # ------------------------------------------------------------------ #
    def refresh_table() -> None:
        if table is not None:
            table.rows = [{"address": addr} for addr in state["recipients"]]
            table.update()
            update_status_icon()

    def _compute_effective_recipients() -> list[str]:
        """Compute effective recipients from local state (groups + active_groups)."""
        active = state.get("active_groups") or []
        groups = state.get("groups") or {}
        if active and groups:
            seen: set[str] = set()
            ordered: list[str] = []
            for g in active:
                for addr in groups.get(g, []) or []:
                    # groups are sanitized to contain only valid emails; merge + dedupe only
                    if addr not in seen:
                        seen.add(addr)
                        ordered.append(addr)
            if ordered:
                return ordered
        return list(state.get("recipients", []))

    def refresh_recipient_list() -> None:
        if recipient_list is None:
            return
        recipient_list.clear()
        with recipient_list:
            for addr in state["recipients"]:
                ui.item(addr)

    def refresh_effective_list() -> None:
        if eff_recipient_list is None:
            return
        eff_recipient_list.clear()
        with eff_recipient_list:
            for addr in _compute_effective_recipients():
                ui.item(addr)

    def refresh_overview() -> None:
        """Aktualisiert die Overview-Anzeige"""
        refresh_recipient_list()
        refresh_effective_list()
        # Update counts
        if overview_counts_base is not None:
            overview_counts_base.text = f"({len(state['recipients'])})"
            overview_counts_base.update()
        if overview_counts_eff is not None:
            overview_counts_eff.text = f"({len(_compute_effective_recipients())})"
            overview_counts_eff.update()
        # Notification flags
        if notif_labels.get('start') is not None:
            on = bool(state['notifications'].get('on_start', False))
            notif_labels['start'].text = 'enabled' if on else 'disabled'
            notif_labels['start'].classes(remove='text-negative text-grey text-positive', add=('text-positive' if on else 'text-grey'))
            notif_labels['start'].update()
        if notif_labels.get('end') is not None:
            on = bool(state['notifications'].get('on_end', False))
            notif_labels['end'].text = 'enabled' if on else 'disabled'
            notif_labels['end'].classes(remove='text-negative text-grey text-positive', add=('text-positive' if on else 'text-grey'))
            notif_labels['end'].update()
        for key in ["sender", "server", "port"]:
            if key in smtp_labels and smtp_labels[key] is not None:
                smtp_labels[key].text = str(state["smtp"][key])
                smtp_labels[key].update()

    def add_recipient() -> None:
        if email_inp is None:
            return
        addr = (email_inp.value or "").strip()
        if not is_valid_email(addr):
            ui.notify("Invalid email address", color="negative", position='bottom-right')
            return
        if addr in state["recipients"]:
            ui.notify("Address already exists", color="warning", position='bottom-right')
            return

        state["recipients"].append(addr)
        email_inp.value = ""
        if persist_state():
            refresh_table()
            refresh_overview()
            if alert_system:
                alert_system.refresh_config()
            logger.info(f"Added new recipient: {addr}; total recipients: {len(state['recipients'])}")

    def delete_selected() -> None:
        if table is None:
            return
        selected_rows = table.selected or []
        if not selected_rows:
            return
        selected_addresses = [row["address"] for row in selected_rows]
        state["recipients"] = [
            addr for addr in state["recipients"] if addr not in selected_addresses
        ]
        # Also remove from all groups
        for gname, addrs in list(state["groups"].items()):
            state["groups"][gname] = [a for a in addrs if a not in selected_addresses]
        if persist_state():
            refresh_table()
            table.selected = []
            refresh_overview()
            if group_table is not None:
                group_table.rows = [{"address": addr} for addr in state["recipients"]]
                group_table.selected = []
                group_table.update()
            if alert_system:
                alert_system.refresh_config()
            ui.notify(f"{len(selected_addresses)} address(es) deleted", color="positive", position='bottom-right')
            logger.info(f"Deleted {len(selected_addresses)} recipient(s): {', '.join(selected_addresses)}; new total recipients: {len(state['recipients'])}")

    def rename_recipient(e: events.GenericEventArguments) -> None:
        """Handles in-line edit of a recipient address.

        Expects payload: {'old': str, 'address': str} or legacy {'index': int, 'address': str}
        Validates, persists, and refreshes UI; reverts on error.
        """
        try:
            payload = e.args or {}
            new_addr = (payload.get('address') or '').strip()
            if 'old' in payload:
                old_addr = (payload.get('old') or '').strip()
                idx = state['recipients'].index(old_addr) if old_addr in state['recipients'] else -1
            else:
                idx_val = payload.get('index')
                idx = int(idx_val) if idx_val is not None else -1
                old_addr = state['recipients'][idx] if 0 <= idx < len(state['recipients']) else ''
        except Exception:
            ui.notify('Invalid edit event payload', color='negative', position='bottom-right')
            logger.error('Invalid edit payload for recipient rename: %s', e.args)
            refresh_table()
            return

        # Bounds check
        if idx < 0 or idx >= len(state['recipients']):
            ui.notify('Edit target not found', color='negative', position='bottom-right')
            logger.error('Edit target not found: %s', e.args)
            refresh_table()
            return

        # Validate email format
        if not is_valid_email(new_addr):
            ui.notify('Invalid email address', color='negative', position='bottom-right')
            logger.warning('Rejected invalid email on rename: %s', new_addr)
            # revert visual table from state
            refresh_table()
            return

        # Prevent duplicates (allow same as old)
        existing = set(state['recipients'])
        if new_addr != old_addr and new_addr in existing:
            ui.notify('Address already exists', color='warning', position='bottom-right')
            logger.warning('Rejected duplicate email on rename: %s', new_addr)
            refresh_table()
            return

        # Apply change and persist
        state['recipients'][idx] = new_addr
        # Update groups memberships
        for gname, addrs in list(state["groups"].items()):
            state["groups"][gname] = [new_addr if a == old_addr else a for a in addrs]
        if persist_state():
            refresh_table()
            refresh_overview()
            if group_table is not None:
                group_table.rows = [{"address": addr} for addr in state["recipients"]]
                # re-apply selection for current group
                if state["current_group"] in state["groups"]:
                    tgt = set(state["groups"][state["current_group"]])
                    group_table.selected = [r for r in group_table.rows if r["address"] in tgt]
                group_table.update()
            if alert_system:
                alert_system.refresh_config()
            logger.info('Recipient updated: %s -> %s', old_addr, new_addr)
            ui.notify('Recipient updated', color='positive', position='bottom-right')
        else:
            # rollback in-memory state on failure
            state['recipients'][idx] = old_addr
            refresh_table()
            ui.notify('Failed to save changes', color='negative', position='bottom-right')
            logger.error('Failed to persist state after rename (rolled back)')

    # ------------------------------------------------------------------ #
    # Haupt-Card                                                         #
    # ------------------------------------------------------------------ #
    logger.info("Creating email card")
    with ui.card().classes("w-full flex-shrink-0"):
        ui.label("Email Settings").classes("text-h6 font-semibold mb-2")

        # Tabs
        with ui.tabs() as tabs:
            tab_overview = ui.tab("Overview").tooltip("Overview of the email configuration")
            tab_rcp = ui.tab("Recipients").tooltip("Manage email recipients")
            tab_smtp = ui.tab("SMTP").tooltip("Configure SMTP settings")
            tab_groups = ui.tab("Groups").tooltip("Create and activate recipient groups")

        # Panels
        with ui.tab_panels(tabs, value=tab_overview).classes("w-full"):

            # ---------------------- Übersicht --------------------------- #
            with ui.tab_panel(tab_overview):
                with ui.column().classes("gap-2"):
                    with ui.row().style("align-self:flex-start; flex-direction:row; justify-content:flex-start; align-items:center; flex-wrap:wrap; gap:12px;"):
                        ui.label("Configured recipients").classes("text-subtitle2 text-grey-7 mt-1")
                        overview_counts_base = ui.label("").classes("text-caption text-grey-5")
                        ui.label("Effective recipients").classes("text-subtitle2 text-grey-7 mt-1 ml-4")
                        overview_counts_eff = ui.label("").classes("text-caption text-grey-5")

                    recipient_list = ui.list().props("dense").classes("pl-2")
                    refresh_recipient_list()

                    ui.separator()

                    ui.label("Effective recipients (used for sending)").classes("text-subtitle2 text-grey-7")
                    eff_recipient_list = ui.list().props("dense").classes("pl-2")
                    refresh_effective_list()

                    ui.separator()

                    ui.label("Notification emails").classes("text-subtitle2 text-grey-7")
                    with ui.row().classes("items-center gap-4"):
                        ui.label("On start:").classes("text-grey-6")
                        notif_labels['start'] = ui.label("").classes("text-caption text-grey")
                        ui.label("On end:").classes("text-grey-6 ml-4")
                        notif_labels['end'] = ui.label("").classes("text-caption text-grey")

                    ui.separator()

                    ui.label("SMTP Settings").classes("text-subtitle2 text-grey-7")
                    with ui.row().classes("items-center"):
                        for key, label_txt in [("sender", "Sender"), ("server", "Server"), ("port", "Port")]:
                            ui.label(f"{label_txt}:").classes("w-24 text-right text-grey-6")
                            smtp_labels[key] = ui.label(str(state["smtp"][key])).classes("flex-grow")

                ui.separator()

                ui.button(
                    icon="send",
                    color="info",
                    on_click=lambda _: send_test_email(),
                ).props("round").tooltip("Send test email to all recipients")

            # ---------------------- Recipients --------------------------- #
            with ui.tab_panel(tab_rcp):
                with ui.row().classes("items-center gap-2"):
                    email_inp = (
                        ui.input("New Email")
                        .classes("w-64")
                        .on("keyup.enter", lambda _: add_recipient())
                    ).tooltip(
                        "Enter email address and press Enter to add "
                        "or click the add button"
                    )

                    with ui.button(
                        icon="add_circle",
                        color="primary",
                        on_click=lambda _: add_recipient(),
                    ).bind_enabled_from(email_inp, "value", is_valid_email).props("round"):
                        ui.tooltip("Add email address")

                ui.separator()

                table = ui.table(
                    columns=[{"name": "address", "label": "Address", "field": "address"}],
                    rows=[],
                    row_key="address",
                    selection="multiple",
                ).classes("w-full")

                # Inline editing for 'address' cell while keeping selection checkboxes
                table.add_slot('body-cell-address', r'''
                    <q-td :props="props">
                        {{ props.value }}
                        <q-popup-edit v-model="props.row.address" v-slot="scope"
                                      @save="(val) => $parent.$emit('rename', { old: scope.initialValue, address: val })">
                            <q-input v-model="scope.value" dense autofocus counter @keyup.enter="scope.set" />
                        </q-popup-edit>
                    </q-td>
                ''')

                table.on('rename', rename_recipient)

                with ui.row().classes("w-full items-center gap-2 mt-2"):
                    with ui.button(
                        icon="delete",
                        color="negative",
                        on_click=lambda _: delete_selected(),
                    ).bind_enabled_from(table, "selected", lambda s: bool(s)).props("round"):
                        ui.tooltip("Delete selected email addresses")

            # ---------------------- SMTP ---------------------------------- #
            with ui.tab_panel(tab_smtp):

                with ui.row().classes("items-center gap-2"):
                    sender_inp = (
                        ui.input("Sender")
                        .bind_value(state["smtp"], "sender")
                        .tooltip("Email address of the sender")
                    )
                    server_inp = (
                        ui.input("Server")
                        .bind_value(state["smtp"], "server")
                        .tooltip("SMTP server address")
                    )
                    port_inp = (
                        ui.number("Port", min=1, max=65535)
                        .bind_value(state["smtp"], "port", forward=int)
                        .tooltip("Port must be between 1 and 65535.")
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
                        status_tt.text = "All SMTP settings valid ✔︎"
                    status_icon.update()

                def manual_save() -> None:
                    errors = validate_smtp(state["smtp"])
                    if errors:
                        ui.notify(" ".join(errors), color="negative", position='bottom-right')
                    else:
                        if persist_state():
                            update_status_icon()
                            refresh_overview()
                            if alert_system:
                                alert_system.refresh_config()

                for inp in (sender_inp, server_inp, port_inp):
                    inp.on("update:model-value", lambda _: update_status_icon())

                update_status_icon()

                ui.button(
                    icon="save",
                    color="primary",
                    on_click=manual_save,
                ).props("round").tooltip("Save SMTP settings")

            # ---------------------- Groups ---------------------------------- #
            with ui.tab_panel(tab_groups):
                ui.label("Manage Recipient Groups").classes("text-subtitle2 text-grey-7")

                with ui.row().classes("items-center gap-2 q-mb-sm"):
                    group_name_inp = ui.input("Group name").classes("w-64")

                    def new_group() -> None:
                        state["current_group"] = None
                        group_name_inp.value = ""
                        if group_table:
                            group_table.selected = []
                    ui.button("New", icon="create_new_folder", color="accent", on_click=lambda _: new_group()).props("round")

                    def save_group() -> None:
                        name = (group_name_inp.value or "").strip()
                        if not name:
                            ui.notify("Please enter a group name", color="warning", position='bottom-right'); return
                        # Collect selected recipients
                        selected = [r["address"] for r in (group_table.selected or [])] if group_table else []
                        if not selected:
                            ui.notify("Please select at least one recipient", color="warning", position='bottom-right'); return
                        # Sanitize addresses once before storing
                        sanitized = sanitize_group_addresses(selected)
                        if len(sanitized) != len(selected):
                            removed = [a for a in selected if a not in sanitized]
                            logger.warning("Group '%s': removed invalid/duplicate address(es): %s", name, ", ".join(removed))
                            ui.notify(
                                f"Ignored invalid/duplicate addresses: {', '.join(removed)}",
                                color="warning",
                                position='bottom-right',
                            )
                        # Explicit apply
                        state["groups"][name] = sanitized
                        state["current_group"] = name
                        if persist_state():
                            refresh_groups_ui()
                            ui.notify(f"Group '{name}' saved", color="positive", position='bottom-right')
                    ui.button("Save group", icon="save", color="primary", on_click=lambda _: save_group()).props("round")

                    # Confirmation dialog for group deletion
                    confirm_dialog = ui.dialog()
                    with confirm_dialog, ui.card():
                        ui.label("Delete group?").classes("text-subtitle2")
                        confirm_text = ui.label("")
                        with ui.row().classes("justify-end gap-2 mt-2"):
                            ui.button("Cancel", on_click=confirm_dialog.close)
                            def _confirm_delete(name: str) -> None:
                                if not name or name not in state["groups"]:
                                    ui.notify("Select a valid group to delete", color="warning", position='bottom-right'); return
                                state["groups"].pop(name, None)
                                state["active_groups"] = [g for g in state["active_groups"] if g != name]
                                state["current_group"] = None
                                group_name_inp.value = ""
                                if group_table:
                                    group_table.selected = []
                                if persist_state():
                                    refresh_groups_ui()
                                    refresh_overview()
                                    ui.notify(f"Group '{name}' deleted", color="positive", position='bottom-right')
                                confirm_dialog.close()
                            # Single confirm handler refers to pending name in state to avoid stacking handlers
                            confirm_btn = ui.button("Delete", color="negative", on_click=lambda _: _confirm_delete(state.get("pending_delete_group", "")))

                    def delete_group() -> None:
                        name = (group_name_inp.value or "").strip()
                        if not name:
                            ui.notify("Select a valid group to delete", color="warning", position='bottom-right'); return
                        confirm_text.text = f"Group '{name}' will be deleted. This cannot be undone."
                        state["pending_delete_group"] = name
                        confirm_dialog.open()
                    ui.button("Delete group", icon="delete", color="negative", on_click=lambda _: delete_group()).props("round")

                with ui.row().classes("items-start gap-4 w-full"):
                    # Existing groups list
                    with ui.column().classes("w-1/3"):
                        ui.label("Existing groups").classes("text-caption text-grey-7")
                        groups_list = ui.list().props("bordered dense")

                        def load_group(name: str) -> None:
                            state["current_group"] = name
                            group_name_inp.value = name
                            if group_table:
                                target = set(state["groups"].get(name, []) or [])
                                group_table.selected = [r for r in group_table.rows if r["address"] in target]
                                group_table.update()

                        def refresh_groups_ui() -> None:
                            groups_list.clear()
                            with groups_list:
                                for gname, addrs in sorted(state["groups"].items()):
                                    with ui.item(on_click=lambda _=None, name=gname: load_group(name)).classes("cursor-pointer"):
                                        ui.item_section(gname)
                                        ui.item_section().classes("text-grey-6").text = f"({len(addrs)} recipients)"
                            # Ensure group table reflects recipients
                            if group_table:
                                refresh_group_table_rows()
                            # Update active groups select options and selection
                            if active_groups_select is not None:
                                active_groups_select.options = list(state["groups"].keys())
                                active_groups_select.value = list(state["active_groups"])
                                active_groups_select.update()

                    # Recipients selection for group
                    with ui.column().classes("w-2/3"):
                        ui.label("Select recipients for this group").classes("text-caption text-grey-7")
                        group_table = ui.table(
                            columns=[{"name": "address", "label": "Address", "field": "address"}],
                            rows=[{"address": addr} for addr in state["recipients"]],
                            row_key="address",
                            selection="multiple",
                        ).classes("w-full")

                        def refresh_group_table_rows() -> None:
                            group_table.rows = [{"address": addr} for addr in state["recipients"]]
                            group_table.update()

                ui.separator()

                # Active groups selection
                ui.label("Active groups (used for sending)").classes("text-subtitle2 text-grey-7")
                with ui.row().classes("items-center gap-2"):
                    active_groups_select = ui.select(
                        options=list(state["groups"].keys()),
                        value=list(state["active_groups"]),
                        multiple=True,
                        label="Active groups",
                    ).classes("min-w-[280px]")

                    def apply_active_groups() -> None:
                        selected = active_groups_select.value or []
                        state["active_groups"] = list(selected)
                        if persist_state():
                            ui.notify(
                                f"Active groups set: {', '.join(state['active_groups']) or '—'}",
                                color="positive",
                                position='bottom-right',
                            )
                            refresh_overview()
                    ui.button("Apply", icon="done", color="primary", on_click=lambda _: apply_active_groups()).props("round").tooltip("Apply active groups for sending")

                ui.separator()

                # Measurement notifications toggles
                ui.label("Measurement notifications").classes("text-subtitle2 text-grey-7")
                with ui.row().classes("items-center gap-2"):
                    notify_start_cb = ui.checkbox(
                        'Send email on measurement start',
                        value=bool(state["notifications"].get("on_start", False))
                    )
                    notify_end_cb = ui.checkbox(
                        'Send email on measurement end',
                        value=bool(state["notifications"].get("on_end", False))
                    )

                    def apply_notifications() -> None:
                        state["notifications"]["on_start"] = bool(notify_start_cb.value)
                        state["notifications"]["on_end"] = bool(notify_end_cb.value)
                        if persist_state():
                            ui.notify("Notification settings applied", color="positive", position='bottom-right')
                            refresh_overview()
                    ui.button("Apply", icon="done", color="primary", on_click=lambda _: apply_notifications()).props("round").tooltip("Apply notification settings")

                # Initialize groups UI
                def _init_groups():
                    # Single canonical UI refresh for groups
                    refresh_groups_ui()
                _init_groups()

    # ------------------------------------------------------------------ #
    # Tabelle initial befüllen                                           #
    # ------------------------------------------------------------------ #
    refresh_table()
    # Ensure overview labels and lists are initially populated
    try:
        refresh_overview()
    except (AttributeError, RuntimeError):
        # If overview widgets are not yet available, ignore (expected during initial render)
        logger.debug("Overview widgets not ready yet; skipping initial refresh", exc_info=True)
    except Exception:
        # Surface unexpected issues during development
        logger.exception("Failed to refresh overview during initial load")

    # no wrapper for refresh_table to avoid name shadowing warnings