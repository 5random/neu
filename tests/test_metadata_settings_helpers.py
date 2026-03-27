from src.config import _create_default_config
from src.gui.constants import StorageKeys
from src.gui.layout import compute_gui_title
from src.gui.settings_elements import metadata_settings


def test_sync_saved_metadata_title_updates_runtime_title_and_storage_pref() -> None:
    cfg = _create_default_config()
    cfg.metadata.cvd_id = 42
    cfg.metadata.cvd_name = "Tracker"

    sync_calls: list[dict[str, object]] = []
    pref_calls: list[tuple[str, str]] = []

    def _sync_title(**kwargs):
        sync_calls.append(dict(kwargs))
        return "Tracker UI"

    def _set_pref(key: str, value: str) -> None:
        pref_calls.append((key, value))

    new_title = metadata_settings._sync_saved_metadata_title(
        cfg,
        sync_title=_sync_title,
        set_pref=_set_pref,
    )

    assert new_title == "Tracker UI"
    assert sync_calls == [{"title": compute_gui_title(cfg), "broadcast": True}]
    assert pref_calls == [(StorageKeys.GUI_TITLE, "Tracker UI")]
