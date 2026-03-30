"""GUI package init with lazy exports to prevent circular imports."""
import importlib
from typing import Any, Callable


def _to_factory_name(name: str) -> str:
    """Convert a public CamelCase name to a `create_` snake_case factory name."""
    parts = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            parts.append("_")
        parts.append(ch.lower())
    return f"create_{''.join(parts)}"

_DEFAULT_ELEMENTS_EXPORTS = {
    "MotionStatusElement",
    "CamfeedContent",
    "HistoryCard",
    "StatsCard",
    "create_motion_status_element",
    "create_camfeed_content",
    "create_history_card",
    "create_stats_card",
}

_GUI_EXPORTS = {
    "create_gui",
    "refresh_connected_clients",
    "sync_runtime_gui_title",
    "build_post_restart_redirect_script",
    "install_post_restart_redirect",
}

__all__ = [
    "CamfeedContent",
    "HistoryCard",
    "MotionStatusElement",
    "StatsCard",
    "build_post_restart_redirect_script",
    "create_camfeed_content",
    "create_gui",
    "create_history_card",
    "create_motion_status_element",
    "create_stats_card",
    "install_post_restart_redirect",
    "refresh_connected_clients",
    "sync_runtime_gui_title",
    "util",
]


def _lazy_call(name: str) -> Callable[..., Any]:
    """Return a wrapper that resolves a lazy export only when called."""
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return __getattr__(name)(*args, **kwargs)

    wrapper.__name__ = name
    return wrapper


CamfeedContent = _lazy_call("CamfeedContent")
HistoryCard = _lazy_call("HistoryCard")
MotionStatusElement = _lazy_call("MotionStatusElement")
StatsCard = _lazy_call("StatsCard")
build_post_restart_redirect_script = _lazy_call("build_post_restart_redirect_script")
create_camfeed_content = _lazy_call("create_camfeed_content")
create_gui = _lazy_call("create_gui")
create_history_card = _lazy_call("create_history_card")
create_motion_status_element = _lazy_call("create_motion_status_element")
create_stats_card = _lazy_call("create_stats_card")
install_post_restart_redirect = _lazy_call("install_post_restart_redirect")
refresh_connected_clients = _lazy_call("refresh_connected_clients")
sync_runtime_gui_title = _lazy_call("sync_runtime_gui_title")
util = importlib.import_module(".util", __name__)


def __getattr__(name: str) -> Any:
    if name in _DEFAULT_ELEMENTS_EXPORTS:
        mod = importlib.import_module(".default_elements", __name__)
        try:
            return getattr(mod, name)
        except AttributeError:
            factory_name = _to_factory_name(name)
            try:
                return getattr(mod, factory_name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module {__name__} cannot resolve lazy export {name!r} "
                    f"from '.default_elements' (tried attributes {name!r} and {factory_name!r})"
                ) from exc
    if name in _GUI_EXPORTS:
        mod = importlib.import_module(".gui_", __name__)
        return getattr(mod, name)
    # Allow direct access to util submodule via attribute if someone does `from src.gui import util`
    if name == "util":
        return importlib.import_module(".util", __name__)
    raise AttributeError(f"module {__name__} has no attribute {name!r}")
