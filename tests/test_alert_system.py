import numpy as np
from datetime import datetime, timedelta

from src.config import _create_default_config
from src.measurement import MeasurementController, resolve_measurement_stop_event
from src.notify import EMailSystem


def _get_plain_text_parts(msg):
    return [
        part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
        for part in msg.walk()
        if part.get_content_type() == "text/plain"
    ]


def test_send_motion_alert_includes_image(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.save_alert_images = True
    # Ensure recipients list not empty and minimal valid email settings
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session")

    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_save_alert_image", lambda self, buf, name: None)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    assert email_system.send_motion_alert(datetime.now(), "session", frame)
    # One message per recipient
    assert len(sent_messages) == len(cfg.email.recipients)

    for _, msg in sent_messages:
        images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
        assert len(images) == 1


def test_send_motion_alert_uses_single_recipient_headers(monkeypatch):
    cfg = _create_default_config()
    cfg.email.recipients = ["first@example.com", "second@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25
    cfg.measurement.alert_include_snapshot = False
    cfg.measurement.save_alert_images = False

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session")
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    assert email_system.send_motion_alert(datetime.now(), "session", np.zeros((4, 4, 3), dtype=np.uint8))
    assert [recipient for recipient, _ in sent_messages] == cfg.email.recipients
    assert [msg["To"] for _, msg in sent_messages] == cfg.email.recipients


def test_send_motion_alert_respects_snapshot_flag(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_include_snapshot = False
    cfg.measurement.save_alert_images = False
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session")
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    assert email_system.send_motion_alert(datetime.now(), "session", np.zeros((10, 10, 3), dtype=np.uint8))
    assert len(sent_messages) == 1
    images = [p for p in sent_messages[0][1].walk() if p.get_content_maintype() == "image"]
    assert images == []
    plain_text_parts = _get_plain_text_parts(sent_messages[0][1])
    assert len(plain_text_parts) == 1
    assert "Attached is the current webcam image." not in plain_text_parts[0]


def test_send_motion_alert_fallback_body_omits_snapshot_notice_without_attachment(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_include_snapshot = False
    cfg.measurement.save_alert_images = False
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25
    cfg.email.templates["alert"] = {"subject": "{missing}", "body": "{missing}"}

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session")
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    assert email_system.send_motion_alert(datetime.now(), "session", np.zeros((10, 10, 3), dtype=np.uint8))
    assert len(sent_messages) == 1
    plain_text_parts = _get_plain_text_parts(sent_messages[0][1])
    assert len(plain_text_parts) == 1
    assert "Attached is the current webcam image." not in plain_text_parts[0]


def test_send_motion_alert_removes_custom_attachment_hint_lines(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_include_snapshot = False
    cfg.measurement.save_alert_images = False
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25
    cfg.email.templates["alert"] = {
        "subject": "Alert - {timestamp}",
        "body": (
            "Custom text\n"
            "Sensor is detached from mount.\n"
            "Image will be attached separately.\n"
            "No motion since {last_motion_time}."
        ),
    }

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session")
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    assert email_system.send_motion_alert(datetime.now(), "session", np.zeros((10, 10, 3), dtype=np.uint8))
    plain_text_parts = _get_plain_text_parts(sent_messages[0][1])
    assert len(plain_text_parts) == 1
    assert "detached from mount" in plain_text_parts[0].lower()
    assert "attached separately" not in plain_text_parts[0].lower()
    assert "No motion since" in plain_text_parts[0]


def test_send_motion_alert_rejects_alerts_for_invalidated_session(monkeypatch):
    cfg = _create_default_config()
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    email_system.reset_alert_state(session_id="session-1")
    email_system.reset_alert_state(session_id=None)

    assert email_system.send_motion_alert(datetime.now(), "session-1", np.zeros((4, 4, 3), dtype=np.uint8)) is False
    assert sent_messages == []


def test_send_motion_alert_aborts_before_send_when_session_is_invalidated(monkeypatch):
    import threading

    cfg = _create_default_config()
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session-1")
    send_calls = []

    class _BlockingSMTP:
        entered = threading.Event()
        release = threading.Event()

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            type(self).entered.set()
            type(self).release.wait(timeout=2)
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def sendmail(self, sender, recipients, message):
            send_calls.append((sender, tuple(recipients)))
            return {}

    monkeypatch.setattr("src.notify.smtplib.SMTP", _BlockingSMTP)

    result_holder = {}

    def _run_send():
        result_holder["value"] = email_system.send_motion_alert(
            datetime.now(),
            "session-1",
            np.zeros((4, 4, 3), dtype=np.uint8),
        )

    worker = threading.Thread(target=_run_send, daemon=True)
    worker.start()
    assert _BlockingSMTP.entered.wait(timeout=1)

    email_system.reset_alert_state(session_id=None)
    _BlockingSMTP.release.set()
    worker.join(timeout=2)

    assert result_holder["value"] is False
    assert send_calls == []


def test_send_motion_alert_aborted_after_partial_delivery_is_not_success(monkeypatch):
    from pathlib import Path

    cfg = _create_default_config()
    cfg.email.recipients = ["first@example.com", "second@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session-1")
    send_calls = []
    saved_image = Path("test_output_partial_alert.jpg")
    saved_image.write_bytes(b"alert-image")

    class _AbortAfterFirstRecipientSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def sendmail(self, sender, recipients, message):
            send_calls.append((sender, tuple(recipients)))
            if len(send_calls) == 1:
                email_system.reset_alert_state(session_id=None)
            return {}

    monkeypatch.setattr("src.notify.smtplib.SMTP", _AbortAfterFirstRecipientSMTP)
    monkeypatch.setattr(EMailSystem, "_save_alert_image", lambda self, buf, name: saved_image)

    try:
        result = email_system.send_motion_alert(
            datetime.now(),
            "session-1",
            np.zeros((4, 4, 3), dtype=np.uint8),
        )

        assert result is False
        assert send_calls == [("sender@example.com", ("first@example.com",))]
        assert saved_image.exists()
        assert email_system.alerts_sent_count == 0
    finally:
        saved_image.unlink(missing_ok=True)


def test_send_motion_alert_normalizes_legacy_subject_newlines(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_include_snapshot = False
    cfg.measurement.save_alert_images = False
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25
    cfg.email.templates["alert"] = {"subject": "Foo\\nBar", "body": "Body"}

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    email_system.reset_alert_state(session_id="session-1")
    serialized_messages = []

    class _SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def sendmail(self, sender, recipients, message):
            serialized_messages.append(message)
            return {}

    monkeypatch.setattr("src.notify.smtplib.SMTP", _SMTP)

    assert email_system.send_motion_alert(datetime.now(), "session-1", None) is True
    assert len(serialized_messages) == 1
    assert "Subject: Foo Bar" in serialized_messages[0]
    assert "Subject: Foo\nBar" not in serialized_messages[0]


def test_get_alert_status_reflects_invalidated_session():
    cfg = _create_default_config()
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        email_system.reset_alert_state(session_id="session-1")
        assert email_system.get_alert_status()["can_send_alert"] is True

        email_system.reset_alert_state(session_id=None)
        assert email_system.can_send_alert(session_id="session-1") is False
        assert email_system.get_alert_status()["can_send_alert"] is False
    finally:
        email_system.close()


def test_alert_limits_use_cooldown_and_max_per_session():
    cfg = _create_default_config()
    cfg.measurement.alert_cooldown_seconds = 120
    cfg.measurement.max_alerts_per_session = 2
    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    try:
        email_system.reset_alert_state(session_id="session-1")
        assert email_system.can_send_alert(session_id="session-1") is True

        email_system.last_alert_time = datetime.now()
        assert email_system.can_send_alert(session_id="session-1") is False

        email_system.last_alert_time = None
        email_system.alerts_sent_count = 2
        assert email_system.can_send_alert(session_id="session-1") is False
    finally:
        email_system.close()


def test_measurement_controller_retries_failed_alerts_without_motion(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_delay_seconds = 30
    cfg.measurement.alert_check_interval = 0.1

    class _RetryingEmailSystem:
        def __init__(self):
            self.calls = 0

        def can_send_alert(self, session_id=None):
            return True

        def send_motion_alert(self, **kwargs):
            self.calls += 1
            return self.calls >= 2

        def reset_alert_state(self, session_id=None):
            return None

        def send_measurement_event(self, **kwargs):
            return True

    email_system = _RetryingEmailSystem()
    controller = MeasurementController(cfg.measurement, email_system=email_system, camera=None)
    controller._executor.submit = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    controller.is_session_active = True
    controller.session_id = "session-1"
    controller.last_motion_time = datetime.now() - timedelta(seconds=31)
    monkeypatch.setattr(controller, "_confirm_no_motion", lambda duration: True)

    with controller.session_lock:
        controller._check_alert_trigger_locked("session-1")

    assert email_system.calls == 1
    assert controller.alert_triggered is True
    assert controller._alert_dispatch_in_progress is False

    controller._last_alert_attempt_monotonic = 0.0
    monkeypatch.setattr("src.measurement.time.monotonic", lambda: 999.0)
    with controller.session_lock:
        controller._check_alert_trigger_locked("session-1")

    assert email_system.calls == 2
    controller.cleanup()


def test_stale_alert_worker_does_not_clear_current_dispatch_state():
    cfg = _create_default_config()

    class _EmailSystem:
        def can_send_alert(self, session_id=None):
            return True

        def send_motion_alert(self, **kwargs):
            raise AssertionError("stale alert worker must not send an email")

        def reset_alert_state(self, session_id=None):
            return None

        def send_measurement_event(self, **kwargs):
            return True

    controller = MeasurementController(cfg.measurement, email_system=_EmailSystem(), camera=None)
    try:
        controller._save_alert_to_history = lambda *args, **kwargs: None

        with controller.session_lock:
            controller.is_session_active = True
            controller.session_id = "session-1"
            controller._reset_alert_tracking_locked()
            stale_generation = controller._alert_generation
            controller._alert_dispatch_in_progress = True
            controller._alert_dispatch_generation = stale_generation

            controller._reset_alert_tracking_locked()
            controller.session_id = "session-1"
            controller.is_session_active = True
            current_generation = controller._alert_generation
            controller._alert_dispatch_in_progress = True
            controller._alert_dispatch_generation = current_generation

        assert controller.trigger_alert_sync("session-1", stale_generation) is False
        with controller.session_lock:
            assert controller._alert_dispatch_in_progress is True
            assert controller._alert_dispatch_generation == current_generation
    finally:
        controller.cleanup()


def test_stop_session_invalidates_queued_alerts(monkeypatch):
    cfg = _create_default_config()
    cfg.measurement.alert_delay_seconds = 1
    queued_calls = []

    class _EmailSystem:
        def __init__(self):
            self.alert_calls = []
            self.reset_calls = []
            self.event_calls = []

        def can_send_alert(self, session_id=None):
            return True

        def send_motion_alert(self, **kwargs):
            self.alert_calls.append(kwargs)
            return True

        def reset_alert_state(self, session_id=None):
            self.reset_calls.append(session_id)

        def send_measurement_event(self, **kwargs):
            self.event_calls.append(kwargs)
            return True

    email_system = _EmailSystem()
    controller = MeasurementController(cfg.measurement, email_system=email_system, camera=None)
    try:
        monkeypatch.setattr(controller, "_confirm_no_motion", lambda duration: True)
        monkeypatch.setattr(controller, "_save_alert_to_history", lambda *args, **kwargs: None)
        controller._executor.submit = lambda fn, *args, **kwargs: queued_calls.append((fn, args, kwargs))

        email_system.reset_alert_state(session_id="session-1")
        controller.is_session_active = True
        controller.session_id = "session-1"
        controller.session_start_time = datetime.now()
        controller.last_motion_time = datetime.now() - timedelta(seconds=2)

        with controller.session_lock:
            controller._check_alert_trigger_locked("session-1")

        assert len(queued_calls) == 1
        assert email_system.alert_calls == []

        controller.stop_session(reason="manual")

        for fn, args, kwargs in queued_calls:
            fn(*args, **kwargs)

        assert email_system.reset_calls == ["session-1", None]
        assert email_system.alert_calls == []
    finally:
        controller.cleanup()


def test_stop_session_aborts_inflight_alert_send(monkeypatch):
    import threading

    cfg = _create_default_config()
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    controller = MeasurementController(cfg.measurement, email_system=email_system, camera=None)
    send_entered = threading.Event()
    release_send = threading.Event()
    sent_messages = []

    def fake_send_emails_batch(self, messages, max_retries=3, abort_check=None):
        send_entered.set()
        assert release_send.wait(timeout=2)
        if abort_check is not None:
            abort_check()
        sent_messages.extend(messages)
        return len(messages)

    monkeypatch.setattr(EMailSystem, "_send_emails_batch", fake_send_emails_batch)
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)
    monkeypatch.setattr(controller, "_save_alert_to_history", lambda *args, **kwargs: None)
    controller._executor.submit = lambda fn, *args, **kwargs: None

    try:
        email_system.reset_alert_state(session_id="session-1")
        with controller.session_lock:
            controller.is_session_active = True
            controller.session_id = "session-1"
            controller.session_start_time = datetime.now()
            controller.last_motion_time = datetime.now()
            controller._reset_alert_tracking_locked()
            generation = controller._alert_generation
            controller._alert_dispatch_in_progress = True
            controller._alert_dispatch_generation = generation

        result_holder = {}

        def _run_alert():
            result_holder["value"] = controller.trigger_alert_sync("session-1", generation)

        worker = threading.Thread(target=_run_alert, daemon=True)
        worker.start()
        assert send_entered.wait(timeout=1)

        assert controller.stop_session(reason="manual") is True
        release_send.set()
        worker.join(timeout=2)

        assert result_holder["value"] is False
        assert sent_messages == []
    finally:
        email_system.close()
        controller.cleanup()


def test_measurement_events_preserve_start_stop_order():
    import threading
    import time

    cfg = _create_default_config()
    calls = []
    done = threading.Event()

    class _EmailSystem:
        def reset_alert_state(self, session_id=None):
            return None

        def send_measurement_event(self, **kwargs):
            if kwargs["event"] == "start":
                time.sleep(0.2)
            calls.append(kwargs["event"])
            if len(calls) >= 2:
                done.set()
            return True

    controller = MeasurementController(cfg.measurement, email_system=_EmailSystem(), camera=None)
    try:
        assert controller.start_session("session-1") is True
        assert controller.stop_session(reason="manual") is True
        assert done.wait(timeout=1)
        assert calls[:2] == ["start", "stop"]
    finally:
        controller.cleanup()


def test_measurement_events_preserve_order_when_start_reset_blocks():
    import threading

    cfg = _create_default_config()
    calls = []
    reset_started = threading.Event()
    release_start_reset = threading.Event()
    events_done = threading.Event()

    class _EmailSystem:
        def reset_alert_state(self, session_id=None):
            if session_id is not None:
                reset_started.set()
                assert release_start_reset.wait(timeout=2)
            return None

        def send_measurement_event(self, **kwargs):
            calls.append(kwargs["event"])
            if len(calls) >= 2:
                events_done.set()
            return True

    controller = MeasurementController(cfg.measurement, email_system=_EmailSystem(), camera=None)
    start_result = {}

    try:
        worker = threading.Thread(
            target=lambda: start_result.setdefault("value", controller.start_session("session-1")),
            daemon=True,
        )
        worker.start()
        assert reset_started.wait(timeout=1)

        assert controller.stop_session(reason="manual") is True
        release_start_reset.set()
        worker.join(timeout=2)

        assert start_result["value"] is True
        assert events_done.wait(timeout=1)
        assert calls[:2] == ["start", "stop"]
    finally:
        release_start_reset.set()
        controller.cleanup()


def test_cleanup_waits_for_shutdown_event_delivery():
    import threading

    cfg = _create_default_config()
    event_started = threading.Event()
    release_event = threading.Event()
    cleanup_done = threading.Event()
    calls = []

    class _EmailSystem:
        def reset_alert_state(self, session_id=None):
            return None

        def send_measurement_event(self, **kwargs):
            calls.append(kwargs["event"])
            if kwargs["reason"] == "shutdown":
                event_started.set()
                assert release_event.wait(timeout=2)
            return True

    controller = MeasurementController(cfg.measurement, email_system=_EmailSystem(), camera=None)
    controller.is_session_active = True
    controller.session_id = "session-1"
    controller.session_start_time = datetime.now()
    controller.last_motion_time = datetime.now()

    worker = threading.Thread(
        target=lambda: (controller.cleanup(), cleanup_done.set()),
        daemon=True,
    )
    worker.start()

    try:
        assert event_started.wait(timeout=1)
        assert cleanup_done.wait(timeout=0.2) is False
        release_event.set()
        worker.join(timeout=2)
        assert cleanup_done.wait(timeout=0.2) is True
        assert "stop" in calls
    finally:
        release_event.set()


def test_stop_reason_timeout_maps_to_end_event():
    assert resolve_measurement_stop_event("timeout") == "end"
    assert resolve_measurement_stop_event("manual") == "stop"

