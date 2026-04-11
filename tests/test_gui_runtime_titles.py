import asyncio
from types import SimpleNamespace

from src.gui import gui_


class _DummyClient:
    def __init__(self) -> None:
        self.title = None
        self.js_calls: list[str] = []

    def run_javascript(self, code: str) -> None:
        self.js_calls.append(code)


def _make_recording_set_tab(calls: list[tuple[str | None, str | None, object | None]]):
    def _set_tab(title=None, icon_url=None, client=None):
        calls.append((title, icon_url, client))
        if title is not None and client is not None and hasattr(client, 'title'):
            client.title = title
        return True

    return _set_tab


def test_runtime_title_helpers_keep_browser_title_static_while_header_changes() -> None:
    pending_state = {
        'is_active': True,
        'session_id': 'session-1',
        'session_start_time': None,
        'recent_motion_detected': None,
    }
    motion_state = {
        'is_active': True,
        'session_id': 'session-1',
        'session_start_time': None,
        'recent_motion_detected': True,
    }
    no_motion_state = {
        'is_active': True,
        'session_id': 'session-1',
        'session_start_time': None,
        'recent_motion_detected': False,
    }

    assert gui_._build_browser_display_title('Tracker', pending_state) == 'Tracker'
    assert gui_._build_browser_display_title('Tracker', motion_state) == 'Tracker'
    assert gui_._build_browser_display_title('Tracker', no_motion_state) == 'Tracker'
    assert gui_._build_header_display_title('Tracker', pending_state) == 'Messung aktiv | Tracker'
    assert gui_._build_header_display_title('Tracker', motion_state) == 'Messung aktiv | Tracker'
    assert gui_._build_header_display_title('Tracker', no_motion_state) == 'Messung aktiv | Tracker'
    assert gui_._build_browser_display_title('Tracker', {'is_active': False}) == 'Tracker'


def test_sync_runtime_gui_title_keeps_browser_title_static_and_updates_favicon(monkeypatch) -> None:
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )
    dummy_client = _DummyClient()
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_check_circle_green', lambda: 'green.ico')
    monkeypatch.setattr(gui_, 'favicon_sensors_off_orange', lambda: 'orange.ico')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(gui_, 'set_tab', _make_recording_set_tab(set_tab_calls))

    gui_._set_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=False,
    )
    try:
        resolved_title = gui_.sync_runtime_gui_title(title='Tracker', client=dummy_client)
    finally:
        gui_._set_runtime_measurement_state(
            is_active=False,
            session_id=None,
            session_start_time=None,
            recent_motion_detected=None,
        )

    assert resolved_title == 'Tracker'
    assert dummy_app.config.title == 'Tracker'
    assert dummy_app.storage.general['cvd.runtime_title'] == 'Tracker'
    assert set_tab_calls == [('Tracker', 'orange.ico', dummy_client)]
    assert dummy_client.title == 'Tracker'
    assert any('cvd-header-title-icon' in code for code in dummy_client.js_calls)
    assert any('Messung aktiv | Tracker' in code for code in dummy_client.js_calls)


def test_runtime_title_sync_script_sets_header_icon_status_classes() -> None:
    motion_state = {
        'is_active': True,
        'session_id': 'session-1',
        'session_start_time': None,
        'recent_motion_detected': True,
    }
    inactive_state = {
        'is_active': False,
        'session_id': None,
        'session_start_time': None,
        'recent_motion_detected': None,
    }

    motion_script = gui_._build_runtime_title_sync_script('Tracker', motion_state)
    inactive_script = gui_._build_runtime_title_sync_script('Tracker', inactive_state)

    assert 'const baseTitle = "Tracker";' in motion_script
    assert 'const headerTitle = "Messung aktiv | Tracker";' in motion_script
    assert 'const headerIconName = "sensors";' in motion_script
    assert 'const headerIconVisible = true;' in motion_script
    assert 'const headerIconStatusClass = "cvd-measurement-motion-detected";' in motion_script
    assert 'headerIcon.textContent = headerIconName;' in motion_script
    assert "headerIcon.classList.remove(...statusClasses);" in motion_script
    assert 'const headerIconVisible = false;' in inactive_script


