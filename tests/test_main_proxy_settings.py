import logging
from types import SimpleNamespace

import main as app_main


def _build_cfg(**overrides) -> SimpleNamespace:
    gui = SimpleNamespace(
        host="localhost",
        port=8080,
        auto_open_browser=False,
        reverse_proxy_enabled=False,
        forwarded_allow_ips="127.0.0.1",
        root_path="",
        session_cookie_https_only=False,
    )
    for key, value in overrides.items():
        setattr(gui, key, value)
    return SimpleNamespace(gui=gui)


def _build_args() -> SimpleNamespace:
    return SimpleNamespace(host=None, port=None, open_browser=None)


def test_resolve_ui_run_settings_keeps_headless_override_when_proxy_disabled(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "is_headless_linux", lambda: True)

    settings = app_main.resolve_ui_run_settings(
        _build_cfg(host="localhost", reverse_proxy_enabled=False),
        _build_args(),
        logging.getLogger("test.main.proxy.disabled"),
    )

    assert settings["host"] == "0.0.0.0"
    assert settings["reverse_proxy_enabled"] is False
    assert settings["session_middleware_kwargs"] is None


def test_resolve_ui_run_settings_enables_proxy_options_from_config(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "is_headless_linux", lambda: True)

    settings = app_main.resolve_ui_run_settings(
        _build_cfg(
            host="127.0.0.1",
            reverse_proxy_enabled=True,
            forwarded_allow_ips="127.0.0.1,10.0.0.2",
            root_path="/cvd/",
            session_cookie_https_only=True,
        ),
        _build_args(),
        logging.getLogger("test.main.proxy.enabled"),
    )

    assert settings["host"] == "127.0.0.1"
    assert settings["reverse_proxy_enabled"] is True
    assert settings["forwarded_allow_ips"] == "127.0.0.1,10.0.0.2"
    assert settings["root_path"] == "/cvd"
    assert settings["session_middleware_kwargs"] == {"https_only": True}


def test_resolve_ui_run_settings_handles_string_false_and_invalid_proxy_fields(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "is_headless_linux", lambda: False)

    settings = app_main.resolve_ui_run_settings(
        _build_cfg(
            reverse_proxy_enabled="false",
            forwarded_allow_ips=123,
            root_path="cvd",
            session_cookie_https_only="false",
            auto_open_browser="false",
        ),
        _build_args(),
        logging.getLogger("test.main.proxy.invalid"),
    )

    assert settings["reverse_proxy_enabled"] is False
    assert settings["forwarded_allow_ips"] == "127.0.0.1"
    assert settings["root_path"] == ""
    assert settings["session_middleware_kwargs"] is None
    assert settings["show"] is False
