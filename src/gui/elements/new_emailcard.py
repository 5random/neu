from typing import Dict, Optional, Any
import re
import asyncio

from nicegui import ui
from nicegui.client import Client

from src.config import AppConfig, save_config, logger
from src.alert import AlertSystem


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z0-9]{2,}$")

def create_emailcard(*, config: AppConfig, alert_system: Optional[AlertSystem] = None) -> None:
    """
    Karte mit drei Tabs:
    1) Übersicht   - read-only Konfig & Test-Mail
    2) Recipients  - Empfänger verwalten
    3) SMTP        - SMTP-Settings inkl. Live-Validierung
    """

    # ------------------------------------------------------------------ #
    # interner Zustand                                                   #
    # ------------------------------------------------------------------ #
    state: Dict = {
        "recipients": list(config.email.recipients),
        "smtp": {
            "server": config.email.smtp_server,
            "port": config.email.smtp_port,
            "sender": config.email.sender_email,
        },
    }

    recipient_list: Optional[ui.list] = None
    table: Optional[ui.table] = None
    email_inp: Optional[ui.input] = None
    smtp_labels: Dict[str, ui.label] = {}

    if alert_system is None:
        logger.error("Alert system is not initialized, email functionality will be disabled.")

    # ------------------------------------------------------------------ #
    # Hilfsfunktionen                                                    #
    # ------------------------------------------------------------------ #
    def persist_state() -> bool:
        """Speichert Konfiguration, meldet Fehler im UI."""
        try:
            config.email.recipients = list(state["recipients"])
            config.email.smtp_server = state["smtp"]["server"]
            config.email.smtp_port = int(state["smtp"]["port"])
            config.email.sender_email = state["smtp"]["sender"]
            save_config(config)
            if alert_system:
                alert_system.refresh_config()
                logger.info("Alert system configuration refreshed")
                
            logger.info(f"Configuration saved successfully: {config.email}")
            ui.notify("Config saved successfully", color="positive", position='bottom-right')
            return True
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"Tried to save config but failed: {exc}", color="negative", position='bottom-right')
            return False

    

    def is_valid_email(addr: str) -> bool:
        return bool(EMAIL_RE.match(addr))

    def validate_smtp(cfg: Dict) -> list[str]:
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
        
        # UI-Updates
        with client:
            ui.notify(f"Sending test email to {', '.join(state['recipients'])}", color="info", position='bottom-right')

        # Konfiguration speichern - OHNE try-except um UI-Konflikt zu vermeiden
        config.email.recipients = list(state["recipients"])
        config.email.smtp_server = state["smtp"]["server"]
        config.email.smtp_port = int(state["smtp"]["port"])
        config.email.sender_email = state["smtp"]["sender"]
        
        try:
            save_config(config)
            logger.info(f"Configuration saved successfully: {config.email}")
        except Exception as exc:
            error_msg = f"Failed to save config: {exc}"
            logger.error(error_msg)
            with client:
                ui.notify(error_msg, color="negative", position='bottom-right')
            return

        # E-Mail senden
        try:
            success = await alert_system.send_test_email_async()
        except Exception as e:
            error_msg = f"Failed to send test email: {e}"
            logger.error(error_msg)
            with client:
                ui.notify(error_msg, color="negative", position='bottom-right')
            return

        # Logging der Empfänger
        for i, recipient in enumerate(config.email.recipients):
            logger.info(f"Recipient {i+1}/{len(config.email.recipients)}: {recipient}")

        # Finale UI-Updates
        with client:
            if success:
                ui.notify(f"Test email sent successfully to all {len(state['recipients'])} recipients", color="positive", position='bottom-right')
            else:
                ui.notify("Error sending test email", color="negative", position='bottom-right')
        
        # Finales Logging
        if success:
            logger.info(f"Test email sent successfully to all {len(state['recipients'])} recipients")
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

    def refresh_recipient_list() -> None:
        if recipient_list is None:
            return
        recipient_list.clear()
        with recipient_list:
            for addr in state["recipients"]:
                ui.item(addr)

    def refresh_overview() -> None:
        """Aktualisiert die Overview-Anzeige"""
        refresh_recipient_list()
        for key in ["sender", "server", "port"]:
            if key in smtp_labels and smtp_labels[key] is not None:
                smtp_labels[key].text = str(state["smtp"][key])

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
        if persist_state():
            refresh_table()
            table.selected = []
            refresh_overview()
            if alert_system:
                alert_system.refresh_config()
            ui.notify(f"{len(selected_addresses)} address(es) deleted", color="positive", position='bottom-right')
            logger.info(f"Deleted {len(selected_addresses)} recipient(s): {', '.join(selected_addresses)}; new total recipients: {len(state['recipients'])}")

    # ------------------------------------------------------------------ #
    # Haupt-Card                                                         #
    # ------------------------------------------------------------------ #
    with ui.card().classes("w-full flex-shrink-0"):
        ui.label("Email Settings").classes("text-h6 font-semibold mb-2")

        # Tabs
        with ui.tabs() as tabs:
            tab_overview = ui.tab("Overview").tooltip("Overview of the email configuration")
            tab_rcp = ui.tab("Recipients").tooltip("Manage email recipients")
            tab_smtp = ui.tab("SMTP").tooltip("Configure SMTP settings")

        # Panels
        with ui.tab_panels(tabs, value=tab_overview).classes("w-full"):

            # ---------------------- Übersicht --------------------------- #
            with ui.tab_panel(tab_overview):
                with ui.column().classes("gap-2"):
                    ui.label("Recipients").classes("text-subtitle2 text-grey-7 mt-1")
                    ui.label().bind_text_from(state, "recipients", backward=lambda x: f"({len(x)})").classes("text-caption text-grey-5")


                    recipient_list = ui.list().props("dense").classes("pl-2")
                    refresh_recipient_list()

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

    # ------------------------------------------------------------------ #
    # Tabelle initial befüllen                                           #
    # ------------------------------------------------------------------ #
    refresh_table()