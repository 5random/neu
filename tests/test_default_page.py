import inspect
from types import SimpleNamespace

from src.gui import default_page
from src.gui.default_elements import camfeed as default_camfeed
from src.gui import instances
from src.gui import settings_page


def test_dashboard_shows_active_group_selector() -> None:
    assert default_page.SHOW_DASHBOARD_ACTIVE_GROUP_SELECTOR is True


def test_dashboard_shows_alert_history() -> None:
    assert default_page.SHOW_DASHBOARD_ALERT_HISTORY is True


def test_dashboard_shows_alert_stats() -> None:
    assert default_page.SHOW_DASHBOARD_ALERT_STATS is True


def test_collect_startup_warnings_reflects_degraded_report() -> None:
    instances.set_startup_report(
        instances.InitializationReport(
            config_ok=True,
            camera_error="camera unavailable",
            measurement_ok=True,
        )
    )
    try:
        assert default_page._collect_startup_warnings() == ["Camera: camera unavailable"]
        assert settings_page._collect_startup_warnings() == ["Camera: camera unavailable"]
        assert instances.get_startup_warnings() == ["Camera: camera unavailable"]
    finally:
        instances.set_startup_report(None)


def test_collect_startup_warnings_uses_fallback_message_for_degraded_report_without_errors() -> None:
    instances.set_startup_report(
        instances.InitializationReport(
            config_ok=True,
            measurement_ok=True,
        )
    )
    try:
        expected = ["Startup completed in degraded mode"]
        assert default_page._collect_startup_warnings() == expected
        assert settings_page._collect_startup_warnings() == expected
        assert instances.get_startup_warnings() == expected
    finally:
        instances.set_startup_report(None)


def test_collect_startup_warnings_includes_non_fatal_config_warnings() -> None:
    instances.set_startup_report(
        instances.InitializationReport(
            config_ok=True,
            camera_ok=True,
            email_ok=True,
            measurement_ok=True,
            config_warnings=["Config file config/config.yaml is empty; using default config."],
        )
    )
    try:
        expected = ["Configuration: Config file config/config.yaml is empty; using default config."]
        assert default_page._collect_startup_warnings() == expected
        assert settings_page._collect_startup_warnings() == expected
        assert instances.get_startup_warnings() == expected
    finally:
        instances.set_startup_report(None)


def test_settings_page_logs_section_render_failures_instead_of_swallowing_them() -> None:
    source = inspect.getsource(settings_page.settings_page)

    assert "logger.exception(\"Failed to render section '%s'\", section_id)" in source


def test_dashboard_passes_camera_into_camfeed() -> None:
    source = inspect.getsource(default_page.index_page)

    assert "create_camfeed_content(camera=camera)" in source


class _DummyElement:
    def __init__(self, owner: "_FakeUI") -> None:
        self.owner = owner
        self._source = None
        self.style_calls: list[str] = []

    def __enter__(self) -> "_DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, *_args, **_kwargs) -> "_DummyElement":
        return self

    def style(self, *args, **_kwargs) -> "_DummyElement":
        if args:
            self.style_calls.append(str(args[0]))
        return self

    def props(self, *_args, **_kwargs) -> "_DummyElement":
        return self

    def tooltip(self, *_args, **_kwargs) -> "_DummyElement":
        return self

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value) -> None:
        self._source = value

    def set_source(self, value: str) -> None:
        self._source = value


class _FailingInteractiveImage(_DummyElement):
    def __init__(
        self,
        owner: "_FakeUI",
        *,
        fail_set_source: bool = False,
        fail_source_assignment: bool = False,
    ) -> None:
        super().__init__(owner)
        self.fail_set_source = fail_set_source
        self.fail_source_assignment = fail_source_assignment

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value) -> None:
        if self.fail_source_assignment:
            raise RuntimeError("direct source assignment failed")
        self._source = value

    def set_source(self, value: str) -> None:
        if self.fail_set_source:
            raise RuntimeError("set_source failed")
        self._source = value


