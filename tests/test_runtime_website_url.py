from types import SimpleNamespace

from src.config import _create_default_config
from src.gui import layout
from src.notify import EMailSystem


def _dummy_app_storage(initial: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(storage=SimpleNamespace(general=dict(initial or {})))


def test_sync_runtime_website_url_persists_discovered_base_url(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = "http://old.example/"
    dummy_app = _dummy_app_storage()
    dummy_client = SimpleNamespace(
        request=SimpleNamespace(
            base_url="http://example.com:8080/",
            url="http://example.com:8080/settings?section=email",
        )
    )

    monkeypatch.setattr(layout, "app", dummy_app)
    monkeypatch.setattr(layout, "get_global_config", lambda: cfg)
    monkeypatch.setattr(layout, "save_global_config", lambda: True)

    resolved = layout.sync_runtime_website_url(client=dummy_client, persist=True)

    assert resolved == "http://example.com:8080/"
    assert cfg.email.website_url == "http://example.com:8080/"
    assert dummy_app.storage.general["cvd.runtime_website_url"] == "http://example.com:8080/"


def test_sync_runtime_website_url_preserves_root_path_from_base_url(monkeypatch) -> None:
    cfg = _create_default_config()
    dummy_app = _dummy_app_storage()
    dummy_client = SimpleNamespace(
        request=SimpleNamespace(
            base_url="https://example.com/cvd/",
            url="https://example.com/cvd/settings?section=config",
        )
    )

    monkeypatch.setattr(layout, "app", dummy_app)
    monkeypatch.setattr(layout, "get_global_config", lambda: cfg)
    monkeypatch.setattr(layout, "save_global_config", lambda: True)

    resolved = layout.sync_runtime_website_url(client=dummy_client, persist=True)

    assert resolved == "https://example.com/cvd/"
    assert cfg.email.website_url == "https://example.com/cvd/"


def test_sync_runtime_website_url_keeps_configured_url_in_runtime_mode(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = "https://configured.example/"
    cfg.email.website_url_source = "runtime"
    dummy_app = _dummy_app_storage()
    dummy_client = SimpleNamespace(
        request=SimpleNamespace(
            base_url="https://proxy.example/",
            url="https://proxy.example/settings?section=config",
        )
    )
    save_calls: list[str] = []

    monkeypatch.setattr(layout, "app", dummy_app)
    monkeypatch.setattr(layout, "get_global_config", lambda: cfg)
    monkeypatch.setattr(layout, "save_global_config", lambda: save_calls.append("saved") or True)

    resolved = layout.sync_runtime_website_url(client=dummy_client, persist=True)

    assert resolved == "https://proxy.example/"
    assert cfg.email.website_url == "https://configured.example/"
    assert dummy_app.storage.general["cvd.runtime_website_url"] == "https://proxy.example/"
    assert save_calls == []


def test_sync_runtime_website_url_accepts_mixed_case_runtime_persist_constant(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = "http://old.example/"
    cfg.email.website_url_source = "runtime_persist"
    cfg.email.WEBSITE_URL_SOURCE_RUNTIME_PERSIST = "Runtime_Persist"
    dummy_app = _dummy_app_storage()
    dummy_client = SimpleNamespace(
        request=SimpleNamespace(
            base_url="https://example.com/cvd/",
            url="https://example.com/cvd/settings?section=config",
        )
    )

    monkeypatch.setattr(layout, "app", dummy_app)
    monkeypatch.setattr(layout, "get_global_config", lambda: cfg)
    monkeypatch.setattr(layout, "save_global_config", lambda: True)

    resolved = layout.sync_runtime_website_url(client=dummy_client, persist=True)

    assert resolved == "https://example.com/cvd/"
    assert cfg.email.website_url == "https://example.com/cvd/"


def test_email_system_prefers_runtime_website_url_over_static_config(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = "http://fallback.example/"

    monkeypatch.setattr("src.notify.app", _dummy_app_storage({"cvd.runtime_website_url": "https://runtime.example/"}))

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        params = email_system._build_common_template_params()
    finally:
        email_system.close()

    assert params["website_url"] == "https://runtime.example/"


def test_email_system_prefers_configured_website_url_in_config_mode(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = "https://configured.example/"
    cfg.email.website_url_source = "config"
    cfg.email.WEBSITE_URL_SOURCE_CONFIG = "Config"

    monkeypatch.setattr("src.notify.app", _dummy_app_storage({"cvd.runtime_website_url": "https://runtime.example/"}))

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        params = email_system._build_common_template_params()
    finally:
        email_system.close()

    assert params["website_url"] == "https://configured.example/"


def test_email_system_allows_empty_website_url_in_runtime_mode(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url = ""
    cfg.email.website_url_source = "runtime"

    monkeypatch.setattr("src.notify.app", _dummy_app_storage({"cvd.runtime_website_url": "https://runtime.example/"}))

    assert cfg.email.validate() == []

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        params = email_system._build_common_template_params()
    finally:
        email_system.close()

    assert params["website_url"] == "https://runtime.example/"


def test_email_system_falls_back_to_runtime_url_when_config_mode_website_url_is_not_string(monkeypatch) -> None:
    cfg = _create_default_config()
    cfg.email.website_url_source = "config"

    monkeypatch.setattr("src.notify.app", _dummy_app_storage({"cvd.runtime_website_url": "https://runtime.example/"}))

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        cfg.email.website_url = False  # type: ignore[assignment]
        params = email_system._build_common_template_params()
    finally:
        email_system.close()

    assert params["website_url"] == "https://runtime.example/"
