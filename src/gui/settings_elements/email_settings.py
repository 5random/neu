from typing import Optional, Any
import re
from nicegui import ui, events
from src.config import get_global_config, save_global_config, get_logger
from src.notify import EMailSystem
from src.gui.util import schedule_bg
from nicegui import Client
from src.gui.settings_elements.ui_helpers import create_action_button, create_heading_row

logger = get_logger("gui.email_settings")

EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")
GROUP_NAME_MAX_LEN = 20  # Max chars for new group names

def sanitize_group_addresses(addresses: list[str]) -> list[str]:
    """Return a deduplicated list of valid email addresses."""
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


def extract_rename_addresses(args: Any) -> tuple[Optional[str], Optional[str]]:
    """Normalize rename event payloads from NiceGUI/Quasar into two addresses."""
    if isinstance(args, (list, tuple)) and len(args) == 1:
        args = args[0]

    if isinstance(args, dict):
        old_addr = args.get('oldAddress') or args.get('address')
        new_addr = args.get('newAddress') or args.get('value')
    elif isinstance(args, (list, tuple)) and len(args) >= 2:
        old_addr, new_addr = args[0], args[1]
    else:
        return None, None

    old_text = old_addr.strip() if isinstance(old_addr, str) else None
    new_text = new_addr.strip() if isinstance(new_addr, str) else None
    return old_text or None, new_text or None


def _get_effective_recipients_from_config(cfg: Any, state: dict) -> list[str]:
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

