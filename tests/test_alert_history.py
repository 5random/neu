import json

import numpy as np

from src.alert_history import append_history_entry, build_history_image_url, parse_history_timestamp, resolve_history_image_path
from src.config import _create_default_config
from src.measurement import MeasurementController


def test_append_history_entry_repairs_non_list_history_file(tmp_path):
    history_file = tmp_path / 'history.json'
    history_file.write_text('{}', encoding='utf-8')

    entry = {
        'timestamp': '2026-03-15 12:00:00',
        'session_id': 'session-1',
        'type': 'alert',
        'image_path': '',
    }

    append_history_entry(entry, history_file=history_file)

    assert (tmp_path / 'history.json.bak').exists()
    assert json.loads(history_file.read_text(encoding='utf-8')) == [entry]


def test_build_history_image_url_stays_within_history_dir(tmp_path):
    history_dir = tmp_path / 'history'
    history_dir.mkdir()

    image_file = history_dir / 'nested' / 'alert test.jpg'
    image_file.parent.mkdir()
    image_file.write_bytes(b'img')

    assert build_history_image_url('nested/alert test.jpg', history_dir) == '/history/nested/alert%20test.jpg'
    assert build_history_image_url(str(image_file), history_dir) == '/history/nested/alert%20test.jpg'
    assert resolve_history_image_path('../outside.jpg', history_dir) is None
    assert build_history_image_url('../outside.jpg', history_dir) == ''


def test_parse_history_timestamp_accepts_supported_formats():
    assert parse_history_timestamp('2026-03-15 12:00:00') is not None
    assert parse_history_timestamp('2026-03-15T12:00:00') is not None
    assert parse_history_timestamp('15.03.2026 12:00:00') is None


def test_measurement_history_stores_relative_posix_image_path(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    controller._save_alert_to_history('session-1', frame, email_sent=False)

    history_file = tmp_path / 'history.json'
    assert history_file.exists()

    entries = json.loads(history_file.read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['type'] == 'alert'
    assert entry['email_sent'] is False
    assert entry['image_path']
    assert '\\' not in entry['image_path']
    assert '/' not in entry['image_path']
    assert not entry['image_path'].startswith(str(tmp_path))
    assert (tmp_path / entry['image_path']).exists()

    controller.cleanup()


def test_measurement_history_sanitizes_session_id_for_alert_image_filename(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    session_id = 'foo/../../../bar'

    controller._save_alert_to_history(session_id, frame, email_sent=False)

    entries = json.loads((tmp_path / 'history.json').read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['session_id'] == session_id
    assert entry['image_path'].endswith('_foo_bar.jpg')
    assert '/' not in entry['image_path']
    assert '\\' not in entry['image_path']

    saved_images = list(tmp_path.glob('*.jpg'))
    assert len(saved_images) == 1
    assert saved_images[0].parent == tmp_path
    assert saved_images[0].name == entry['image_path']

    controller.cleanup()


def test_measurement_history_sanitizes_windows_style_session_id_for_alert_image_filename(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    session_id = r'foo\..\..\..\bar'

    controller._save_alert_to_history(session_id, frame, email_sent=False)

    entries = json.loads((tmp_path / 'history.json').read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['session_id'] == session_id
    assert entry['image_path'].endswith('_foo_bar.jpg')
    assert '/' not in entry['image_path']
    assert '\\' not in entry['image_path']

    saved_images = list(tmp_path.glob('*.jpg'))
    assert len(saved_images) == 1
    assert saved_images[0].parent == tmp_path
    assert saved_images[0].name == entry['image_path']

    controller.cleanup()


def test_measurement_history_uses_fallback_when_session_id_sanitizes_to_empty(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    session_id = r'...///\\:::'

    controller._save_alert_to_history(session_id, frame, email_sent=False)

    entries = json.loads((tmp_path / 'history.json').read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['session_id'] == session_id
    assert entry['image_path'].endswith('_session.jpg')
    assert '/' not in entry['image_path']
    assert '\\' not in entry['image_path']
    assert (tmp_path / entry['image_path']).exists()

    controller.cleanup()


def test_measurement_history_uses_configured_png_format(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)
    cfg.measurement.image_format = 'png'

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    controller._save_alert_to_history('session-png', frame, email_sent=False)

    entries = json.loads((tmp_path / 'history.json').read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['image_path'].endswith('.png')
    saved_file = tmp_path / entry['image_path']
    assert saved_file.exists()
    assert saved_file.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')

    controller.cleanup()


def test_trigger_alert_sync_writes_history_without_email_system(tmp_path):
    cfg = _create_default_config()
    cfg.measurement.history_path = str(tmp_path)

    controller = MeasurementController(cfg.measurement, email_system=None, camera=None)
    with controller.session_lock:
        controller.is_session_active = True
        controller.session_id = 'session-2'
        controller._reset_alert_tracking_locked()
        alert_generation = controller._alert_generation
        controller._alert_dispatch_in_progress = True
        controller._alert_dispatch_generation = alert_generation

    assert controller.trigger_alert_sync('session-2', alert_generation) is False

    history_file = tmp_path / 'history.json'
    assert history_file.exists()

    entries = json.loads(history_file.read_text(encoding='utf-8'))
    assert len(entries) == 1

    entry = entries[0]
    assert entry['type'] == 'alert'
    assert entry['session_id'] == 'session-2'
    assert entry['email_sent'] is False
    assert entry['image_path'] == ''

    controller.cleanup()
