from typing import Dict
import re
from nicegui import ui
from src.config import save_config, load_config
from src.alert import AlertSystem

def create_emailcard():
    config = load_config()
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
            errors.append("Absenderadresse ist ungültig.")
        if not cfg.get("server"):
            errors.append("SMTP-Server darf nicht leer sein.")
        port = cfg.get("port")
        if not isinstance(port, (int, float)) or not 1 <= int(port) <= 65535:
            errors.append("Port muss zwischen 1 und 65535 liegen.")
        return errors

    def refresh_table() -> None:
        table.rows = [{"address": addr} for addr in state["recipients"]]
        table.update()

    def add_recipient() -> None:
        addr = (email_inp.value or "").strip()
        if not is_valid_email(addr):
            ui.notify("Ungültige E-Mail-Adresse", color="negative")
            return
        if addr in state["recipients"]:
            ui.notify("Adresse existiert bereits", color="warning")
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
        ui.notify(f"{len(selected_addresses)} Adresse(n) gelöscht", color="positive")


    # -- Alles in einer Karte ----------------------------------------------------
    with ui.card().classes("w-full max-w-2xl"):

        # -- Tabs -----------------------------------------------------------------
        with ui.tabs() as tabs:
            tab_rcp  = ui.tab("Empfänger")
            tab_smtp = ui.tab("SMTP")

        # -- Tab-Panels -----------------------------------------------------------
        with ui.tab_panels(tabs, value=tab_rcp).classes("w-full"):

            # Panel: Empfänger
            with ui.tab_panel(tab_rcp):
                with ui.row().classes("items-center gap-2"):
                    email_inp = (
                        ui.input("Neue E-Mail")
                        .classes("w-64")
                        .on("keyup.enter", lambda _: add_recipient())
                    )
                    with ui.button(
                        icon='add_circle',
                        color="primary",
                        on_click=lambda _: add_recipient(),
                    ).bind_enabled_from(email_inp, "value", is_valid_email).props('round'):
                        ui.tooltip('E-Mail-Adresse hinzufügen')

                ui.separator()

                table = ui.table(
                    columns=[{"name": "address", "label": "Adresse", "field": "address"}],
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
                        ui.tooltip('Ausgewählte E-Mail-Adressen löschen')

                    def send_test_email():
                        alert = AlertSystem(config.email, config.measurement, config)
                        if alert.send_test_email():
                            ui.notify("Test-E-Mail erfolgreich gesendet", color="positive")
                        else:
                            ui.notify("Fehler beim Senden der Test-E-Mail", color="negative")


                    with ui.button(
                        icon='send',
                        color="info",
                        on_click= lambda _: send_test_email()
                    ).props('round').classes("ml-auto"):
                        ui.tooltip('Test-E-Mail an alle Empfänger senden')

            # Panel: SMTP-Einstellungen
            with ui.tab_panel(tab_smtp):
                smtp = state["smtp"]

                with ui.row().classes("items-center gap-2"):
                    ui.input("Absender").bind_value(smtp, "sender")
                    ui.input("SMTP-Server").bind_value(smtp, "server")
                    with ui.number("Port", min=1, max=65535).bind_value(smtp, "port", forward=int):
                        ui.tooltip("Port muss zwischen 1 und 65535 liegen!")


                def attempt_save():
                    errors = validate_smtp(smtp)
                    if errors:
                        ui.notify(" ".join(errors), color="negative")
                    else:
                        persist_state()
                        ui.notify("Gespeichert", color="positive")

                with ui.button(icon='save', color="primary", on_click=attempt_save).props('round'):
                    ui.tooltip('SMTP-Einstellungen speichern')

    # Tabellen-Inhalt initial laden
    refresh_table()
