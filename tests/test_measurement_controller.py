from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.measurement import MeasurementController
from src.config import MeasurementConfig


def create_controller(alert_delay=60, camera=None, alert_system=None):
    cfg = MeasurementConfig(
        auto_start=False,
        session_timeout_minutes=10,
        save_alert_images=False,
        image_save_path='.',
        image_format='jpg',
        image_quality=80,
        alert_delay_seconds=alert_delay,
    )
    return MeasurementController(cfg, alert_system, camera)


def test_last_motion_initialized_on_start():
    mc = create_controller()
    assert mc.last_motion_time is None
    mc.start_session("s1")
    assert mc.session_start_time is not None
    assert mc.last_motion_time == mc.session_start_time


def test_should_trigger_uses_session_start_when_no_motion():
    mc = create_controller(alert_delay=1)
    mc.start_session("s1")
    # Simulate that no motion timestamp is recorded
    mc.last_motion_time = None
    mc.session_start_time = datetime.now() - timedelta(seconds=2)
    assert mc.should_trigger_alert() is True

    mc.last_motion_time = None
    mc.session_start_time = datetime.now()
    assert mc.should_trigger_alert() is False


def test_trigger_alert_without_camera_passes_none(monkeypatch):
    called = {}

    class DummyAlert:
        def send_motion_alert(self, last_motion_time=None, session_id=None, camera_frame=None):
            called['frame'] = camera_frame
            return True

    alert = DummyAlert()
    mc = create_controller(alert_delay=0, alert_system=alert)
    mc.start_session('s1')
    mc.last_motion_time = mc.session_start_time - timedelta(seconds=1)

    assert mc.trigger_alert() is True
    assert called['frame'] is None


def test_trigger_alert_with_camera_snapshot(monkeypatch):
    called = {}

    class DummyAlert:
        def send_motion_alert(self, last_motion_time=None, session_id=None, camera_frame=None):
            called['frame'] = camera_frame
            return True

    class DummyCamera:
        def take_snapshot(self):
            return 'frame-data'

    camera = DummyCamera()
    alert = DummyAlert()
    mc = create_controller(alert_delay=0, camera=camera, alert_system=alert)
    mc.start_session('s1')
    mc.last_motion_time = mc.session_start_time - timedelta(seconds=1)

    assert mc.trigger_alert() is True
    assert called['frame'] == 'frame-data'


def test_trigger_alert_calls_alert_system_when_delay_elapsed():
    called = {'count': 0}

    class DummyAlert:
        def send_motion_alert(self, last_motion_time=None, session_id=None, camera_frame=None):
            called['count'] += 1
            return True

    alert = DummyAlert()
    mc = create_controller(alert_delay=1, alert_system=alert)
    mc.start_session('s1')
    mc.last_motion_time = mc.session_start_time - timedelta(seconds=2)

    assert mc.trigger_alert() is True
    assert called['count'] == 1
