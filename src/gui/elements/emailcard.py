from typing import Dict
import re
from nicegui import ui
from src.config import AppConfig, save_config
from src.alert import AlertSystem

def create_emailcard(*, config: AppConfig) -> None:
    state: Dict = {
        "recipients": list(config.email.recipients),
        "smtp": {
            "server": config.email.smtp_server,
            "port": config.email.smtp_port,
            "sender": config.email.sender_email,
        },
    }

    def persist_state() -> None:
        """Datei-, DB- oder REST-Persistenz einbauen"""
        config.email.recipients = list(state["recipients"])
        config.email.smtp_server = state["smtp"]["server"]
        config.email.smtp_port = int(state["smtp"]["port"])
        config.email.sender_email = state["smtp"]["sender"]
        save_config(config)

    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z0-9]{2,}$")

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

    def refresh_table() -> None:
        table.rows = [{"address": addr} for addr in state["recipients"]]
        table.update()

    def add_recipient() -> None:
        addr = (email_inp.value or "").strip()
        if not is_valid_email(addr):
            ui.notify("Invalid email address", color="negative")
            return
        if addr in state["recipients"]:
            ui.notify("Address already exists", color="warning")
            return

        state["recipients"].append(addr)
        email_inp.value = ""
        persist_state()
        refresh_table()

    def delete_selected() -> None:
        selected_rows = table.selected or []
        if not selected_rows:
            return
        selected_addresses = [row["address"] for row in selected_rows]
        state["recipients"] = [
            addr for addr in state["recipients"] if addr not in selected_addresses
        ]
        persist_state()
        refresh_table()
        table.selected = []
        ui.notify(f"{len(selected_addresses)} address(es) deleted", color="positive")


    # -- Alles in einer Karte ----------------------------------------------------
    with ui.card().style("align-self:stretch; flex-direction:column; flex-wrap:wrap;").style("align-self:stretch; width: 100%; flex-direction:column; flex-wrap:wrap;").classes("w-full"):
        ui.label('Email Settings')\
                .style("align-self:flex-start; display:block;")\
                .classes('text-h6 font-semibold mb-2')
        # -- Tabs -----------------------------------------------------------------
        with ui.tabs() as tabs:
            tab_rcp  = ui.tab("Recipients").tooltip("Manage email recipients")
            tab_smtp = ui.tab("SMTP").tooltip("Configure SMTP settings")

        # -- Tab-Panels -----------------------------------------------------------
        with ui.tab_panels(tabs, value=tab_rcp).classes("w-full"):

            # Panel: Empfänger
            with ui.tab_panel(tab_rcp):
                with ui.row().classes("items-center gap-2"):
                    email_inp = (
                        ui.input("New Email")
                        .classes("w-64")
                        .on("keyup.enter", lambda _: add_recipient())
                    ).tooltip("Enter email address and press Enter to add or the add button")
                    
                    with ui.button(
                        icon='add_circle',
                        color="primary",
                        on_click=lambda _: add_recipient(),
                    ).bind_enabled_from(email_inp, "value", is_valid_email).props('round'):
                        ui.tooltip('Add email address')

                ui.separator()

                table = ui.table(
                    columns=[{"name": "address", "label": "Address", "field": "address"}],
                    rows=[],
                    row_key="address",
                    selection="multiple",
                ).classes("w-full")

                # Hier: volle Breite für die Button-Zeile, damit ml-auto wirkt
                with ui.row().classes("w-full items-center gap-2 mt-2"):
                    with ui.button(
                        icon='delete',
                        color="negative",
                        on_click=lambda _: delete_selected(),
                    ).bind_enabled_from(table, "selected", lambda s: bool(s)).props('round'):
                        ui.tooltip('Delete selected email addresses')

            # Panel: SMTP-Einstellungen
            with ui.tab_panel(tab_smtp):
                smtp = state["smtp"]

                with ui.row().classes("items-center gap-2"):
                    ui.input("Sender").bind_value(smtp, "sender").tooltip("Email address of the sender")
                    ui.input("Server").bind_value(smtp, "server").tooltip("SMTP server address")
                    with ui.number("Port", min=1, max=65535).bind_value(smtp, "port", forward=int):
                        ui.tooltip("Port must be between 1 and 65535.")


                def attempt_save():
                    errors = validate_smtp(smtp)
                    if errors:
                        ui.notify(" ".join(errors), color="negative")
                    else:
                        persist_state()
                        ui.notify("Saved", color="positive")
                
                def send_test_email():
                        alert = AlertSystem(config.email, config.measurement, config)
                        if alert.send_test_email():
                            ui.notify("Test email sent successfully", color="positive")
                        else:
                            ui.notify("Error sending test email", color="negative")

                with ui.row().classes('w-full items-center'):
                    ui.button(icon='save', color="primary", on_click=attempt_save).props('round').tooltip('Save SMTP settings')
                    ui.element('div').classes('w-px h-8 bg-gray-300 mx-2')
                    ui.button(icon='send', color="info", on_click= lambda _: send_test_email()).props('round').tooltip('Send test email to all recipients')

    # Tabellen-Inhalt initial laden
    refresh_table()
