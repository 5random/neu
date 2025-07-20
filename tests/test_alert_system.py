from datetime import datetime

from src.alert import AlertSystem
from src.config import EmailConfig, MeasurementConfig


class DummyROI:
    def __init__(self, enabled=True):
        self.enabled = enabled


class DummyMotionCfg:
    def __init__(self):
        self.sensitivity = 0.5

    def get_roi(self):
        return DummyROI(True)


class DummyWebcamCfg:
    def __init__(self):
        self.camera_index = 0


class DummyAppCfg:
    def __init__(self):
        self.webcam = DummyWebcamCfg()
        self.motion_detection = DummyMotionCfg()


def create_alert_system(recipients=None):
    if recipients is None:
        recipients = ["a@example.com", "b@example.com"]
    email_cfg = EmailConfig(
        website_url="http://example.com",
        recipients=recipients,
        smtp_server="smtp.example.com",
        smtp_port=25,
        sender_email="sender@example.com",
        templates={"alert": {"subject": "Alert {timestamp}", "body": "Body {timestamp}"}},
    )
    meas_cfg = MeasurementConfig(
        auto_start=False,
        session_timeout_minutes=1,
        save_alert_images=False,
        image_save_path=".",
        image_format="jpg",
        image_quality=80,
        alert_delay_seconds=60,
    )
    return AlertSystem(email_cfg, meas_cfg, DummyAppCfg())


def test_send_motion_alert_without_camera(monkeypatch):
    alert = create_alert_system()
    sent = []

    def dummy_send_batch(messages):
        sent.extend(messages)
        return len(messages)

    monkeypatch.setattr(alert, "_send_emails_batch", dummy_send_batch)

    assert alert.send_motion_alert(last_motion_time=datetime.now()) is True
    assert len(sent) == len(alert.email_config.recipients)
    for recipient, msg in sent:
        # only text payload expected
        assert len(msg.get_payload()) == 1
