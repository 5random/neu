from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

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
from src.gui.settings_elements.ui_helpers import create_action_button, create_section_heading

logger = get_logger("gui.config_settings")
NotifyKind = Literal["positive", "negative", "warning", "info", "ongoing"]


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
    """Render a guided config import flow with upload, review, and apply steps."""

    cfg = get_global_config()
    if cfg is None:
        ui.label("Configuration not available").classes("text-red")
        logger.error("Configuration not available - cannot create config settings")
        return

    state: dict[str, Any] = {
        "preview": None,
        "source_name": "",
        "source_text": "",
        "uploaded_temp_path": None,
    }

    stepper: Optional[Any] = None
    upload_widget: Optional[Any] = None
    review_step: Optional[Any] = None
    select_step: Optional[Any] = None
    upload_status_label: Optional[Any] = None
    upload_error_label: Optional[Any] = None
    review_summary_label: Optional[Any] = None
    apply_summary_label: Optional[Any] = None
    ready_table: Optional[Any] = None
    same_table: Optional[Any] = None
    issue_table: Optional[Any] = None
    upload_next_btn: Optional[Any] = None
    review_next_btn: Optional[Any] = None
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

    def _set_step(name: str) -> None:
        if stepper is None:
            return
        try:
            stepper.set_value(name)
        except Exception:
            stepper.value = name
            stepper.update()

    def _set_enabled(element: Optional[Any], enabled: bool) -> None:
        if element is None:
            return
        try:
            element.enable() if enabled else element.disable()
        except Exception:
            pass

    def _cleanup_uploaded_file() -> None:
        temp_path = state.get("uploaded_temp_path")
        if not temp_path:
            return
        try:
            Path(temp_path).unlink(missing_ok=True)
            logger.info("Removed temporary uploaded config file: %s", temp_path)
        except Exception as exc:
            logger.warning("Failed to remove temporary uploaded config file '%s': %s", temp_path, exc)
        finally:
            state["uploaded_temp_path"] = None

    def _resolve_uploaded_temp_path(upload_file: Any) -> Optional[str]:
        public_path = getattr(upload_file, "path", None)
        if isinstance(public_path, (str, Path)):
            return str(public_path)

        file_obj = getattr(upload_file, "file", None)
        file_name = getattr(file_obj, "name", None)
        if isinstance(file_name, (str, Path)):
            candidate = Path(file_name)
            if candidate.exists():
                return str(candidate)

        # Fallback for older / internal NiceGUI upload implementations.
        private_path = getattr(upload_file, "_path", None)
        if isinstance(private_path, (str, Path)):
            return str(private_path)

        return None

    def _reset_import_flow(*, notify_message: Optional[str] = None, notify_type: NotifyKind = "info") -> None:
        _cleanup_uploaded_file()
        state["preview"] = None
        state["source_name"] = ""
        state["source_text"] = ""
        if upload_widget is not None:
            try:
                upload_widget.reset()
            except Exception:
                pass
        _refresh_preview()
        _set_step("upload")
        if notify_message:
            ui.notify(notify_message, type=notify_type, position="bottom-right")

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

    def _partition_entries(preview: Optional[ConfigImportPreview]) -> tuple[list[Any], list[Any], list[Any]]:
        if preview is None:
            return [], [], []
        ready_entries = [entry for entry in preview.entries if entry.status == "ready"]
        same_entries = [entry for entry in preview.entries if entry.status == "same"]
        issue_entries = [
            entry
            for entry in preview.entries
            if entry.status in {"invalid", "missing", "unknown"}
        ]
        return ready_entries, same_entries, issue_entries

    def _update_action_state() -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        has_ready = preview is not None and not preview.errors and bool(preview.ready_updates)
        if apply_all_btn is not None:
            _set_enabled(apply_all_btn, has_ready)
        if apply_selected_btn is not None and ready_table is not None:
            selected_paths = [row.get("path") for row in (ready_table.selected or []) if row.get("path")]
            _set_enabled(apply_selected_btn, has_ready and bool(selected_paths))

    def _update_step_state() -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        has_preview = preview is not None and not preview.errors
        has_ready = bool(preview.ready_updates) if preview is not None and has_preview else False
        _set_enabled(review_step, has_preview)
        _set_enabled(select_step, has_ready)
        _set_enabled(upload_next_btn, has_preview)
        _set_enabled(review_next_btn, has_ready)
        _update_action_state()

    def _refresh_preview() -> None:
        preview: Optional[ConfigImportPreview] = state["preview"]
        ready_entries, same_entries, issue_entries = _partition_entries(preview)
        source_name = state["source_name"] or "uploaded config"

        if upload_status_label is not None:
            if preview is None:
                upload_status_label.text = "Upload a .yaml file."
            elif preview.errors:
                upload_status_label.text = f"Uploaded file: {source_name}"
            else:
                upload_status_label.text = (
                    f"Uploaded file: {source_name} | "
                    f"transferable: {preview.count('ready')} | "
                    f"blocked: {len(issue_entries)} | "
                    f"already matching: {preview.count('same')}"
                )
            upload_status_label.update()

        if upload_error_label is not None:
            upload_error_label.text = " | ".join(preview.errors) if preview and preview.errors else ""
            upload_error_label.update()

        if review_summary_label is not None:
            if preview is None:
                review_summary_label.text = "After upload, this step shows which values cannot be imported."
            else:
                review_summary_label.text = (
                    f"Source: {preview.source_name} | "
                    f"blocked: {len(issue_entries)} | "
                    f"already matching: {len(same_entries)} | "
                    f"transferable: {len(ready_entries)}"
                )
            review_summary_label.update()

        if apply_summary_label is not None:
            if preview is None:
                apply_summary_label.text = "Upload and analyse a config file first."
            elif preview.errors:
                apply_summary_label.text = "The uploaded file has parsing errors and cannot be imported."
            elif ready_entries:
                apply_summary_label.text = (
                    "Select the valid settings you want to import, or apply all transferable settings at once."
                )
            else:
                apply_summary_label.text = "There are no transferable settings in this file."
            apply_summary_label.update()

        if issue_table is not None:
            issue_table.rows = _rows_for_entries(issue_entries, include_status=True)
            issue_table.update()
        if same_table is not None:
            same_table.rows = _rows_for_entries(same_entries, include_status=False)
            same_table.update()
        if ready_table is not None:
            ready_table.rows = _rows_for_entries(ready_entries, include_status=False)
            ready_table.selected = []
            ready_table.update()

        _update_step_state()

        if preview is None or preview.errors:
            _set_step("upload")
        elif not preview.ready_updates and getattr(stepper, "value", None) == "select":
            _set_step("review")

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
        _reset_import_flow()

    async def _handle_upload(event: UploadEventArguments) -> None:
        filename = event.file.name or "uploaded_config.yaml"
        suffix = Path(filename).suffix.lower()
        if suffix not in {".yaml", ".yml"}:
            ui.notify("Only .yaml or .yml files are supported", type="warning", position="bottom-right")
            return
        _cleanup_uploaded_file()
        try:
            text = await event.file.text()
        except Exception as exc:
            logger.error("Failed to read uploaded config: %s", exc, exc_info=True)
            ui.notify(f"Could not read uploaded file: {exc}", type="negative", position="bottom-right")
            return

        state["source_name"] = filename
        state["source_text"] = text
        state["uploaded_temp_path"] = _resolve_uploaded_temp_path(event.file)
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
        _set_step("review")

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
        create_section_heading(
            "Configuration",
            icon="description",
            caption="Import another config.yaml: upload, review blocked values, then select what should be adopted.",
            anchor_id="config",
            title_classes="text-subtitle1 font-semibold",
            row_classes="items-center gap-2",
            icon_classes="text-primary text-xl shrink-0",
        )

        with ui.row().classes("w-full justify-end"):
            ui.button("Download current config.yaml", icon="download", on_click=download_config_yaml).props("color=primary")

        with ui.stepper(value="upload").props("vertical flat animated").classes("w-full") as stepper:
            with ui.step("upload", title="Upload config", icon="upload_file"):
                ui.label(
                    "Step 1: Upload the new .yaml file. The file is analysed immediately after upload."
                ).classes("text-body2")
                with ui.row().classes("w-full gap-2 items-center flex-wrap"):
                    upload_widget = ui.upload(
                        label="Upload new config.yaml",
                        auto_upload=True,
                        max_file_size=1024 * 1024,
                        on_upload=_handle_upload,
                        on_rejected=lambda: ui.notify("Upload rejected", type="warning", position="bottom-right"),
                    ).props("accept=.yaml,.yml color=secondary flat bordered")
                upload_status_label = ui.label("Upload a .yaml file to start.").classes("text-body2 text-grey-8")
                upload_error_label = ui.label("").classes("text-body2 text-negative")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    next_button = create_action_button(
                        "apply",
                        label="Review importability",
                        icon="arrow_forward",
                        on_click=lambda: _set_step("review"),
                    )
                    next_button.disable()
                    upload_next_btn = next_button

            with ui.step("review", title="Review blocked values", icon="rule") as review_step:
                ui.label(
                    "Step 2: Review which settings cannot be imported or are already identical to the current configuration."
                ).classes("text-body2")
                review_summary_label = ui.label(
                    "After upload, this step shows which values cannot be imported."
                ).classes("text-body2 text-grey-8")

                with ui.expansion("Blocked / Missing / Unsupported", value=True, icon="report_problem").classes("w-full"):
                    ui.label(
                        "These settings cannot be imported because they are invalid, missing in the uploaded file, or unsupported by this version."
                    ).classes("text-body2")
                    issue_table = ui.table(columns=issue_columns, rows=[], row_key="path").classes("w-full")

                with ui.expansion("Already Matching", value=False, icon="done").classes("w-full"):
                    ui.label(
                        "These values are already identical to the currently active configuration."
                    ).classes("text-body2")
                    same_table = ui.table(columns=ready_columns, rows=[], row_key="path").classes("w-full")

                with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                    ui.button("Back", icon="arrow_back", on_click=lambda: _set_step("upload")).props("flat no-caps")
                    next_button = create_action_button(
                        "apply",
                        label="Choose settings to import",
                        icon="arrow_forward",
                        on_click=lambda: _set_step("select"),
                    )
                    next_button.disable()
                    review_next_btn = next_button
                review_step.disable()

            with ui.step("select", title="Select and import", icon="task_alt") as select_step:
                ui.label(
                    "Step 3: Select the transferable settings you want to import, or apply all valid settings."
                ).classes("text-body2")
                apply_summary_label = ui.label(
                    "Upload and analyse a config file first."
                ).classes("text-body2 text-grey-8")
                ready_table = ui.table(
                    columns=ready_columns,
                    rows=[],
                    row_key="path",
                    selection="multiple",
                ).classes("w-full")
                ready_table.on("selection", lambda _: _update_action_state())

                with ui.row().classes("w-full items-center justify-between gap-2 flex-wrap mt-2"):
                    with ui.row().classes("gap-2 flex-wrap"):
                        ui.button("Back", icon="arrow_back", on_click=lambda: _set_step("review")).props("flat no-caps")
                        create_action_button(
                            "clear",
                            label="Dismiss import",
                            icon="close",
                            on_click=lambda: _reset_import_flow(
                                notify_message="Config import dismissed",
                                notify_type="warning",
                            ),
                        )
                    with ui.row().classes("gap-2 flex-wrap"):
                        selected_button = create_action_button(
                            "apply",
                            label="Import selected settings",
                            icon="done_all",
                            on_click=lambda: _apply_selected(
                                [row.get("path") for row in (ready_table.selected or []) if row.get("path")] if ready_table is not None else []
                            ),
                        )
                        all_button = create_action_button(
                            "apply",
                            label="Import all valid settings",
                            icon="publish",
                            on_click=lambda: _apply_selected(None),
                        )
                        selected_button.disable()
                        all_button.disable()
                        apply_selected_btn = selected_button
                        apply_all_btn = all_button
                select_step.disable()

    _refresh_preview()
