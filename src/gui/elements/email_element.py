from typing import Dict
import re
from nicegui import ui

state: Dict = {
    "recipients": [],          # List[str] – nur E-Mail-Adressen
    "smtp": {
        "host": "",
        "port": 587,
        "user": "",
        "password": "",
        "sender": "",
        "use_tls": True,
    },
}

def persist_state() -> None:
    """TODO: Datei-, DB- oder REST-Persistenz einbauen"""
    pass

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z0-9]{2,}$")

def is_valid_email(addr: str) -> bool:
    return bool(EMAIL_RE.match(addr))

def validate_smtp(cfg: Dict) -> list[str]:
    errors: list[str] = []
    if not is_valid_email(cfg.get("sender", "")):
        errors.append("Absenderadresse ist ungültig.")
    if not cfg.get("host"):
        errors.append("SMTP-Host darf nicht leer sein.")
    port = cfg.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        errors.append("Port muss zwischen 1 und 65535 liegen.")
    return errors

with ui.header().classes("items-center"):
    ui.label("E-Mail-Alert-Service • Skeleton").classes("text-lg font-bold")

# -- Tabs ---------------------------------------------------------------------
with ui.tabs() as tabs:
    tab_rcp  = ui.tab("Empfänger")
    tab_smtp = ui.tab("SMTP")

# -- Tab-Panels ---------------------------------------------------------------
with ui.tab_panels(tabs, value=tab_rcp).classes("w-full"):

    # ------------------ Panel: Empfänger -------------------------------------
    with ui.tab_panel(tab_rcp):

        email_inp = (
            ui.input("Neue E-Mail")
            .classes("w-64")
            .on("keyup.enter", lambda _: add_recipient())  # Enter-Support
        )

        ui.button(
            "Hinzufügen",
            color="primary",
            on_click=lambda _: add_recipient(),
        ).bind_enabled_from(email_inp, "value", is_valid_email)

        ui.separator()

        table = ui.table(
            columns=[{"name": "address", "label": "Adresse", "field": "address"}],
            rows=[],
            row_key="address",
            selection="multiple",
        ).classes("w-full")

        ui.button(
            "Löschen (Auswahl)",
            color="negative",
            on_click=lambda _: delete_selected(),
        ).bind_enabled_from(table, "selected", lambda s: bool(s))

    # ------------------ Panel: SMTP-Einstellungen ----------------------------
    with ui.tab_panel(tab_smtp):

        smtp = state["smtp"]

        ui.input("Absender").bind_value(smtp, "sender")
        ui.input("SMTP-Host").bind_value(smtp, "host")
        ui.number("Port").bind_value(smtp, "port")
        ui.input("Benutzer").bind_value(smtp, "user")
        ui.input(
            "Passwort",
            password=True,
            password_toggle_button=True,
        ).bind_value(smtp, "password")
        ui.checkbox("TLS verwenden").bind_value(smtp, "use_tls")

        def attempt_save():
            errors = validate_smtp(smtp)
            if errors:
                ui.notify(" ".join(errors), color="negative")
            else:
                persist_state()                     # TODO
                ui.notify("Gespeichert (nur RAM)", color="positive")

        ui.button("Speichern", color="primary", on_click=attempt_save)

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
    persist_state()              # TODO
    refresh_table()

def delete_selected() -> None:
    selected_rows = table.selected or []
    if not selected_rows:
        return
    # Extrahiere alle ausgewählten Adressen
    selected_addresses = [row["address"] for row in selected_rows]
    # Entferne sie aus dem State
    state["recipients"] = [
        addr for addr in state["recipients"] if addr not in selected_addresses
    ]
    persist_state()              # TODO
    refresh_table()
    table.selected = []          # Auswahl zurücksetzen
    ui.notify(f"{len(selected_addresses)} Adresse(n) gelöscht", color="positive")

refresh_table()
ui.run(title="Alert-Service", reload=False)
