"""GUI package init with lazy exports to prevent circular imports."""
import importlib

_DEFAULT_ELEMENTS_EXPORTS = {
    "MotionStatusElement",
    "CameraStatusElement", 
    "SystemStatusElement",
    "HistoryCard",
    "StatsCard"
}

_GUI_EXPORTS = {
    "create_header",
    "create_navigation",
    "create_content_area"
}

from typing import Any

def __getattr__(name: str) -> Any:
    if name in _DEFAULT_ELEMENTS_EXPORTS:
        mod = importlib.import_module(".default_elements", __name__)
        return getattr(mod, name)
    if name in _GUI_EXPORTS:
        mod = importlib.import_module(".gui_", __name__)
        return getattr(mod, name)
    # Allow direct access to util submodule via attribute if someone does `from src.gui import util`
    if name == "util":
        return importlib.import_module(".util", __name__)
    raise AttributeError(f"module {__name__} has no attribute {name!r}")