def create_emailcard(*, email_system: Optional[EMailSystem] = None) -> None:
    """Render email settings as stacked sections (no tabs), suitable for settings page list layout.

    Sections in order:
    - Overview
    - Recipients
    - Groups (incl. Active groups)
    - Notifications
    - SMTP
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
        # Warn for legacy group names exceeding max length (do not auto-rename)
        try:
            too_long = [g for g in sanitized_groups.keys() if len(str(g)) > GROUP_NAME_MAX_LEN]
            if too_long:
                logger.warning(
                    "Found %d group name(s) exceeding max length %d: %s",
                    len(too_long), GROUP_NAME_MAX_LEN, ", ".join(too_long[:5]) + (" ..." if len(too_long) > 5 else "")
                )
        except Exception:
            logger.debug("Group length check failed during init", exc_info=True)
    except Exception:  # noqa: BLE001
        # Don't break UI init if sanitization fails unexpectedly
        logger.exception("Failed to sanitize groups on init; continuing with raw data")

    recipient_list: Optional[ui.list] = None
    eff_recipient_list: Optional[ui.list] = None
    table: Optional[ui.table] = None
    group_table: Optional[ui.table] = None  # deprecated: groups now use main recipients table selection
    active_groups_select: Optional[ui.select] = None
    group_select: Optional[ui.select] = None
    active_groups_apply_btn: Optional[ui.button] = None
    delete_btn: Optional[ui.button] = None
    email_inp: Optional[ui.input] = None
    smtp_labels: dict[str, ui.label] = {}
    overview_counts_base: Optional[ui.label] = None
    overview_counts_eff: Optional[ui.label] = None
    notif_labels: dict[str, ui.label] = {}
    # Notification UI refs for enable/disable handling
    notifications_apply_btn: Optional[ui.button] = None
    notify_start_cb: Optional[ui.checkbox] = None
    notify_end_cb: Optional[ui.checkbox] = None
    notify_stop_cb: Optional[ui.checkbox] = None
    # Per-recipient notification preferences (from config)
    recipient_prefs: dict[str, dict[str, bool]] = dict(getattr(config.email, 'recipient_prefs', {}) or {})

    if email_system is None:
        logger.error("Email system is not initialized, email functionality will be disabled.")

    # ------------------------------------------------------------------ #
    # Hilfsfunktionen                                                    #
    # ------------------------------------------------------------------ #
    def _summarize_email_config(email_cfg: Any) -> str:
        """Return a concise summary string for logging (no templates / large fields)."""
        try:
            rec_count = len(getattr(email_cfg, "recipients", []) or [])
            groups = getattr(email_cfg, "groups", {}) or {}
            grp_count = len(groups)
            active_count = len(getattr(email_cfg, "active_groups", []) or [])
            smtp = getattr(email_cfg, "smtp_server", getattr(email_cfg, "smtp", {}).get("server", "<unknown>"))
            sender = getattr(email_cfg, "sender_email", getattr(email_cfg, "smtp", {}).get("sender", "<unknown>"))
            return (
                f"EmailConfig(summary): recipients={rec_count}, groups={grp_count}, "
                f"active_groups={active_count}, smtp_server={smtp}, sender={sender}"
            )
        except Exception:
            return "EmailConfig(summary): <error building summary>"

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
            # per-recipient prefs (store only for current recipients)
            if hasattr(config.email, 'recipient_prefs'):
                valid: dict[str, dict[str, bool]] = {}
                for addr in state.get('recipients', []):
                    p = recipient_prefs.get(addr, {}) or {}
                    valid[addr] = {
                        'on_start': bool(p.get('on_start', False)),
                        'on_end': bool(p.get('on_end', False)),
                        'on_stop': bool(p.get('on_stop', False)),
                    }
                config.email.recipient_prefs = valid
            if save_global_config():
                if email_system:
                    email_system.refresh_config()
                    logger.info("Email system configuration refreshed")
                # Replace verbose dataclass repr with concise summary
                logger.info(_summarize_email_config(config.email))
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

    async def send_async_test_email(client: Client, btn: ui.button) -> None:
        """Asynchrone Funktion zum Senden einer Test-E-Mail."""
        # UI Loading State
        btn.props('loading')
        
        # Alle Variablen außerhalb der try-Blöcke deklarieren
        success = False
        error_msg = ""
        
        try:
            # Logging am Anfang
            logger.info("Sending test email...")
            logger.info(f"Email system: {email_system}")
            logger.info(f'Recipients: {state["recipients"]}')
            logger.info(f'Total recipients: {len(state["recipients"])}')

            if email_system is None:
                logger.error("Email system not initialized")
                with client:
                    ui.notify("Email system not initialized", color="negative", position='bottom-right')
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
                success = await email_system.send_test_email_async()
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
        finally:
            btn.props(remove='loading')

    def send_test_email(btn: ui.button) -> None:
        """Sende Test-E-Mail an alle Empfänger."""
        cfg = get_global_config()
        effective = _get_effective_recipients_from_config(cfg, state)
        if not effective:
            logger.warning("No effective recipients configured for test email")
            ui.notify("Can't send test email: No recipients configured (recipients or active groups)", color="warning", position='bottom-right')
            return

        logger.info(f"Starting test email to {len(effective)} recipients")
        for recipient in effective:
            logger.info(f"Will send to: {recipient}")
         
        client: Client = ui.context.client
        
        # Schedule test email send safely via helper (defers if loop not ready)
        schedule_bg(send_async_test_email(client, btn), name='send_test_email')

    # ------------------------------------------------------------------ #
    # UI-Helper                                                          #
    # ------------------------------------------------------------------ #
    def _update_active_groups_apply_state() -> None:
        """Enable Active-Groups Apply button only if selection differs from current state."""
        nonlocal active_groups_select, active_groups_apply_btn
        try:
            if active_groups_apply_btn is None or active_groups_select is None:
                return
            selected = set((active_groups_select.value or []))
            current = set(state.get("active_groups", []) or [])
            if selected == current:
                active_groups_apply_btn.disable()
            else:
                active_groups_apply_btn.enable()
        except Exception:
            # Be conservative: disable on error
            try:
                if active_groups_apply_btn is not None:
                    active_groups_apply_btn.disable()
            except Exception:
                pass

    def _update_notifications_apply_state() -> None:
        """Enable Notifications Apply only if checkbox values differ from state."""
        nonlocal notifications_apply_btn, notify_start_cb, notify_end_cb, notify_stop_cb
        try:
            if (
                notifications_apply_btn is None
                or notify_start_cb is None
                or notify_end_cb is None
                or notify_stop_cb is None
            ):
                return
            cur_start = bool(state.get("notifications", {}).get("on_start", False))
            cur_end = bool(state.get("notifications", {}).get("on_end", False))
            cur_stop = bool(state.get("notifications", {}).get("on_stop", False))
            sel_start = bool(getattr(notify_start_cb, 'value', cur_start))
            sel_end = bool(getattr(notify_end_cb, 'value', cur_end))
            sel_stop = bool(getattr(notify_stop_cb, 'value', cur_stop))
            if (cur_start == sel_start) and (cur_end == sel_end) and (cur_stop == sel_stop):
                notifications_apply_btn.disable()
            else:
                notifications_apply_btn.enable()
        except Exception:
            try:
                if notifications_apply_btn is not None:
                    notifications_apply_btn.disable()
            except Exception:
                pass

    # Centralized group selection application helper
    def apply_group_selection(name: Optional[str]) -> None:
        try:
            if name and name in (state.get("groups") or {}):
                state["current_group"] = name

                if table is not None:
                    target = set(state["groups"].get(name, []) or [])
                    table.selected = [r for r in (table.rows or []) if r.get("address") in target]
                    table.update()
                if group_select is not None and getattr(group_select, 'value', None) != name:
                    group_select.value = name
                    group_select.update()
            else:
                state["current_group"] = None

                if table is not None:
                    table.selected = []
                    table.update()
                if group_select is not None and getattr(group_select, 'value', None) is not None:
                    group_select.value = None
                    group_select.update()
        except Exception:
            logger.exception('Failed to apply group selection')
    def _groups_for(addr: str) -> list[str]:
        """Return sorted list of groups containing the given address."""
        try:
            result: list[str] = []
            for gname, addrs in (state.get("groups") or {}).items():
                if addr in (addrs or []):
                    result.append(gname)
            return sorted(result)
        except Exception:
            logger.exception('Failed to compute groups for %s', addr)
            return []

    def _row_for(addr: str) -> dict:
        p = recipient_prefs.get(addr, {}) or {}
        g_list = _groups_for(addr)
        return {
            "address": addr,
            "on_start": bool(p.get("on_start", False)),
            "on_end": bool(p.get("on_end", False)),
            "on_stop": bool(p.get("on_stop", False)),
            "groups": ", ".join(g_list) if g_list else "—",
            "groups_list": g_list,
        }

    def refresh_table() -> None:
        if table is not None:
            table.rows = [_row_for(addr) for addr in state["recipients"]]
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
        if notif_labels.get('stop') is not None:
            on = bool(state['notifications'].get('on_stop', False))
            notif_labels['stop'].text = 'enabled' if on else 'disabled'
            notif_labels['stop'].classes(remove='text-negative text-grey text-positive', add=('text-positive' if on else 'text-grey'))
            notif_labels['stop'].update()
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
            if email_system:
                email_system.refresh_config()
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
        # Remove per-recipient preferences
        try:
            for addr in selected_addresses:
                recipient_prefs.pop(addr, None)
        except Exception:
            logger.exception('Failed to cleanup recipient prefs during delete')
        if persist_state():
            refresh_table()
            table.selected = []
            refresh_overview()
            if email_system:
                email_system.refresh_config()
            ui.notify(f"{len(selected_addresses)} address(es) deleted", color="positive", position='bottom-right')
            logger.info(f"Deleted {len(selected_addresses)} recipient(s): {', '.join(selected_addresses)}; new total recipients: {len(state['recipients'])}")

    def rename_recipient(e: events.GenericEventArguments) -> None:
        """Handle renaming of a recipient in the table."""
        try:
            old_addr, new_addr = extract_rename_addresses(e.args)
            if not old_addr or not new_addr:
                return
            if not is_valid_email(new_addr):
                ui.notify(f"Invalid email address: {new_addr}", color="negative")
                refresh_table()  # Revert UI
                return
            if new_addr in state["recipients"] and new_addr != old_addr:
                ui.notify(f"Address already exists: {new_addr}", color="warning")
                refresh_table()  # Revert UI
                return
            
            # Update recipients list
            try:
                idx = state["recipients"].index(old_addr)
                state["recipients"][idx] = new_addr
            except ValueError:
                return

            # Update groups
            for gname, addrs in state["groups"].items():
                if old_addr in addrs:
                    state["groups"][gname] = [new_addr if a == old_addr else a for a in addrs]
            
            # Update prefs
            if old_addr in recipient_prefs:
                recipient_prefs[new_addr] = recipient_prefs.pop(old_addr)

            if persist_state():
                ui.notify(f"Renamed {old_addr} to {new_addr}", color="positive")
                refresh_table()
                refresh_overview()
        except Exception:
            logger.exception("Error renaming recipient")
            refresh_table()

    def toggle_pref(addr: str, key: str, value: bool) -> None:
        """Toggle a specific notification preference for a recipient."""
        if addr not in recipient_prefs:
            recipient_prefs[addr] = {}
        recipient_prefs[addr][key] = value
        if persist_state():
            # No full refresh needed, just notify? Or refresh table to be safe
            # refresh_table() # Table updates automatically via binding? No.
            pass

    # ------------------------------------------------------------------ #
    # UI-Aufbau                                                          #
    # ------------------------------------------------------------------ #
    with ui.column().classes("w-full gap-6"):
        
        # ---------------------- Overview ------------------------------ #
        with ui.card().classes("w-full p-4"):
            create_heading_row(
                "Overview",
                icon="dashboard",
                title_classes="text-h6 font-bold mb-2",
                row_classes="items-center gap-2",
                icon_classes="text-primary text-xl shrink-0",
            )
            with ui.grid(columns=2).classes("gap-x-8 gap-y-2"):
                with ui.row().classes("items-baseline gap-2"):
                    ui.label("Total recipients:").classes("text-subtitle2 text-grey-7")
                    overview_counts_base = ui.label("").classes("text-caption text-grey-5")
                
                with ui.row().classes("items-baseline gap-2"):
                    ui.label("Effective recipients").classes("text-subtitle2 text-grey-7")
                    overview_counts_eff = ui.label("").classes("text-caption text-grey-5")
            test_email_btn: ui.button = ui.button("Send test email", icon="send", color="info", on_click=lambda: send_test_email(test_email_btn)).props("unelevated")

        # Lists
        with ui.grid(columns=2).classes("w-full gap-4"):
            with ui.card().classes("w-full p-2"):
                create_heading_row(
                    "All Recipients",
                    icon="list_alt",
                    title_classes="text-caption font-bold mb-1",
                    row_classes="items-center gap-2",
                    icon_classes="text-primary text-base shrink-0",
                )
                recipient_list = ui.list().props("dense separator")
            with ui.card().classes("w-full p-2"):
                create_heading_row(
                    "Effective (Active)",
                    icon="done_all",
                    title_classes="text-caption font-bold mb-1",
                    row_classes="items-center gap-2",
                    icon_classes="text-primary text-base shrink-0",
                )
                eff_recipient_list = ui.list().props("dense separator")

        ui.separator()

        # ---------------------- Recipients ---------------------------- #
        with ui.row().classes("w-full items-center justify-between"):
            create_heading_row(
                "Recipients",
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

        # Main Recipients Table
        table = ui.table(
            columns=[
                {"name": "address", "label": "Address", "field": "address", "sortable": True, "align": "left"},
                {"name": "groups", "label": "Groups", "field": "groups", "align": "left"},
                {"name": "on_start", "label": "Start", "field": "on_start", "align": "center"},
                {"name": "on_end", "label": "End", "field": "on_end", "align": "center"},
                {"name": "on_stop", "label": "Stop", "field": "on_stop", "align": "center"},
            ],
            rows=[],
            selection="multiple",
            pagination={"rowsPerPage": 10},
        ).classes("w-full").props("dense flat bordered")
        
        # Add slots for checkboxes
        table.add_slot('body-cell-on_start', r'''
            <q-td :props="props">
                <q-checkbox v-model="props.row.on_start" dense 
                    @update:model-value="() => $parent.$emit('toggle', props.row.address, 'on_start', props.row.on_start)" />
            </q-td>
        ''')
        table.add_slot('body-cell-on_end', r'''
            <q-td :props="props">
                <q-checkbox v-model="props.row.on_end" dense 
                    @update:model-value="() => $parent.$emit('toggle', props.row.address, 'on_end', props.row.on_end)" />
            </q-td>
        ''')
        table.add_slot('body-cell-on_stop', r'''
            <q-td :props="props">
                <q-checkbox v-model="props.row.on_stop" dense 
                    @update:model-value="() => $parent.$emit('toggle', props.row.address, 'on_stop', props.row.on_stop)" />
            </q-td>
        ''')
        # Add slot for inline editing of address
        table.add_slot('body-cell-address', r'''
            <q-td :props="props">
                {{ props.row.address }}
                <q-popup-edit v-model="props.row.address" v-slot="scope" 
                    @save="(val, initialValue) => $parent.$emit('rename', { oldAddress: initialValue, newAddress: val })">
                    <q-input v-model="scope.value" dense autofocus counter @keyup.enter="scope.set" />
                </q-popup-edit>
            </q-td>
        ''')
        
        table.on('selection', lambda e: delete_btn.enable() if e.args else delete_btn.disable())
        table.on('toggle', lambda e: toggle_pref(e.args[0], e.args[1], e.args[2]))
        table.on('rename', lambda e: rename_recipient(e))

        ui.separator()

        # ---------------------- Groups -------------------------------- #
        create_heading_row(
            "Groups",
            icon="groups",
            title_classes="text-h6",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-xl shrink-0",
        )
        
        # Group Management (Create/Delete)
        with ui.row().classes("w-full gap-4 items-start"):
            # Left: Group Selector & Actions
            with ui.card().classes("flex-1 p-4"):
                create_heading_row(
                    "Manage Groups",
                    icon="group_work",
                    title_classes="font-bold mb-2",
                    row_classes="items-center gap-2",
                    icon_classes="text-primary text-lg shrink-0",
                )
                with ui.row().classes("w-full gap-2"):
                    group_select = ui.select(
                        options=list(state["groups"].keys()),
                        label="Select Group",
                        with_input=True,
                        new_value_mode="add-unique",
                        clearable=True
                    ).classes("flex-grow").props("use-input")
                    
                    def _on_group_change(e: Any) -> None:
                        apply_group_selection(e.value)
                    if group_select is not None:
                        group_select.on('update:model-value', _on_group_change)

                    def _delete_current_group() -> None:
                        g = state.get("current_group")
                        if g and g in state["groups"]:
                            del state["groups"][g]
                            # Remove from active groups if present
                            if g in state.get("active_groups", []):
                                state["active_groups"].remove(g)
                            persist_state()
                            # Update UI (with None-checks)
                            if group_select is not None:
                                group_select.options = list(state["groups"].keys())
                                group_select.value = None
                            if active_groups_select is not None:
                                active_groups_select.options = list(state["groups"].keys())
                            ui.notify(f"Group '{g}' deleted", color="positive")
                    
                    ui.button(icon="delete", color="negative", on_click=_delete_current_group).props("flat round").tooltip("Delete current group")

                # Add/Remove selected recipients to/from group
                with ui.row().classes("w-full gap-2 mt-4"):
                    def _add_selected_to_group() -> None:
                        g = state.get("current_group")
                        if not g:
                            ui.notify("No group selected", color="warning")
                            return
                        if table is None:
                            return
                        sel = table.selected or []
                        if not sel:
                            ui.notify("No recipients selected", color="warning")
                            return
                        
                        current_list = state["groups"].get(g, [])
                        added = 0
                        for row in sel:
                            addr = row["address"]
                            if addr not in current_list:
                                current_list.append(addr)
                                added += 1
                        state["groups"][g] = current_list
                        if persist_state():
                            refresh_table() # Update groups column
                            ui.notify(f"Added {added} recipients to '{g}'", color="positive")
                    
                    def _remove_selected_from_group() -> None:
                        g = state.get("current_group")
                        if not g:
                            ui.notify("No group selected", color="warning")
                            return
                        if table is None:
                            return
                        sel = table.selected or []
                        if not sel:
                            ui.notify("No recipients selected", color="warning")
                            return
                        
                        current_list = state["groups"].get(g, [])
                        removed = 0
                        for row in sel:
                            addr = row["address"]
                            if addr in current_list:
                                current_list.remove(addr)
                                removed += 1
                        state["groups"][g] = current_list
                        if persist_state():
                            refresh_table()
                            ui.notify(f"Removed {removed} recipients from '{g}'", color="positive")

                    ui.button("Add Selected to Group", icon="group_add", on_click=_add_selected_to_group).props("outline")
                    ui.button("Remove from Group", icon="group_remove", on_click=_remove_selected_from_group).props("outline color=warning")

            # Right: Active Groups
            with ui.card().classes("flex-1 p-4"):
                create_heading_row(
                    "Active Groups",
                    icon="how_to_reg",
                    title_classes="font-bold mb-2",
                    row_classes="items-center gap-2",
                    icon_classes="text-primary text-lg shrink-0",
                )
                ui.label("Recipients in active groups will receive emails.").classes("text-caption text-grey mb-2")
                
                active_groups_select = ui.select(
                    options=list(state["groups"].keys()),
                    value=state["active_groups"],
                    label="Active Groups",
                    multiple=True,
                ).classes("w-full").props("use-chips")
                
                def _apply_active_groups() -> None:
                    if active_groups_select is None:
                        return
                    state["active_groups"] = active_groups_select.value
                    if persist_state():
                        ui.notify("Active groups updated", color="positive")
                        refresh_overview()
                    _update_active_groups_apply_state()
                
                active_groups_apply_btn = create_action_button('apply', on_click=_apply_active_groups)
                active_groups_select.on('update:model-value', lambda _: _update_active_groups_apply_state())
                _update_active_groups_apply_state()

        ui.separator()

        # ---------------------- Notifications ------------------------- #
        create_heading_row(
            "Global Notifications",
            icon="notifications_active",
            title_classes="text-h6",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-xl shrink-0",
        )
        with ui.row().classes("items-center gap-4 flex-wrap"):
            notify_start_cb = ui.checkbox(
                'Send email on measurement start',
                value=bool(state["notifications"].get("on_start", False))
            )
            notify_end_cb = ui.checkbox(
                'Send email on measurement end',
                value=bool(state["notifications"].get("on_end", False))
            )
            notify_stop_cb = ui.checkbox(
                'Send email on measurement stop',
                value=bool(state["notifications"].get("on_stop", False))
            )
            ui.space()
            def apply_notifications() -> None:
                if notify_start_cb is None or notify_end_cb is None or notify_stop_cb is None:
                    return
                state["notifications"]["on_start"] = bool(notify_start_cb.value)
                state["notifications"]["on_end"] = bool(notify_end_cb.value)
                state["notifications"]["on_stop"] = bool(notify_stop_cb.value)
                if persist_state():
                    ui.notify("Notification settings applied", color="positive", position='bottom-right')
                    refresh_overview()
                _update_notifications_apply_state()
            notifications_apply_btn = create_action_button('apply')
            if notifications_apply_btn is not None:
                notifications_apply_btn.on('click', lambda _=None: apply_notifications())
            if notify_start_cb is not None:
                notify_start_cb.on('update:model-value', lambda _=None: _update_notifications_apply_state())
            if notify_end_cb is not None:
                notify_end_cb.on('update:model-value', lambda _=None: _update_notifications_apply_state())
            if notify_stop_cb is not None:
                notify_stop_cb.on('update:model-value', lambda _=None: _update_notifications_apply_state())
            _update_notifications_apply_state()

        ui.separator()

        # ---------------------- SMTP ---------------------------------- #
        create_heading_row(
            "SMTP",
            icon="mail",
            title_classes="text-subtitle2 text-grey-7",
            row_classes="items-center gap-2 mt-3",
            icon_classes="text-grey-7 text-lg shrink-0",
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
                    if email_system:
                        email_system.refresh_config()

        for inp in (sender_inp, server_inp, port_inp):
            inp.on("update:model-value", lambda _: update_status_icon())

        update_status_icon()

        with ui.row().classes("w-full justify-end mt-1"):
            create_action_button('save', label='Save SMTP', on_click=manual_save)

    # Initialize groups UI after controls are created
    def _init_groups() -> None:
        # Single canonical UI refresh for groups
        # refresh_groups_ui() # Not defined?
        # Also ensure Apply state reflects current selection after initial load
        try:
            _update_active_groups_apply_state()
            _update_notifications_apply_state()
        except Exception:
            pass
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
