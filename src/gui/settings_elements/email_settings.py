from typing import Optional
import asyncio

from nicegui import ui, background_tasks
from nicegui import events
from nicegui.client import Client

from src.config import get_global_config, save_global_config, get_logger
from src.config import EmailConfig as _EmailConfig
from src.notify import EMailSystem
from src.gui.util import schedule_bg

logger = get_logger('gui.email')

# Reuse the central email regex from EmailConfig for consistency
EMAIL_RE = _EmailConfig.EMAIL_RE

# Hard limit for group names (not configurable via GUI)
GROUP_NAME_MAX_LEN = 20

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
    email_inp: Optional[ui.input] = None
    smtp_labels: dict[str, ui.label] = {}
    overview_counts_base: Optional[ui.label] = None
    overview_counts_eff: Optional[ui.label] = None
    notif_labels: dict[str, ui.label] = {}
    # Notification UI refs for enable/disable handling
    notifications_apply_btn: Optional[ui.button] = None
    notify_start_cb: Optional[ui.checkbox] = None
    notify_end_cb: Optional[ui.checkbox] = None
    # Per-recipient notification preferences (from config)
    recipient_prefs: dict[str, dict[str, bool]] = dict(getattr(config.email, 'recipient_prefs', {}) or {})

    if email_system is None:
        logger.error("Email system is not initialized, email functionality will be disabled.")

    # ------------------------------------------------------------------ #
    # Hilfsfunktionen                                                    #
    # ------------------------------------------------------------------ #
    def _summarize_email_config(email_cfg) -> str:
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

    async def send_async_test_email(client: Client) -> None:
        """Asynchrone Funktion zum Senden einer Test-E-Mail."""
        # Alle Variablen außerhalb der try-Blöcke deklarieren
        success = False
        error_msg = ""
        
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

    def send_test_email() -> None:
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
        schedule_bg(send_async_test_email(client), name='send_test_email')

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
        nonlocal notifications_apply_btn, notify_start_cb, notify_end_cb
        try:
            if notifications_apply_btn is None or notify_start_cb is None or notify_end_cb is None:
                return
            cur_start = bool(state.get("notifications", {}).get("on_start", False))
            cur_end = bool(state.get("notifications", {}).get("on_end", False))
            sel_start = bool(getattr(notify_start_cb, 'value', cur_start))
            sel_end = bool(getattr(notify_end_cb, 'value', cur_end))
            if (cur_start == sel_start) and (cur_end == sel_end):
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
                if 'group_name_inp' in locals():
                    pass  # placeholder for linter
                if group_name_inp is not None:
                    group_name_inp.value = name
                if table is not None:
                    target = set(state["groups"].get(name, []) or [])
                    table.selected = [r for r in (table.rows or []) if r.get("address") in target]
                    table.update()
                if group_select is not None and getattr(group_select, 'value', None) != name:
                    group_select.value = name
                    group_select.update()
            else:
                state["current_group"] = None
                if group_name_inp is not None:
                    group_name_inp.value = ""
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

        # Apply change and persist; migrate stored preferences if present
        state['recipients'][idx] = new_addr
        try:
            if old_addr in recipient_prefs:
                recipient_prefs[new_addr] = recipient_prefs.pop(old_addr)
        except Exception:
            logger.exception('Failed to migrate recipient prefs on rename')
        # Update groups memberships
        for gname, addrs in list(state["groups"].items()):
            state["groups"][gname] = [new_addr if a == old_addr else a for a in addrs]
        if persist_state():
            refresh_table()
            refresh_overview()
            if email_system:
                email_system.refresh_config()
            logger.info('Recipient updated: %s -> %s', old_addr, new_addr)
            ui.notify('Recipient updated', color='positive', position='bottom-right')
        else:
            # rollback in-memory state on failure
            state['recipients'][idx] = old_addr
            refresh_table()
            ui.notify('Failed to save changes', color='negative', position='bottom-right')
            logger.error('Failed to persist state after rename (rolled back)')

    # ---------------------- Overview --------------------------- #
    logger.info("Creating email settings (list layout)")
    ui.label("Overview").classes("text-subtitle2 text-grey-7 mt-1")
    with ui.column().classes("gap-2"):
        # Top row: counts on the left, send test email on the right
        with ui.row().classes("items-center justify-between w-full gap-3 flex-wrap"):
            with ui.row().classes("items-center gap-4 flex-wrap"):
                with ui.row().classes("items-baseline gap-2"):
                    ui.label("Configured recipients").classes("text-subtitle2 text-grey-7")
                    overview_counts_base = ui.label("").classes("text-caption text-grey-5")
                with ui.row().classes("items-baseline gap-2"):
                    ui.label("Effective recipients").classes("text-subtitle2 text-grey-7")
                    overview_counts_eff = ui.label("").classes("text-caption text-grey-5")
            ui.button("Send test email", icon="send", color="info", on_click=lambda _: send_test_email()).props("unelevated")

        # Lists
        with ui.grid(columns=2).classes("w-full gap-4"):
            with ui.column().classes("gap-2"):
                ui.label("Configured recipients").classes("text-caption text-grey-7")
                recipient_list = ui.list().props("dense bordered").classes("pl-2")
                refresh_recipient_list()
            with ui.column().classes("gap-2"):
                ui.label("Effective recipients (used for sending)").classes("text-caption text-grey-7")
                eff_recipient_list = ui.list().props("dense bordered").classes("pl-2")
                refresh_effective_list()

        # Summary of notification flags and SMTP snapshot
        with ui.row().classes("items-center gap-4 flex-wrap"):
            ui.label("Notification emails").classes("text-subtitle2 text-grey-7")
            ui.label("On start:").classes("text-grey-6")
            notif_labels['start'] = ui.label("").classes("text-caption text-grey")
            ui.label("On end:").classes("text-grey-6 ml-2")
            notif_labels['end'] = ui.label("").classes("text-caption text-grey")
        with ui.row().classes("items-center gap-4 flex-wrap"):
            ui.label("SMTP Settings").classes("text-subtitle2 text-grey-7")
            for key, label_txt in [("sender", "Sender"), ("server", "Server"), ("port", "Port")]:
                ui.label(f"{label_txt}:").classes("text-grey-6")
                smtp_labels[key] = ui.label(str(state["smtp"][key])).classes("text-caption")

    ui.separator()

    # ---------------------- Recipients & Groups (two columns) --- #
    with ui.row().classes("w-full gap-4 items-start flex-wrap"):
        # Left column: email input + add/delete
        with ui.column().classes("gap-2 min-w-[320px] flex-1"):
            ui.label("Recipients").classes("text-subtitle2 text-grey-7")
            email_inp = (
                ui.input("New Email")
                .props("dense outlined stack-label")
                .classes("min-w-[260px] sm:w-[360px] md:w-[420px]")
                .on("keyup.enter", lambda _: add_recipient())
            ).tooltip("Enter email address and press Enter to add or click Add")
            with ui.row().classes("gap-2"):
                ui.button("Add", icon="add_circle", color="primary", on_click=lambda _: add_recipient())\
                    .bind_enabled_from(email_inp, "value", is_valid_email)
                ui.button("Delete selected", icon="delete", color="negative", on_click=lambda _: delete_selected())\
                    .bind_enabled_from(lambda: table, "selected", lambda s: bool(s))

        # Right column: group select + name (same row) + save/delete
        with ui.column().classes("gap-2 min-w-[320px] flex-1"):
            ui.label("Recipient Groups").classes("text-subtitle2 text-grey-7")
            # Keep inputs aligned and prevent label-floating layout shifts
            with ui.row().classes("items-end gap-2 w-full flex-wrap"):
                group_select = ui.select(
                    options=list(state["groups"].keys()),
                    value=None,
                    label="Select group to edit",
                    clearable=True,
                ).props("dense outlined stack-label").classes("min-w-[260px] sm:w-[360px] md:w-[420px]")
                group_select.on('update:model-value', lambda _=None: apply_group_selection(group_select.value))
                group_name_inp = (
                    ui.input("Group name")
                    .props(f"maxlength={GROUP_NAME_MAX_LEN} counter dense outlined stack-label")
                    .tooltip(f"Max {GROUP_NAME_MAX_LEN} characters")
                    .classes("min-w-[260px] sm:w-[360px] md:w-[420px]")
                )

            def _validate_group_name(name: str) -> bool:
                n = (name or "").strip()
                if not n:
                    ui.notify("Please enter a group name", color="warning", position='bottom-right')
                    return False
                if len(n) > GROUP_NAME_MAX_LEN:
                    ui.notify(f"Group name too long (max {GROUP_NAME_MAX_LEN} characters)", color="negative", position='bottom-right')
                    return False
                return True

            def save_group() -> None:
                name = (group_name_inp.value or "").strip()
                if not _validate_group_name(name):
                    return
                selected = [r["address"] for r in (table.selected or [])] if table else []
                if not selected:
                    ui.notify("Please select at least one recipient", color="warning", position='bottom-right'); return
                sanitized = sanitize_group_addresses(selected)
                if len(sanitized) != len(selected):
                    removed = [a for a in selected if a not in sanitized]
                    logger.warning("Group '%s': removed invalid/duplicate address(es): %s", name, ", ".join(removed))
                    ui.notify(
                        f"Ignored invalid/duplicate addresses: {', '.join(removed)}",
                        color="warning",
                        position='bottom-right',
                    )
                state["groups"][name] = sanitized
                state["current_group"] = name
                if persist_state():
                    refresh_groups_ui()
                    refresh_table()
                    if group_select is not None:
                        group_select.value = name
                        group_select.update()
                    ui.notify(f"Group '{name}' saved", color="positive", position='bottom-right')
            with ui.row().classes("gap-2"):
                ui.button("Save group", icon="save", color="primary", on_click=lambda _: save_group())

                # Delete group (with confirmation)
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
                            apply_group_selection(None)
                            if persist_state():
                                refresh_groups_ui()
                                refresh_table()
                                refresh_overview()
                                ui.notify(f"Group '{name}' deleted", color="positive", position='bottom-right')
                            confirm_dialog.close()
                        confirm_btn = ui.button("Delete", color="negative", on_click=lambda _: _confirm_delete(state.get("pending_delete_group", "")))

                def delete_group() -> None:
                    name = (group_name_inp.value or "").strip()
                    if not name:
                        ui.notify("Select a valid group to delete", color="warning", position='bottom-right'); return
                    confirm_text.text = f"Group '{name}' will be deleted. This cannot be undone."
                    state["pending_delete_group"] = name
                    confirm_dialog.open()
                ui.button("Delete group", icon="delete", color="negative", on_click=lambda _: delete_group())

        # The recipients table (full width)
        table = ui.table(
            columns=[
                {"name": "address", "label": "Address", "field": "address"},
                {"name": "groups", "label": "Groups", "field": "groups"},
                {"name": "on_start", "label": "On start", "field": "on_start"},
                {"name": "on_end", "label": "On end", "field": "on_end"},
                {"name": "on_stop", "label": "On stop", "field": "on_stop"},
            ],
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

        # Per-recipient preference toggles
        def _on_toggle_pref(e: events.GenericEventArguments) -> None:
            try:
                payload = e.args or {}
                addr = (payload.get('address') or '').strip()
                key = (payload.get('key') or '').strip()
                val = bool(payload.get('value', False))
                if not addr or key not in ('on_start', 'on_end', 'on_stop'):
                    return
                if addr not in state['recipients']:
                    return
                rp = recipient_prefs.get(addr, {}) or {}
                rp[key] = val
                recipient_prefs[addr] = rp
                persist_state()
                refresh_table()
            except Exception:
                logger.exception('Failed to toggle recipient pref')

        table.add_slot('body-cell-on_start', r'''
            <q-td :props="props">
                <q-checkbox size="sm" :model-value="props.row.on_start"
                            @update:model-value="(val) => $parent.$emit('toggle_pref', { address: props.row.address, key: 'on_start', value: val })"/>
            </q-td>
        ''')
        table.add_slot('body-cell-on_end', r'''
            <q-td :props="props">
                <q-checkbox size="sm" :model-value="props.row.on_end"
                            @update:model-value="(val) => $parent.$emit('toggle_pref', { address: props.row.address, key: 'on_end', value: val })"/>
            </q-td>
        ''')
        table.add_slot('body-cell-on_stop', r'''
            <q-td :props="props">
                <q-checkbox size="sm" :model-value="props.row.on_stop"
                            @update:model-value="(val) => $parent.$emit('toggle_pref', { address: props.row.address, key: 'on_stop', value: val })"/>
            </q-td>
        ''')
        table.on('toggle_pref', _on_toggle_pref)

        # Groups display with popup for full list
        table.add_slot('body-cell-groups', r'''
            <q-td :props="props">
                <div class="cursor-pointer text-primary" style="white-space: normal; word-break: break-word;">
                    {{ props.row.groups || '—' }}
                    <q-popup-proxy transition-show="scale" transition-hide="scale">
                        <q-card style="min-width: 280px; max-width: 520px;">
                            <q-card-section>
                                <div class="text-subtitle2">Groups for {{ props.row.address }}</div>
                            </q-card-section>
                            <q-separator />
                            <q-card-section>
                                <div v-if="props.row.groups_list && props.row.groups_list.length" class="q-gutter-xs">
                                    <q-chip v-for="g in props.row.groups_list" :key="g" color="primary" text-color="white" dense>{{ g }}</q-chip>
                                </div>
                                <div v-else class="text-grey">No groups</div>
                            </q-card-section>
                            <q-separator />
                            <q-card-actions align="right">
                                <q-btn flat label="Close" v-close-popup />
                            </q-card-actions>
                        </q-card>
                    </q-popup-proxy>
                </div>
            </q-td>
        ''')

        # Existing groups list (below table)
        ui.separator()
        ui.label("Existing groups").classes("text-caption text-grey-7 mt-1")
        groups_list = ui.list().props("bordered dense")

        def load_group(name: str) -> None:
            # Toggle behavior: clicking the same group again clears the current selection/state
            if state.get("current_group") == name:
                apply_group_selection(None)
            else:
                apply_group_selection(name)

        def refresh_groups_ui() -> None:
            groups_list.clear()
            with groups_list:
                with ui.row().classes("q-gutter-xs flex-wrap"):
                    for gname in sorted(state["groups"].keys()):
                        selected = state.get("current_group") == gname
                        icon = "close" if selected else "radio_button_unchecked"
                        (
                            ui.chip(gname)
                            .props(f"color=primary text-color=white dense clickable icon={icon}")
                            .classes("cursor-pointer")
                            .on_click(lambda _=None, name=gname: load_group(name))
                        )
            if group_select is not None:
                group_select.options = list(state["groups"].keys())
                group_select.value = state.get("current_group")
                group_select.update()
            if active_groups_select is not None:
                active_groups_select.options = list(state["groups"].keys())
                active_groups_select.value = list(state["active_groups"])
                active_groups_select.update()

    ui.separator()

    # Active groups selection
    with ui.row().classes("items-center w-full gap-3"):
        ui.label("Active groups (used for sending)").classes("text-subtitle2 text-grey-7")
        ui.space()
        active_groups_select = ui.select(
            options=list(state["groups"].keys()),
            value=list(state["active_groups"]),
            multiple=True,
            label="Active groups",
        ).classes("min-w-[280px]")
        active_groups_select.on('update:model-value', lambda _=None: _update_active_groups_apply_state())
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
            _update_active_groups_apply_state()
        active_groups_apply_btn = ui.button("Apply", icon="done", color="primary")
        active_groups_apply_btn.on('click', lambda _=None: apply_active_groups())
        _update_active_groups_apply_state()

    ui.separator()

    # Measurement notifications toggles
    with ui.row().classes("items-center w-full gap-3"):
        ui.label("Measurement notifications").classes("text-subtitle2 text-grey-7")
        notify_start_cb = ui.checkbox(
            'Send email on measurement start',
            value=bool(state["notifications"].get("on_start", False))
        )
        notify_end_cb = ui.checkbox(
            'Send email on measurement end',
            value=bool(state["notifications"].get("on_end", False))
        )
        ui.space()
        def apply_notifications() -> None:
            state["notifications"]["on_start"] = bool(notify_start_cb.value)
            state["notifications"]["on_end"] = bool(notify_end_cb.value)
            if persist_state():
                ui.notify("Notification settings applied", color="positive", position='bottom-right')
                refresh_overview()
            _update_notifications_apply_state()
        notifications_apply_btn = ui.button("Apply", icon="done", color="primary")
        notifications_apply_btn.on('click', lambda _=None: apply_notifications())
        notify_start_cb.on('update:model-value', lambda _=None: _update_notifications_apply_state())
        notify_end_cb.on('update:model-value', lambda _=None: _update_notifications_apply_state())
        _update_notifications_apply_state()

    ui.separator()

    # ---------------------- SMTP ---------------------------------- #
    ui.label("SMTP").classes("text-subtitle2 text-grey-7 mt-3")
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
        ui.button("Save SMTP", icon="save", color="primary", on_click=manual_save)

    # Initialize groups UI after controls are created
    def _init_groups():
        # Single canonical UI refresh for groups
        refresh_groups_ui()
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