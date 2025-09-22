"""GUI package init with lazy exports to prevent circular imports.

We avoid importing submodules at package import time. Instead we provide
attributes on demand via __getattr__.
"""

import importlib

_DEFAULT_ELEMENTS_EXPORTS = {
    "create_emailcard",
    "create_uvc_content",
    "create_motiondetection_card",
    "create_measurement_card",
    "create_motion_status_element",
    "create_camfeed_content",
}

_GUI_EXPORTS = {"create_gui", "init_camera"}

def __getattr__(name: str):
    # Lazy proxies to break circular imports
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