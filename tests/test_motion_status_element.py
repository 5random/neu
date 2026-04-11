from types import SimpleNamespace

from src.gui.default_elements import motion_status_element


class _DummyElement:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, *_args, **_kwargs):
        return self

    def style(self, *_args, **_kwargs):
        return self

    def props(self, *_args, **_kwargs):
        return self

    def tooltip(self, *_args, **_kwargs):
        return self

    def update(self):
        return self

    def disable(self):
        return self


class _FakeUI:
    def __init__(self) -> None:
        self.context = SimpleNamespace(client=None)
        self.navigate = SimpleNamespace(to=lambda _path: None)

    def card(self):
        return _DummyElement()

    def row(self):
        return _DummyElement()

    def column(self):
        return _DummyElement()

    def expansion(self, *_args, **_kwargs):
        return _DummyElement()

    def button(self, *_args, **_kwargs):
        return _DummyElement()

    def icon(self, *_args, **_kwargs):
        return _DummyElement()

    def label(self, *_args, **_kwargs):
        return _DummyElement()


class _FakeClient:
    def __init__(self) -> None:
        self.disconnect_handlers: list[object] = []

    def on_disconnect(self, handler) -> None:
        self.disconnect_handlers.append(handler)


def test_motion_status_skips_listener_registration_without_client_context(monkeypatch) -> None:
    fake_ui = _FakeUI()
    camera_listener_calls: list[object] = []
    camera = SimpleNamespace(
        get_last_motion_result=lambda: None,
        enable_motion_detection=lambda callback: camera_listener_calls.append(callback),
        disable_motion_detection=lambda _callback: None,
    )

    monkeypatch.setattr(motion_status_element, 'ui', fake_ui)
    monkeypatch.setattr(motion_status_element, '_ensure_motion_update_route_registered', lambda: None)
    monkeypatch.setattr(motion_status_element, 'create_heading_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(motion_status_element, 'create_motion_sensitivity_controls', lambda *args, **kwargs: None)
    monkeypatch.setattr(motion_status_element, 'resolve_combined_motion_state', lambda _camera: (False, None))

    motion_status_element.create_motion_status_element(camera)

    assert camera_listener_calls == []


def test_motion_status_reuses_single_disconnect_handler_per_client(monkeypatch) -> None:
    fake_ui = _FakeUI()
    fake_client = _FakeClient()
    fake_ui.context.client = fake_client
    camera_listener_calls: list[tuple[str, object]] = []
    camera = SimpleNamespace(
        get_last_motion_result=lambda: None,
        enable_motion_detection=lambda callback: camera_listener_calls.append(("enable", callback)),
        disable_motion_detection=lambda callback: camera_listener_calls.append(("disable", callback)),
    )

    monkeypatch.setattr(motion_status_element, 'ui', fake_ui)
    monkeypatch.setattr(motion_status_element, '_ensure_motion_update_route_registered', lambda: None)
    monkeypatch.setattr(motion_status_element, 'create_heading_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(motion_status_element, 'create_motion_sensitivity_controls', lambda *args, **kwargs: None)
    monkeypatch.setattr(motion_status_element, 'resolve_combined_motion_state', lambda _camera: (False, None))

    motion_status_element.create_motion_status_element(camera)
    motion_status_element.create_motion_status_element(camera)

    assert len(fake_client.disconnect_handlers) == 1
    assert getattr(fake_client, 'cvd_motion_status_disconnect_handler') is fake_client.disconnect_handlers[0]
    assert callable(getattr(fake_client, 'cvd_motion_status_listener_cleanup'))
    assert [kind for kind, _listener in camera_listener_calls] == ["enable", "disable", "enable"]

    fake_client.disconnect_handlers[0]()

    assert [kind for kind, _listener in camera_listener_calls] == ["enable", "disable", "enable", "disable"]
    assert not hasattr(fake_client, 'cvd_motion_status_listener_cleanup')
    assert not hasattr(fake_client, 'cvd_motion_status_disconnect_handler')
