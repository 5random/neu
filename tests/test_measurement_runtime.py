from src.config import _create_default_config
from src.measurement import MeasurementController


class _CameraStub:
    def __init__(self) -> None:
        self.enabled_callbacks = []
        self.disabled_callbacks = []

    def enable_motion_detection(self, callback) -> None:
        self.enabled_callbacks.append(callback)

    def disable_motion_detection(self, callback) -> None:
        self.disabled_callbacks.append(callback)


class _EmailSystemStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def reset_alert_state(self, session_id=None) -> None:
        self.events.append(('reset', session_id))

    def send_measurement_event(self, **_kwargs) -> None:
        return None


def test_measurement_controller_set_camera_rebinds_motion_listener() -> None:
    cfg = _create_default_config()
    first_camera = _CameraStub()
    second_camera = _CameraStub()
    controller = MeasurementController(cfg.measurement, email_system=None, camera=first_camera)

    try:
        assert len(first_camera.enabled_callbacks) == 1
        listener = first_camera.enabled_callbacks[0]

        controller.set_camera(None)

        assert controller.camera is None
        assert first_camera.disabled_callbacks == [listener]

        controller.set_camera(second_camera)

        assert controller.camera is second_camera
        assert second_camera.enabled_callbacks == [listener]
    finally:
        controller.cleanup()


def test_measurement_controller_notifies_session_state_callbacks_on_start_and_stop() -> None:
    cfg = _create_default_config()
    camera = _CameraStub()
    controller = MeasurementController(cfg.measurement, email_system=None, camera=camera)
    events: list[dict[str, object]] = []

    def _listener(payload: dict[str, object]) -> None:
        events.append(dict(payload))

    controller.register_session_state_callback(_listener)

    try:
        assert controller.start_session(session_id='session-1') is True
        assert controller.stop_session(reason='manual') is True
    finally:
        controller.cleanup()

    assert events[0]['is_active'] is True
    assert events[0]['session_id'] == 'session-1'
    assert events[0]['session_start_time'] is not None
    assert events[1] == {
        'is_active': False,
        'session_id': None,
        'session_start_time': None,
    }


def test_measurement_controller_notifies_session_callbacks_before_email_state_sync() -> None:
    cfg = _create_default_config()
    camera = _CameraStub()
    email_system = _EmailSystemStub()
    controller = MeasurementController(cfg.measurement, email_system=email_system, camera=camera)
    events: list[tuple[str, object]] = []

    def _listener(payload: dict[str, object]) -> None:
        events.append(('callback', dict(payload)))

    original_reset_alert_state = email_system.reset_alert_state

    def _recording_reset_alert_state(session_id=None) -> None:
        events.append(('reset', session_id))
        original_reset_alert_state(session_id=session_id)

    email_system.reset_alert_state = _recording_reset_alert_state
    controller.register_session_state_callback(_listener)

    try:
        assert controller.start_session(session_id='session-1') is True
        assert controller.stop_session(reason='manual') is True
    finally:
        controller.cleanup()

    assert [event_type for event_type, _payload in events] == ['callback', 'reset', 'callback', 'reset']
    assert events[0][1]['is_active'] is True
    assert events[0][1]['session_id'] == 'session-1'
    assert events[0][1]['session_start_time'] is not None
    assert events[1] == ('reset', 'session-1')
    assert events[2][1] == {
        'is_active': False,
        'session_id': None,
        'session_start_time': None,
    }
    assert events[3] == ('reset', None)
