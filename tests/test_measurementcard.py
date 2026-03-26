from datetime import datetime, timedelta, timezone

from src.gui.default_elements import measurementcard


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = cls(2026, 3, 26, 12, 0, 0)
        if tz is None:
            return base
        return base.replace(tzinfo=tz)


def test_derive_elapsed_duration_uses_naive_now_for_naive_start_time(monkeypatch):
    monkeypatch.setattr(measurementcard, 'datetime', _FrozenDateTime)

    start_time = _FrozenDateTime(2026, 3, 26, 11, 59, 30)

    assert measurementcard._derive_elapsed_duration(start_time) == timedelta(seconds=30)


def test_derive_elapsed_duration_uses_matching_timezone_for_aware_start_time(monkeypatch):
    monkeypatch.setattr(measurementcard, 'datetime', _FrozenDateTime)

    tz = timezone(timedelta(hours=2))
    start_time = _FrozenDateTime(2026, 3, 26, 11, 59, 30, tzinfo=tz)

    assert measurementcard._derive_elapsed_duration(start_time) == timedelta(seconds=30)


def test_calculate_session_progress_ratio_returns_fraction_for_positive_window():
    elapsed = timedelta(seconds=15)
    session_max = timedelta(seconds=60)

    assert measurementcard._calculate_session_progress_ratio(elapsed, session_max) == 0.25


def test_calculate_session_progress_ratio_returns_zero_for_non_positive_window():
    elapsed = timedelta(seconds=15)
    session_max = timedelta(0)

    assert measurementcard._calculate_session_progress_ratio(elapsed, session_max) == 0.0
