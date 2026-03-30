from pathlib import Path

from src.gui.default_elements.history_card import _build_history_rows


def test_build_history_rows_sorts_descending_and_limits_to_latest_five() -> None:
    entries = [
        {'timestamp': '2026-03-27 10:00:01', 'session_id': 's1', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:07', 'session_id': 's7', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:03', 'session_id': 's3', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:06', 'session_id': 's6', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:02', 'session_id': 's2', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:05', 'session_id': 's5', 'type': 'alert', 'image_path': ''},
        {'timestamp': '2026-03-27 10:00:04', 'session_id': 's4', 'type': 'alert', 'image_path': ''},
        {'timestamp': 'invalid', 'session_id': 'broken', 'type': 'alert', 'image_path': ''},
    ]

    rows = _build_history_rows(entries, history_dir=Path.cwd(), max_entries=5)

    assert [row['session_id'] for row in rows] == ['s7', 's6', 's5', 's4', 's3']
    assert len(rows) == 5
    assert all(row['id'] for row in rows)
    assert all(row['image_url'] == '' for row in rows)


def test_build_history_rows_respects_zero_limit() -> None:
    entries = [
        {'timestamp': '2026-03-27 10:00:01', 'session_id': 's1', 'type': 'alert', 'image_path': ''},
    ]

    rows = _build_history_rows(entries, history_dir=Path.cwd(), max_entries=0)

    assert rows == []


def test_build_history_rows_generate_stable_ids_across_refreshes() -> None:
    entries = [
        {'timestamp': '2026-03-27 10:00:01', 'session_id': 's1', 'type': 'alert', 'image_path': 'alert_a.jpg', 'email_sent': True},
        {'timestamp': '2026-03-27 10:00:01', 'session_id': 's1', 'type': 'alert', 'image_path': 'alert_a.jpg', 'email_sent': True},
        {'timestamp': '2026-03-27 10:00:02', 'session_id': 's2', 'type': 'alert', 'image_path': 'alert_b.jpg', 'email_sent': False},
    ]

    first_rows = _build_history_rows(entries, history_dir=Path.cwd(), max_entries=5)
    second_rows = _build_history_rows(entries, history_dir=Path.cwd(), max_entries=5)

    assert [row['id'] for row in first_rows] == [row['id'] for row in second_rows]
    assert len({row['id'] for row in first_rows}) == 3