class _ControllerStub:
    def __init__(self) -> None:
        self.session_callbacks: list[object] = []
        self.motion_callbacks: list[object] = []
        self.status = {
            'is_active': False,
            'session_id': None,
            'session_start_time': None,
            'recent_motion_detected': None,
        }

    def register_session_state_callback(self, callback) -> None:
        self.session_callbacks.append(callback)

    def unregister_session_state_callback(self, callback) -> None:
        self.session_callbacks.remove(callback)

    def register_motion_callback(self, callback) -> None:
        self.motion_callbacks.append(callback)

    def unregister_motion_callback(self, callback) -> None:
        self.motion_callbacks.remove(callback)

    def get_session_status(self) -> dict[str, object]:
        return dict(self.status)


def test_register_client_runtime_title_sync_registers_session_and_motion_listeners(monkeypatch) -> None:
    controller = _ControllerStub()
    client = SimpleNamespace(title=None, js_calls=[])
    client.run_javascript = lambda code: client.js_calls.append(code)
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []
    scheduled_coroutines: list[tuple[object, str | None]] = []
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_check_circle_green', lambda: 'green.ico')
    monkeypatch.setattr(gui_, 'favicon_sensors_off_orange', lambda: 'orange.ico')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(
        gui_,
        'schedule_bg',
        lambda coroutine, name=None: scheduled_coroutines.append((coroutine, name)),
    )
    monkeypatch.setattr(gui_, 'set_tab', _make_recording_set_tab(set_tab_calls))

    gui_.register_client_runtime_title_sync(
        measurement_controller=controller,
        client=client,
    )

    assert len(controller.session_callbacks) == 1
    assert len(controller.motion_callbacks) == 1
    assert callable(getattr(client, 'cvd_runtime_title_listener_cleanup'))
    assert not hasattr(client, 'cvd_runtime_title_disconnect_handler')

    set_tab_calls.clear()
    client.title = None
    client.js_calls.clear()

    controller.session_callbacks[0](
        {
            'is_active': True,
            'session_id': 'session-1',
            'session_start_time': None,
        }
    )
    assert [name for _, name in scheduled_coroutines] == ['sync_runtime_gui_title']
    assert set_tab_calls == []
    assert client.title is None
    asyncio.run(scheduled_coroutines.pop(0)[0])

    controller.motion_callbacks[0](SimpleNamespace(motion_detected=True))
    assert [name for _, name in scheduled_coroutines] == ['sync_runtime_gui_title']
    assert set_tab_calls == [(None, 'neutral.ico', client)]
    asyncio.run(scheduled_coroutines.pop(0)[0])

    assert (None, 'neutral.ico', client) in set_tab_calls
    assert (None, 'green.ico', client) in set_tab_calls
    assert all(title is None for title, _, _ in set_tab_calls)

    cleanup = getattr(client, 'cvd_runtime_title_listener_cleanup')
    cleanup()

    assert controller.session_callbacks == []
    assert controller.motion_callbacks == []


def test_register_client_runtime_title_sync_resyncs_on_same_client_reregistration(monkeypatch) -> None:
    controller = _ControllerStub()
    controller.status = {
        'is_active': True,
        'session_id': 'session-1',
        'session_start_time': None,
        'recent_motion_detected': True,
    }
    client = SimpleNamespace(title=None, js_calls=[])
    client.run_javascript = lambda code: client.js_calls.append(code)
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_check_circle_green', lambda: 'green.ico')
    monkeypatch.setattr(gui_, 'favicon_sensors_off_orange', lambda: 'orange.ico')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(gui_, 'set_tab', _make_recording_set_tab(set_tab_calls))

    gui_.register_client_runtime_title_sync(
        measurement_controller=controller,
        client=client,
    )

    assert set_tab_calls == [('Tracker', 'green.ico', client)]
    assert len(client.js_calls) == 1
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR) == (True, True)
    assert len(controller.session_callbacks) == 1
    assert len(controller.motion_callbacks) == 1

    set_tab_calls.clear()
    client.js_calls.clear()

    gui_.register_client_runtime_title_sync(
        measurement_controller=controller,
        client=client,
    )

    assert set_tab_calls == [(None, 'green.ico', client)]
    assert len(client.js_calls) == 1
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR) == (True, True)
    assert len(controller.session_callbacks) == 1
    assert len(controller.motion_callbacks) == 1


