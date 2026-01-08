from __future__ import annotations

from nicegui import ui, app

from src.config import get_global_config, save_global_config, get_logger

logger = get_logger('gui.metadata')


def create_metadata_settings() -> None:
	"""Render controls to edit Metadata (cvd_id, cvd_name) only.

	Changes are persisted to config and immediately reflected in the UI header/footer
	and email templates which use {cvd_id} and {cvd_name} placeholders.
	"""
	cfg = get_global_config()
	if not cfg:
		ui.label('⚠️ Configuration not available').classes('text-red')
		return

	md = cfg.metadata

	with ui.column().classes('gap-3'):
		ui.label('Metadata').classes('text-subtitle1 font-semibold').props('id=metadata')
		ui.label('Identify this tracker instance in emails and the UI header.').classes('text-body2')

		# Inputs
		with ui.grid(columns=2).classes('gap-3 w-full'):
			cvd_id_inp = ui.input('CVD ID', value=str(getattr(md, 'cvd_id', 0))).props('type=number').tooltip('Numeric identifier for this tracker')
			cvd_name_inp = ui.input('CVD Name', value=str(getattr(md, 'cvd_name', ''))).tooltip('Short name for this tracker')

		# Live preview row (manual update for reliability)
		with ui.row().classes('items-center gap-2'):
			ui.label('Preview title:').classes('text-body2 text-grey-7')
			preview_lbl = ui.label().classes('text-body1 text-primary')

		# Helpers
		def _compute_template_title(cvd_id_val: str, cvd_name_val: str) -> str:
			try:
				tpl = getattr(getattr(cfg, 'gui', None), 'title', 'CVD-TRACKER')
				return str(tpl).format(
					cvd_id=int(cvd_id_val) if str(cvd_id_val).strip() else '',
					cvd_name=str(cvd_name_val or '').strip(),
				)
			except Exception:
				try:
					return str(getattr(getattr(cfg, 'gui', None), 'title', 'CVD-TRACKER'))
				except Exception:
					return 'CVD-TRACKER'

		def _is_dirty() -> bool:
			try:
				return (
					int(cvd_id_inp.value or 0) != int(getattr(md, 'cvd_id', 0)) or
					str(cvd_name_inp.value or '').strip() != str(getattr(md, 'cvd_name', '')).strip()
				)
			except Exception:
				return True

		# Placeholder for button to allow toggling after it's created
		apply_btn: ui.button | None = None

		def _update_ui_from_inputs() -> None:
			nonlocal apply_btn
			# Update preview text directly
			new_preview = _compute_template_title(str(cvd_id_inp.value or ''), str(cvd_name_inp.value or ''))
			try:
				preview_lbl.text = new_preview
				preview_lbl.update()
			except Exception:
				pass
			# Toggle apply button
			try:
				if apply_btn is not None:
					if _is_dirty():
						apply_btn.enable()
					else:
						apply_btn.disable()
			except Exception:
				pass

		# Buttons row
		with ui.row().classes('gap-2'):
			def _apply() -> None:
				try:
					# Validate and persist to global config
					try:
						cfg.metadata.cvd_id = int(cvd_id_inp.value or 0)
					except Exception:
						ui.notify('CVD ID must be a number', type='warning', position='bottom-right')
						return
					cfg.metadata.cvd_name = str(cvd_name_inp.value or '').strip()

					if save_global_config():
						ui.notify('Metadata saved. Please restart the application to apply.', type='positive', position='bottom-right')
						logger.info('Metadata updated: id=%s, name=%s', cfg.metadata.cvd_id, cfg.metadata.cvd_name)
						_update_ui_from_inputs()
					else:
						ui.notify('Failed to save metadata', type='negative', position='bottom-right')
				except Exception as exc:  # noqa: BLE001
					logger.error('Failed to save metadata: %s', exc, exc_info=True)
					ui.notify(f'Error saving metadata: {exc}', type='negative', position='bottom-right')

			apply_btn = ui.button('Apply', on_click=_apply).props('color=primary')
			def _reset() -> None:
				cvd_id_inp.value = str(getattr(md, 'cvd_id', 0))
				cvd_name_inp.value = str(getattr(md, 'cvd_name', ''))
				cvd_id_inp.update()
				cvd_name_inp.update()
				_update_ui_from_inputs()

			ui.button('Reset', on_click=_reset)

		# Wire robust value change events
		try:
			cvd_id_inp.on_value_change(lambda e: _update_ui_from_inputs())
			cvd_name_inp.on_value_change(lambda e: _update_ui_from_inputs())
		except Exception:
			# Fallback to generic 'input' events
			try:
				cvd_id_inp.on('input', lambda e=None: _update_ui_from_inputs())
				cvd_name_inp.on('input', lambda e=None: _update_ui_from_inputs())
			except Exception:
				pass

	# Initialize preview text and button state now that everything exists
		_update_ui_from_inputs()

