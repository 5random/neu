import inspect

from src.gui import layout


def test_build_header_uses_logo_button_for_home_navigation() -> None:
    source = inspect.getsource(layout.build_header)

    assert "ui.button(icon='img:/pics/logo_ipc_short.svg', on_click=_go_home)" in source
    assert "tooltip('Go to Home')" in source


def test_build_header_places_power_button_in_action_row() -> None:
    source = inspect.getsource(layout.build_header)

    assert "ui.button(icon='power_settings_new', on_click=show_power_menu_dialog)" in source
    assert "id=cvd-header-power" in source


def test_build_header_title_is_not_bound_to_gui_title_storage() -> None:
    source = inspect.getsource(layout.build_header)

    assert "title_label.bind_text_from(get_ui_storage(), StorageKeys.GUI_TITLE)" not in source
    assert "ui.label(current_title).props('id=cvd-header-title')" in source


def test_build_footer_keeps_gui_title_storage_binding() -> None:
    source = inspect.getsource(layout.build_footer)

    assert "footer_label.bind_text_from(get_ui_storage(), StorageKeys.GUI_TITLE)" in source
