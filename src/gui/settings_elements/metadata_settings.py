from __future__ import annotations

from nicegui import ui

from src.config import get_global_config, save_global_config, get_logger
from src.gui.constants import StorageKeys
from src.gui.layout import compute_gui_title
from src.gui.settings_elements.ui_helpers import create_action_button, create_section_heading
from src.gui.storage import set_ui_pref

logger = get_logger('gui.metadata')


def create_metadata_settings() -> None:
    """Render controls to edit Metadata (cvd_id, cvd_name) only.

    Changes are persisted to config and immediately reflected in the UI header/footer
    and email templates which use {cvd_id} and {cvd_name} placeholders.
    """
    cfg = get_global_config()
    if not cfg:
        ui.label('Configuration not available').classes('text-red')
        return

    md = cfg.metadata

    with ui.column().classes('gap-3'):
        create_section_heading(
            'Metadata',
            icon='badge',
            caption='Identify this tracker instance in emails and the UI header.',
            anchor_id='metadata',
            title_classes='text-subtitle1 font-semibold',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )

        with ui.grid(columns=2).classes('gap-3 w-full'):
            cvd_id_inp = ui.input('CVD ID', value=str(getattr(md, 'cvd_id', 0))).props('type=number').tooltip('Numeric identifier for this tracker')
            cvd_name_inp = ui.input('CVD Name', value=str(getattr(md, 'cvd_name', ''))).tooltip('Short name for this tracker')

        with ui.row().classes('items-center gap-2'):
            ui.label('Preview title:').classes('text-body2 text-grey-7')
            preview_lbl = ui.label().classes('text-body1 text-primary')

        def _compute_template_title(cvd_id_val: str, cvd_name_val: str) -> str:
            return compute_gui_title(
                cfg,
                cvd_id=int(cvd_id_val) if str(cvd_id_val).strip() else '',
                cvd_name=str(cvd_name_val or '').strip(),
            )

        def _is_dirty() -> bool:
            try:
                return (
                    int(cvd_id_inp.value or 0) != int(getattr(md, 'cvd_id', 0))
                    or str(cvd_name_inp.value or '').strip() != str(getattr(md, 'cvd_name', '')).strip()
                )
            except Exception:
                return True

        apply_btn: ui.button | None = None

        def _update_ui_from_inputs() -> None:
            nonlocal apply_btn
            new_preview = _compute_template_title(str(cvd_id_inp.value or ''), str(cvd_name_inp.value or ''))
            try:
                preview_lbl.text = new_preview
                preview_lbl.update()
            except Exception:
                pass
            try:
                if apply_btn is not None:
                    if _is_dirty():
                        apply_btn.enable()
                    else:
                        apply_btn.disable()
            except Exception:
                pass

        with ui.row().classes('gap-2'):
            def _apply() -> None:
                try:
                    try:
                        cfg.metadata.cvd_id = int(cvd_id_inp.value or 0)
                    except Exception:
                        ui.notify('CVD ID must be a number', type='warning', position='bottom-right')
                        return
                    cfg.metadata.cvd_name = str(cvd_name_inp.value or '').strip()

                    if save_global_config():
                        from src.gui.gui_ import sync_runtime_gui_title

                        new_title = sync_runtime_gui_title(title=compute_gui_title(cfg), broadcast=True)
                        set_ui_pref(StorageKeys.GUI_TITLE, new_title)
                        ui.notify('Metadata saved.', type='positive', position='bottom-right')
                        logger.info('Metadata updated: id=%s, name=%s', cfg.metadata.cvd_id, cfg.metadata.cvd_name)
                        _update_ui_from_inputs()
                    else:
                        ui.notify('Failed to save metadata', type='negative', position='bottom-right')
                except Exception as exc:  # noqa: BLE001
                    logger.error('Failed to save metadata: %s', exc, exc_info=True)
                    ui.notify(f'Error saving metadata: {exc}', type='negative', position='bottom-right')

            apply_btn = create_action_button('apply', on_click=_apply)

            def _reset() -> None:
                cvd_id_inp.value = str(getattr(md, 'cvd_id', 0))
                cvd_name_inp.value = str(getattr(md, 'cvd_name', ''))
                cvd_id_inp.update()
                cvd_name_inp.update()
                _update_ui_from_inputs()

            create_action_button('reset', on_click=_reset)

        try:
            cvd_id_inp.on_value_change(lambda e: _update_ui_from_inputs())
            cvd_name_inp.on_value_change(lambda e: _update_ui_from_inputs())
        except Exception:
            try:
                cvd_id_inp.on('input', lambda e=None: _update_ui_from_inputs())
                cvd_name_inp.on('input', lambda e=None: _update_ui_from_inputs())
            except Exception:
                pass

        _update_ui_from_inputs()
