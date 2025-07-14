from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.measurement import MeasurementController
from src.config import MeasurementConfig


def create_controller(alert_delay=60):
    cfg = MeasurementConfig(
        auto_start=False,
        session_timeout_minutes=10,
        save_alert_images=False,
        image_save_path='.',
        image_format='jpg',
        image_quality=80,
        alert_delay_seconds=alert_delay,
    )
    return MeasurementController(cfg)


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