class _FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[object, ...]] = []
        self.debug_calls: list[tuple[object, ...]] = []
        self.warning_calls: list[tuple[object, ...]] = []

    def info(self, message: str, *args) -> None:
        self.info_calls.append((message, *args))

    def debug(self, message: str, *args) -> None:
        self.debug_calls.append((message, *args))

    def warning(self, message: str, *args) -> None:
        self.warning_calls.append((message, *args))


class _FakeUI:
    def __init__(self, interactive_image_factory=None) -> None:
        self.labels: list[str] = []
        self.icons: list[str] = []
        self.body_html_calls: list[str] = []
        self.buttons: list[tuple[tuple, dict]] = []
        self.navigate_calls: list[str] = []
        self.interactive_images_created = 0
        self.last_interactive_image = None
        self._interactive_image_factory = interactive_image_factory or (lambda owner: _DummyElement(owner))
        self.navigate = SimpleNamespace(to=lambda path: self.navigate_calls.append(path))

    def card(self) -> _DummyElement:
        return _DummyElement(self)

    def row(self) -> _DummyElement:
        return _DummyElement(self)

    def column(self) -> _DummyElement:
        return _DummyElement(self)

    def button(self, *args, **kwargs) -> _DummyElement:
        self.buttons.append((args, kwargs))
        return _DummyElement(self)

    def label(self, text: str) -> _DummyElement:
        self.labels.append(text)
        return _DummyElement(self)

    def icon(self, name: str) -> _DummyElement:
        self.icons.append(name)
        return _DummyElement(self)

    def interactive_image(self, *args, **kwargs) -> _DummyElement:
        self.interactive_images_created += 1
        self.last_interactive_image = self._interactive_image_factory(self)
        if args:
            self.last_interactive_image.source = args[0]
        return self.last_interactive_image

    def add_body_html(self, html: str) -> None:
        self.body_html_calls.append(html)


def test_dashboard_camfeed_renders_placeholder_without_camera(monkeypatch) -> None:
    fake_ui = _FakeUI()

    monkeypatch.setattr(default_camfeed, "ui", fake_ui)
    monkeypatch.setattr(default_camfeed, "create_heading_row", lambda *args, **kwargs: None)

    default_camfeed.create_camfeed_content(camera_available=False)

    assert "Camera not available" in fake_ui.labels
    assert fake_ui.body_html_calls == []
    assert fake_ui.interactive_images_created == 0


def test_dashboard_camfeed_uses_stream_source_for_interactive_image(monkeypatch) -> None:
    fake_ui = _FakeUI()
    fake_logger = _FakeLogger()

    monkeypatch.setattr(default_camfeed, "ui", fake_ui)
    monkeypatch.setattr(default_camfeed, "logger", fake_logger)
    monkeypatch.setattr(default_camfeed, "create_heading_row", lambda *args, **kwargs: None)

    default_camfeed.create_camfeed_content(camera_available=True)

    assert fake_ui.interactive_images_created == 1
    assert "Connecting camera..." in fake_ui.labels
    assert fake_ui.last_interactive_image.source == "/video_feed"
    assert any("aspect-ratio:1280/720" in style for style in fake_ui.last_interactive_image.style_calls)
    assert any("min-height:240px" in style for style in fake_ui.last_interactive_image.style_calls)
    assert fake_ui.body_html_calls != []
    assert fake_logger.debug_calls == []
    assert fake_logger.warning_calls == []


def test_dashboard_camfeed_refresh_script_reuses_single_global_state() -> None:
    script = default_camfeed._build_camfeed_refresh_script()

    assert "window.__cvdDefaultCamState" in script
    assert "/video_feed?ts=" not in script
    assert "var url = '/video_feed';" in script
    assert "if (!force && currentSrc === url)" in script
    assert script.count("scheduleReconnect();") >= 2
    assert "start(true);" in script
    assert "document.visibilityState !== 'visible'" in script
    assert "addEventListener('load'" in script
    assert "addEventListener('error'" in script
    assert "removeEventListener('visibilitychange'" in script
    assert "removeEventListener('pagehide'" in script
    assert "removeEventListener('beforeunload'" in script
