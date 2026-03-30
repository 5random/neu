import json
from datetime import datetime

import numpy as np

import src.alert_history as alert_history
from src.alert_history import replace_history_entries
from src.config import _create_default_config
from src.measurement import MeasurementController
from src.notify import EMailSystem


def _alert_entry(index: int, image_path: str, *, details: str = "No motion detected") -> dict[str, object]:
    return {
        "timestamp": f"2026-03-30 12:{index:02d}:00",
        "session_id": f"session-{index}",
        "type": "alert",
        "image_path": image_path,
        "details": details,
        "email_sent": bool(index % 2),
    }


def test_trigger_alert_sync_persists_image_only_in_history_dir(monkeypatch, tmp_path):
    cfg = _create_default_config()
    history_dir = tmp_path / "history"
    legacy_dir = tmp_path / "alerts"
    cfg.measurement.history_path = str(history_dir)
    cfg.measurement.image_save_path = str(legacy_dir)
    cfg.email.recipients = ["recipient@example.com"]
    cfg.email.sender_email = "sender@example.com"
    cfg.email.smtp_server = "localhost"
    cfg.email.smtp_port = 25

    email_system = EMailSystem(cfg.email, cfg.measurement, cfg)
    controller = MeasurementController(cfg.measurement, email_system=email_system, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    class _Camera:
        def take_snapshot(self):
            return frame

    controller.camera = _Camera()
    monkeypatch.setattr(EMailSystem, "_send_emails_batch", lambda self, messages, max_retries=3, abort_check=None: len(messages))
    monkeypatch.setattr(EMailSystem, "_should_send_alert_unsafe", lambda self: True)

    try:
        email_system.reset_alert_state(session_id="session-1")
        with controller.session_lock:
            controller.is_session_active = True
            controller.session_id = "session-1"
            controller.last_motion_time = datetime.now()
            controller._reset_alert_tracking_locked()
            generation = controller._alert_generation
            controller._alert_dispatch_in_progress = True
            controller._alert_dispatch_generation = generation

        assert controller.trigger_alert_sync("session-1", generation) is True

        history_file = history_dir / "history.json"
        entries = json.loads(history_file.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["image_path"]
        assert (history_dir / entries[0]["image_path"]).exists()
        assert not legacy_dir.exists()
    finally:
        controller.cleanup()
        email_system.close()


def test_replace_history_entries_limits_persisted_images_to_latest_25(tmp_path):
    history_file = tmp_path / "history.json"
    entries = []
    for index in reversed(range(30)):
        image_name = f"alert_{index:02d}.jpg"
        (tmp_path / image_name).write_bytes(f"img-{index}".encode("utf-8"))
        entries.append(_alert_entry(index, image_name))

    replace_history_entries(entries, history_file=history_file)

    stored_entries = json.loads(history_file.read_text(encoding="utf-8"))
    stored_by_session = {entry["session_id"]: entry for entry in stored_entries}
    cleared_count = len(entries) - alert_history.MAX_HISTORY_IMAGE_FILES
    assert len(stored_entries) == len(entries)

    for index in range(cleared_count):
        assert stored_by_session[f"session-{index}"]["image_path"] == ""
        assert not (tmp_path / f"alert_{index:02d}.jpg").exists()
    for index in range(cleared_count, len(entries)):
        assert stored_by_session[f"session-{index}"]["image_path"] == f"alert_{index:02d}.jpg"
        assert (tmp_path / f"alert_{index:02d}.jpg").exists()


def test_replace_history_entries_max_entry_limit_keeps_newest_entries_when_unsorted(tmp_path, monkeypatch):
    history_file = tmp_path / "history.json"
    entries = []
    for index in (3, 1, 2, 0):
        image_name = f"alert_max_{index}.jpg"
        (tmp_path / image_name).write_bytes(f"img-{index}".encode("utf-8"))
        entries.append(_alert_entry(index, image_name))

    monkeypatch.setattr(alert_history, "MAX_HISTORY_ENTRIES", 2)

    replace_history_entries(entries, history_file=history_file)

    stored_entries = json.loads(history_file.read_text(encoding="utf-8"))
    assert {entry["session_id"] for entry in stored_entries} == {"session-2", "session-3"}
    assert not (tmp_path / "alert_max_0.jpg").exists()
    assert not (tmp_path / "alert_max_1.jpg").exists()
    assert (tmp_path / "alert_max_2.jpg").exists()
    assert (tmp_path / "alert_max_3.jpg").exists()


def test_replace_history_entries_trims_oldest_entries_when_size_limit_exceeded(tmp_path, monkeypatch):
    history_file = tmp_path / "history.json"
    entries = []
    for index in range(3):
        image_name = f"alert_size_{index}.jpg"
        (tmp_path / image_name).write_bytes(f"img-{index}".encode("utf-8"))
        entries.append(_alert_entry(index, image_name, details="x" * 512))

    size_limit = alert_history._serialized_history_entries_size_bytes(entries[1:])
    monkeypatch.setattr(alert_history, "MAX_HISTORY_FILE_SIZE_BYTES", size_limit)

    replace_history_entries(entries, history_file=history_file)

    stored_entries = json.loads(history_file.read_text(encoding="utf-8"))
    assert [entry["session_id"] for entry in stored_entries] == ["session-1", "session-2"]
    assert history_file.stat().st_size <= size_limit
    assert not (tmp_path / "alert_size_0.jpg").exists()
    assert (tmp_path / "alert_size_1.jpg").exists()
    assert (tmp_path / "alert_size_2.jpg").exists()


def test_replace_history_entries_size_limit_removes_oldest_when_entries_are_unsorted(tmp_path, monkeypatch):
    history_file = tmp_path / "history.json"
    entries = []
    for index in (3, 1, 2, 0):
        image_name = f"alert_unsorted_size_{index}.jpg"
        (tmp_path / image_name).write_bytes(f"img-{index}".encode("utf-8"))
        entries.append(_alert_entry(index, image_name, details="x" * 512))

    size_limit = alert_history._serialized_history_entries_size_bytes([entries[0], entries[2]])
    monkeypatch.setattr(alert_history, "MAX_HISTORY_FILE_SIZE_BYTES", size_limit)

    replace_history_entries(entries, history_file=history_file)

    stored_entries = json.loads(history_file.read_text(encoding="utf-8"))
    assert {entry["session_id"] for entry in stored_entries} == {"session-2", "session-3"}
    assert history_file.stat().st_size <= size_limit
    assert not (tmp_path / "alert_unsorted_size_0.jpg").exists()
    assert not (tmp_path / "alert_unsorted_size_1.jpg").exists()
    assert (tmp_path / "alert_unsorted_size_2.jpg").exists()
    assert (tmp_path / "alert_unsorted_size_3.jpg").exists()


def test_replace_history_entries_with_empty_list_removes_alert_images(tmp_path):
    history_file = tmp_path / "history.json"
    entries = []
    for index in range(2):
        image_name = f"alert_clear_{index}.jpg"
        (tmp_path / image_name).write_bytes(f"img-{index}".encode("utf-8"))
        entries.append(_alert_entry(index, image_name))

    replace_history_entries(entries, history_file=history_file)
    replace_history_entries([], history_file=history_file)

    assert json.loads(history_file.read_text(encoding="utf-8")) == []
    assert list(tmp_path.glob("alert_*.jpg")) == []


def test_measurement_config_ensure_save_path_uses_history_dir(tmp_path):
    cfg = _create_default_config()
    history_dir = tmp_path / "history"
    legacy_dir = tmp_path / "alerts"
    cfg.measurement.history_path = str(history_dir)
    cfg.measurement.image_save_path = str(legacy_dir)

    cfg.measurement.ensure_save_path()

    assert history_dir.exists()
    assert history_dir.is_dir()
    assert not legacy_dir.exists()


def test_measurement_config_ensure_save_path_falls_back_to_legacy_dir_when_history_path_is_empty(tmp_path):
    cfg = _create_default_config()
    legacy_dir = tmp_path / "alerts"
    cfg.measurement.history_path = ""
    cfg.measurement.image_save_path = str(legacy_dir)

    cfg.measurement.ensure_save_path()

    assert legacy_dir.exists()
    assert legacy_dir.is_dir()
