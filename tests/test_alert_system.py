import numpy as np
from datetime import datetime

from src.alert import AlertSystem
from src.config import _create_default_config


def test_send_motion_alert_includes_image(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.save_alert_images = True
    # Ensure recipients list not empty and minimal valid email settings
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    alert = AlertSystem(cfg.email, cfg.measurement, cfg)

    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(AlertSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(AlertSystem, "_save_alert_image", lambda self, buf, name: None)
    monkeypatch.setattr(AlertSystem, "_should_send_alert_unsafe", lambda self: True)

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    assert alert.send_motion_alert(datetime.now(), "session", frame)
    # One message per recipient
    assert len(sent_messages) == len(cfg.email.recipients)

    for _, msg in sent_messages:
        images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
        assert len(images) == 1

