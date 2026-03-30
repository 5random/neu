from types import SimpleNamespace
from unittest.mock import Mock

import src.gui as gui


def test_gui_lazy_exports_resolve_factory_aliases(monkeypatch) -> None:
    default_elements = SimpleNamespace(
        create_motion_status_element=Mock(return_value="motion"),
        create_camfeed_content=Mock(return_value="camfeed"),
        create_history_card=Mock(return_value="history"),
        create_stats_card=Mock(return_value="stats"),
    )
    gui_module = SimpleNamespace(create_gui=Mock(return_value="gui"))

    def fake_import_module(name: str, package: str | None = None) -> object:
        if (name, package) == (".default_elements", "src.gui"):
            return default_elements
        if (name, package) == (".gui_", "src.gui"):
            return gui_module
        if (name, package) == (".util", "src.gui"):
            return SimpleNamespace()
        raise AssertionError(f"unexpected import: {(name, package)!r}")

    monkeypatch.setattr(gui.importlib, "import_module", fake_import_module)

    assert gui.HistoryCard() == "history"
    assert gui.create_history_card() == "history"
    assert gui.CamfeedContent() == "camfeed"
    assert gui.create_gui() == "gui"


def test_gui_lazy_exports_raise_clear_error_for_missing_factory(monkeypatch) -> None:
    default_elements = SimpleNamespace()

    def fake_import_module(name: str, package: str | None = None) -> object:
        if (name, package) == (".default_elements", "src.gui"):
            return default_elements
        if (name, package) == (".util", "src.gui"):
            return SimpleNamespace()
        raise AssertionError(f"unexpected import: {(name, package)!r}")

    monkeypatch.setattr(gui.importlib, "import_module", fake_import_module)

    try:
        gui.__getattr__("HistoryCard")
    except AttributeError as exc:
        assert "HistoryCard" in str(exc)
        assert "create_history_card" in str(exc)
    else:
        raise AssertionError("expected AttributeError for unresolved lazy export")
