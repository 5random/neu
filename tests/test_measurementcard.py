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


def test_resolve_active_groups_sync_tracks_clean_external_updates():
    selection, synced = measurementcard._resolve_active_groups_sync(
        ["ops"],
        ["ops"],
        ["lab"],
        valid_options=["ops", "lab"],
    )

    assert selection == ["lab"]
    assert synced == ["lab"]


def test_resolve_active_groups_sync_keeps_dirty_local_selection():
    selection, synced = measurementcard._resolve_active_groups_sync(
        ["ops"],
        ["lab"],
        ["lab"],
        valid_options=["ops", "lab"],
    )

    assert selection == ["ops"]
    assert synced == ["lab"]


def test_has_unsaved_active_group_changes_uses_synced_snapshot():
    assert measurementcard._has_unsaved_active_group_changes(
        ["ops"],
        ["lab"],
        valid_options=["ops", "lab"],
    )
    assert not measurementcard._has_unsaved_active_group_changes(
        ["lab"],
        ["lab"],
        valid_options=["ops", "lab"],
    )


def test_needs_active_groups_value_refresh_when_removed_group_is_still_selected():
    assert measurementcard._needs_active_groups_value_refresh(
        ["ops", "removed"],
        ["ops"],
    )
    assert not measurementcard._needs_active_groups_value_refresh(
        ["ops"],
        ["ops"],
    )


def test_measurement_card_tooltips_cover_active_group_quick_selector():
    assert measurementcard.MEASUREMENT_CARD_TOOLTIPS == {
        'active_groups': 'Quick selection of the recipient groups for the current measurement run. Static recipients are added automatically.',
        'active_groups_apply': 'Save the selected active groups for the current measurement run.',
        'active_groups_info': 'Static recipients always receive emails in addition to the active groups selected here.',
        'alert_counter': 'Shows how many alerts count against the current session limit.',
        'alert_counter_decrement': 'Decrease the current session alert counter by one without changing cooldown.',
        'alert_counter_reset': 'Reset the current session alert counter to zero without changing cooldown.',
        'alert_cooldown': 'Remaining time until another alert may be sent.',
    }


def test_derive_alert_counter_view_state_marks_ready_session() -> None:
    view = measurementcard._derive_alert_counter_view_state(
        {
            'is_active': True,
            'alerts_sent_count': 1,
            'max_alerts_per_session': 3,
            'cooldown_remaining': None,
            'can_send_alert': True,
        }
    )

    assert view['alerts_count_text'] == '1 / 3'
    assert view['cooldown_state'] == 'ready'
    assert view['show_decrement'] is True
    assert view['enable_reset'] is True


def test_derive_alert_counter_view_state_marks_limit_reached() -> None:
    view = measurementcard._derive_alert_counter_view_state(
        {
            'is_active': True,
            'alerts_sent_count': 3,
            'max_alerts_per_session': 3,
            'cooldown_remaining': 0,
            'can_send_alert': False,
        }
    )

    assert view['cooldown_state'] == 'limit'
    assert view['show_decrement'] is True
    assert view['show_reset'] is True


def test_derive_alert_counter_view_state_marks_running_cooldown() -> None:
    view = measurementcard._derive_alert_counter_view_state(
        {
            'is_active': True,
            'alerts_sent_count': 1,
            'max_alerts_per_session': 3,
            'cooldown_remaining': 12.5,
            'can_send_alert': False,
        }
    )

    assert view['cooldown_state'] == 'cooldown'
    assert view['cooldown_remaining'] == 12.5


def test_derive_alert_counter_view_state_hides_buttons_for_inactive_session() -> None:
    view = measurementcard._derive_alert_counter_view_state(
        {
            'is_active': False,
            'alerts_sent_count': 0,
            'max_alerts_per_session': 3,
            'cooldown_remaining': None,
            'can_send_alert': False,
        }
    )

    assert view['cooldown_state'] == 'idle'
    assert view['show_decrement'] is False
    assert view['show_reset'] is False