def test_runtime_state_sync_updates_only_favicon_after_initial_title_sync(monkeypatch) -> None:
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )
    client = _DummyClient()
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_check_circle_green', lambda: 'green.ico')
    monkeypatch.setattr(gui_, 'favicon_sensors_off_orange', lambda: 'orange.ico')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(gui_, 'set_tab', _make_recording_set_tab(set_tab_calls))

    gui_.sync_runtime_gui_title(
        title='Tracker',
        client=client,
        measurement_state={
            'is_active': False,
            'session_id': None,
            'session_start_time': None,
            'recent_motion_detected': None,
        },
    )
    assert set_tab_calls == [('Tracker', 'default.ico', client)]
    set_tab_calls.clear()

    gui_.sync_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=None,
        client=client,
    )

    assert set_tab_calls == [(None, 'neutral.ico', client)]
    assert client.title == 'Tracker'


def test_failed_set_tab_does_not_poison_runtime_title_signature_cache(monkeypatch) -> None:
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )
    client = _DummyClient()
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []

    def _flaky_set_tab(title=None, icon_url=None, client=None):
        set_tab_calls.append((title, icon_url, client))
        if len(set_tab_calls) == 1:
            raise RuntimeError('boom')
        if title is not None and client is not None and hasattr(client, 'title'):
            client.title = title
        return True

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(gui_, 'set_tab', _flaky_set_tab)

    gui_.sync_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=None,
        client=client,
    )

    assert set_tab_calls == [('Tracker', 'neutral.ico', client)]
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR, None) is None
    assert not hasattr(client, gui_._CLIENT_RUNTIME_TITLE_PENDING_SIGNATURE_ATTR)

    gui_.sync_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=None,
        client=client,
    )

    assert set_tab_calls == [
        ('Tracker', 'neutral.ico', client),
        ('Tracker', 'neutral.ico', client),
    ]
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR) == (True, None)
    assert not hasattr(client, gui_._CLIENT_RUNTIME_TITLE_PENDING_SIGNATURE_ATTR)


def test_failed_header_sync_does_not_poison_runtime_title_signature_cache(monkeypatch) -> None:
    dummy_app = SimpleNamespace(
        config=SimpleNamespace(title=''),
        storage=SimpleNamespace(general={}),
    )
    client = _DummyClient()
    set_tab_calls: list[tuple[str | None, str | None, object | None]] = []

    def _failing_run_javascript(code: str) -> None:
        client.js_calls.append(code)
        if len(client.js_calls) == 1:
            raise RuntimeError('header boom')

    client.run_javascript = _failing_run_javascript

    monkeypatch.setattr(gui_, 'app', dummy_app)
    monkeypatch.setattr(gui_, 'compute_gui_title', lambda: 'Tracker')
    monkeypatch.setattr(gui_, 'favicon_radio_button_checked_neutral', lambda: 'neutral.ico')
    monkeypatch.setattr(gui_, 'get_default_favicon_url', lambda: 'default.ico')
    monkeypatch.setattr(gui_, 'set_tab', _make_recording_set_tab(set_tab_calls))

    gui_.sync_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=None,
        client=client,
    )

    assert set_tab_calls == [('Tracker', 'neutral.ico', client)]
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR, None) is None
    assert not hasattr(client, gui_._CLIENT_RUNTIME_TITLE_PENDING_SIGNATURE_ATTR)

    gui_.sync_runtime_measurement_state(
        is_active=True,
        session_id='session-1',
        session_start_time=None,
        recent_motion_detected=None,
        client=client,
    )

    assert set_tab_calls == [
        ('Tracker', 'neutral.ico', client),
        ('Tracker', 'neutral.ico', client),
    ]
    assert getattr(client, gui_._CLIENT_RUNTIME_TITLE_SIGNATURE_ATTR) == (True, None)
    assert not hasattr(client, gui_._CLIENT_RUNTIME_TITLE_PENDING_SIGNATURE_ATTR)
