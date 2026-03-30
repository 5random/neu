import json
import shutil
import uuid
from pathlib import Path

from src import alert_history
from src.alert_history import (
    append_history_entry,
    get_history_revision,
    register_history_listener,
    replace_history_entries,
    unregister_history_listener,
)


def _alert_entry(timestamp: str, session_id: str) -> dict[str, str]:
    return {
        'timestamp': timestamp,
        'session_id': session_id,
        'type': 'alert',
        'image_path': '',
    }


def test_history_revision_increments_on_append_and_replace(monkeypatch) -> None:
    history_file = Path('data/history/test_history_revision_a.json')
    revision_key = alert_history._history_revision_key(history_file)
    alert_history._history_revisions.pop(revision_key, None)

    monkeypatch.setattr(alert_history, '_load_history_entries_unlocked', lambda *args, **kwargs: [])
    monkeypatch.setattr(alert_history, '_write_history_entries_unlocked', lambda *args, **kwargs: None)

    initial_revision = get_history_revision(history_file=history_file)

    append_history_entry(_alert_entry('2026-03-27 12:00:00', 'session-1'), history_file=history_file)
    after_append = get_history_revision(history_file=history_file)
    replace_history_entries([], history_file=history_file)
    after_replace = get_history_revision(history_file=history_file)

    assert after_append == initial_revision + 1
    assert after_replace == after_append + 1


def test_history_revision_is_tracked_per_history_file(monkeypatch) -> None:
    history_file_a = Path('data/history/test_history_revision_a.json')
    history_file_b = Path('data/history/test_history_revision_b.json')
    alert_history._history_revisions.pop(alert_history._history_revision_key(history_file_a), None)
    alert_history._history_revisions.pop(alert_history._history_revision_key(history_file_b), None)

    monkeypatch.setattr(alert_history, '_load_history_entries_unlocked', lambda *args, **kwargs: [])
    monkeypatch.setattr(alert_history, '_write_history_entries_unlocked', lambda *args, **kwargs: None)

    append_history_entry(_alert_entry('2026-03-27 12:00:00', 'session-a'), history_file=history_file_a)

    assert get_history_revision(history_file=history_file_a) == 1
    assert get_history_revision(history_file=history_file_b) == 0


def test_history_listener_is_called_once_and_can_be_unregistered(monkeypatch) -> None:
    history_file = Path('data/history/test_history_listener.json')
    history_key = alert_history._history_revision_key(history_file)
    alert_history._history_revisions.pop(history_key, None)
    alert_history._history_listeners.pop(history_key, None)
    revisions: list[int] = []

    monkeypatch.setattr(alert_history, '_load_history_entries_unlocked', lambda *args, **kwargs: [])
    monkeypatch.setattr(alert_history, '_write_history_entries_unlocked', lambda *args, **kwargs: None)

    def listener(revision: int) -> None:
        revisions.append(revision)

    register_history_listener(listener, history_file=history_file)
    append_history_entry(_alert_entry('2026-03-27 12:00:00', 'session-a'), history_file=history_file)
    unregister_history_listener(listener, history_file=history_file)
    append_history_entry(_alert_entry('2026-03-27 12:01:00', 'session-b'), history_file=history_file)

    assert revisions == [1]


def test_append_history_entry_persists_pending_image_with_entry() -> None:
    temp_dir = Path(f'codex_test_alert_history_revision_{uuid.uuid4().hex}')
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_file = temp_dir / 'history.json'
        append_history_entry(
            _alert_entry('2026-03-27 12:00:00', 'session-image'),
            history_file=history_file,
            pending_image_filename='alert_test.jpg',
            pending_image_bytes=b'img-bytes',
        )

        stored_entries = json.loads(history_file.read_text(encoding='utf-8'))
        assert len(stored_entries) == 1
        assert stored_entries[0]['image_path'] == 'alert_test.jpg'
        assert (temp_dir / 'alert_test.jpg').read_bytes() == b'img-bytes'
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
