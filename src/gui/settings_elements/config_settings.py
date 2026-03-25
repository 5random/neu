from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from nicegui import app, ui
from nicegui.events import UploadEventArguments

from src.config import (
    ConfigImportPreview,
    analyze_imported_config_text,
    apply_imported_config_preview,
    get_global_config,
    get_logger,
    sync_runtime_config_instances,
)

logger = get_logger("gui.config_settings")


def _format_value(value: Any, *, limit: int = 140) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def create_config_settings(
    *,
    camera: Optional[Any] = None,
    measurement_controller: Optional[Any] = None,
    email_system: Optional[Any] = None,
) -> None:
    """Render config download plus safe config import preview and apply actions."""

    cfg = get_global_config()
    if cfg is None:
        ui.label("Configuration not available").classes("text-red")
        logger.error("Configuration not available - cannot create config settings")
        return

    state: dict[str, Any] = {
        "preview": None,
        "source_name": "",
        "source_text": "",
    }

    ready_table: Optional[Any] = None
    same_table: Optional[Any] = None
    issue_table: Optional[Any] = None
    summary_label: Optional[Any] = None
    error_label: Optional[Any] = None
    apply_selected_btn: Optional[Any] = None
    apply_all_btn: Optional[Any] = None

    def download_config_yaml() -> None:
        cfg_dir = Path("config")
        cfg_file = cfg_dir / "config.yaml"
        if not cfg_file.exists():
            ui.notify("Config file not found (config/config.yaml)", type="warning", position="bottom-right")
            return
        try:
            app.add_static_files("/config", str(cfg_dir))
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
        ui.download.from_url("/config/config.yaml")
        ui.notify("Config downloaded", type="positive", position="bottom-right")
        logger.info("Config file offered for download")

    def _rows_for_entries(entries: list[Any], *, include_status: bool) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in entries:
            row = {
                "path": entry.path,
                "current": _format_value(entry.current_value),
                "imported": _format_value(entry.imported_value),
                "reason": entry.reason or "-",
            }
            if include_status:
                row["status"] = entry.status
            rows.append(row)
        return rows

    def _update_action_state() -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        ready_paths = list(preview.ready_updates.keys()) if preview is not None else []
        if apply_all_btn is not None:
            apply_all_btn.enable() if ready_paths else apply_all_btn.disable()
        if apply_selected_btn is not None and ready_table is not None:
            selected_paths = [row.get("path") for row in (ready_table.selected or []) if row.get("path")]
            apply_selected_btn.enable() if selected_paths else apply_selected_btn.disable()

    def _refresh_preview() -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        ready_entries = []
        same_entries = []
        issue_entries = []
        if preview is not None:
            ready_entries = [entry for entry in preview.entries if entry.status == "ready"]
            same_entries = [entry for entry in preview.entries if entry.status == "same"]
            issue_entries = [
                entry
                for entry in preview.entries
                if entry.status in {"invalid", "missing", "unknown"}
            ]

        if ready_table is not None:
            ready_table.rows = _rows_for_entries(ready_entries, include_status=False)
            ready_table.selected = []
            ready_table.update()
        if same_table is not None:
            same_table.rows = _rows_for_entries(same_entries, include_status=False)
            same_table.update()
        if issue_table is not None:
            issue_table.rows = _rows_for_entries(issue_entries, include_status=True)
            issue_table.update()

        if summary_label is not None:
            if preview is None:
                summary_label.text = "No imported config analysed yet."
            else:
                summary_label.text = (
                    f"Source: {preview.source_name} | "
                    f"transferable: {preview.count('ready')} | "
                    f"already matching: {preview.count('same')} | "
                    f"invalid: {preview.count('invalid')} | "
                    f"missing: {preview.count('missing')} | "
                    f"unsupported: {preview.count('unknown')}"
                )
            summary_label.update()

        if error_label is not None:
            error_label.text = " | ".join(preview.errors) if preview and preview.errors else ""
            error_label.update()

        _update_action_state()

    def _reanalyse_current_source() -> None:
        source_text = state["source_text"]
        source_name = state["source_name"] or "uploaded config"
        if not source_text:
            return
        state["preview"] = analyze_imported_config_text(source_text, source_name=source_name)
        _refresh_preview()

    def _apply_selected(selected_paths: Optional[list[str]]) -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        if preview is None:
            ui.notify("Upload and analyse a config file first", type="warning", position="bottom-right")
            return
        result = apply_imported_config_preview(preview, selected_paths=selected_paths, persist=True)
        if not result.ok:
            ui.notify("; ".join(result.errors), type="negative", position="bottom-right")
            logger.warning("Config import failed: %s", "; ".join(result.errors))
            return

        active_config = get_global_config()
        if active_config is not None:
            sync_result = sync_runtime_config_instances(
                active_config,
                applied_paths=result.applied_paths,
                camera=camera,
                measurement_controller=measurement_controller,
                email_system=email_system,
            )
            if sync_result.errors:
                warning_text = "; ".join(sync_result.errors)
                ui.notify(
                    f"Config imported, but runtime sync was partial: {warning_text}",
                    type="warning",
                    position="bottom-right",
                )
                logger.warning("Config import runtime sync issues: %s", warning_text)

        ui.notify(
            f"Imported {len(result.applied_paths)} config setting(s)",
            type="positive",
            position="bottom-right",
        )
        logger.info("Imported config settings: %s", ", ".join(result.applied_paths))
        _reanalyse_current_source()

    async def _handle_upload(event: UploadEventArguments) -> None:
        filename = event.file.name or "uploaded_config.yaml"
        suffix = Path(filename).suffix.lower()
        if suffix not in {".yaml", ".yml"}:
            ui.notify("Only .yaml or .yml files are supported", type="warning", position="bottom-right")
            return
        try:
            text = await event.file.text()
        except Exception as exc:
            logger.error("Failed to read uploaded config: %s", exc, exc_info=True)
            ui.notify(f"Could not read uploaded file: {exc}", type="negative", position="bottom-right")
            return

        state["source_name"] = filename
        state["source_text"] = text
        state["preview"] = analyze_imported_config_text(text, source_name=filename)
        _refresh_preview()

        preview: ConfigImportPreview = state["preview"]
        if preview.errors:
            ui.notify("Uploaded config could not be parsed", type="negative", position="bottom-right")
            logger.warning("Uploaded config '%s' could not be parsed: %s", filename, "; ".join(preview.errors))
            return
        ui.notify(
            f"Config analysed: {preview.count('ready')} transferable, "
            f"{preview.count('invalid') + preview.count('unknown')} blocked",
            type="positive",
            position="bottom-right",
        )
        logger.info(
            "Config '%s' analysed: ready=%s same=%s invalid=%s missing=%s unknown=%s",
            filename,
            preview.count("ready"),
            preview.count("same"),
            preview.count("invalid"),
            preview.count("missing"),
            preview.count("unknown"),
        )

    ready_columns = [
        {"name": "path", "label": "Setting", "field": "path", "sortable": True, "align": "left"},
        {"name": "current", "label": "Current", "field": "current", "sortable": False, "align": "left"},
        {"name": "imported", "label": "Imported", "field": "imported", "sortable": False, "align": "left"},
        {"name": "reason", "label": "Note", "field": "reason", "sortable": False, "align": "left"},
    ]
    issue_columns = [
        {"name": "status", "label": "Status", "field": "status", "sortable": True, "align": "left"},
        {"name": "path", "label": "Setting", "field": "path", "sortable": True, "align": "left"},
        {"name": "current", "label": "Current", "field": "current", "sortable": False, "align": "left"},
        {"name": "imported", "label": "Imported", "field": "imported", "sortable": False, "align": "left"},
        {"name": "reason", "label": "Reason", "field": "reason", "sortable": False, "align": "left"},
    ]

    with ui.column().classes("w-full gap-3"):
        ui.label("Configuration").classes("text-subtitle1 font-semibold").props("id=config")
        ui.label(
            "Download the active config.yaml or upload another config.yaml to analyse which settings can be imported safely."
        ).classes("text-body2")

        with ui.row().classes("w-full gap-2 items-center flex-wrap"):
            ui.button("Download config.yaml", icon="download", on_click=download_config_yaml).props("color=primary")
            ui.upload(
                label="Upload config.yaml",
                auto_upload=True,
                max_file_size=1024 * 1024,
                on_upload=_handle_upload,
                on_rejected=lambda: ui.notify("Upload rejected", type="warning", position="bottom-right"),
            ).props("accept=.yaml,.yml color=secondary flat bordered")

        summary_label = ui.label("No imported config analysed yet.").classes("text-body2 text-grey-8")
        error_label = ui.label("").classes("text-body2 text-negative")

        with ui.row().classes("gap-2 flex-wrap"):
            apply_selected_btn = ui.button(
                "Apply selected settings",
                icon="done_all",
                on_click=lambda: _apply_selected(
                    [row.get("path") for row in (ready_table.selected or []) if row.get("path")] if ready_table is not None else []
                ),
            ).props("color=positive")
            apply_all_btn = ui.button(
                "Apply all valid settings",
                icon="publish",
                on_click=lambda: _apply_selected(None),
            ).props("color=positive outline")
            apply_selected_btn.disable()
            apply_all_btn.disable()

        with ui.expansion("Transferable Settings", value=True, icon="task_alt").classes("w-full"):
            ui.label("These values are valid and can be imported into the current instance.").classes("text-body2")
            ready_table = ui.table(
                columns=ready_columns,
                rows=[],
                row_key="path",
                selection="multiple",
            ).classes("w-full")
            ready_table.on("selection", lambda _: _update_action_state())

        with ui.expansion("Already Matching", value=False, icon="done").classes("w-full"):
            ui.label("These imported values are already equal to the currently active config.").classes("text-body2")
            same_table = ui.table(columns=ready_columns, rows=[], row_key="path").classes("w-full")

        with ui.expansion("Blocked / Missing / Unsupported", value=True, icon="report_problem").classes("w-full"):
            ui.label(
                "These settings cannot be imported because they are missing, invalid, or not supported by this version."
            ).classes("text-body2")
            issue_table = ui.table(columns=issue_columns, rows=[], row_key="path").classes("w-full")

    _refresh_preview()
