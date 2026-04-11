from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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


class _DummyUIElement:
    def __init__(self, owner: "_FakeMeasurementUI", *, text: str | None = None, value=None, icon: str | None = None) -> None:
        self.owner = owner
        self.text = text
        self.value = value
        self.icon = icon
        self.visible = True
        self.enabled = True
        self._deleted = False
        self._props: dict[str, object] = {}
        self._events: dict[str, object] = {}
        self.min = None
        self.suffix = None

    def __enter__(self) -> "_DummyUIElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, *_args, **_kwargs) -> "_DummyUIElement":
        return self

    def style(self, *_args, **_kwargs) -> "_DummyUIElement":
        return self

    def props(self, *args, **_kwargs) -> "_DummyUIElement":
        if args:
            self.owner.props_calls.append(str(args[0]))
        return self

    def tooltip(self, *_args, **_kwargs) -> "_DummyUIElement":
        return self

    def on(self, event: str, handler) -> "_DummyUIElement":
        self._events[event] = handler
        return self

    def trigger(self, event: str, *args):
        handler = self._events.get(event)
        if callable(handler):
            return handler(*args)
        return None

    def update(self) -> "_DummyUIElement":
        return self

    def enable(self) -> "_DummyUIElement":
        self.enabled = True
        return self

    def disable(self) -> "_DummyUIElement":
        self.enabled = False
        return self

    def clear(self) -> None:
        return None

    def delete(self) -> None:
        self._deleted = True

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None


class _DummyTimer:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeMeasurementUI:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.buttons: list[_DummyUIElement] = []
        self.checkboxes: list[_DummyUIElement] = []
        self.numbers: list[_DummyUIElement] = []
        self.selects: list[_DummyUIElement] = []
        self.props_calls: list[str] = []
        self.timers: list[_DummyTimer] = []
        self.navigate = SimpleNamespace(to=lambda _path: None)

    def card(self) -> _DummyUIElement:
        return _DummyUIElement(self)

    def row(self) -> _DummyUIElement:
        return _DummyUIElement(self)

    def column(self) -> _DummyUIElement:
        return _DummyUIElement(self)

    def grid(self, **_kwargs) -> _DummyUIElement:
        return _DummyUIElement(self)

    def element(self, *_args, **_kwargs) -> _DummyUIElement:
        return _DummyUIElement(self)

    def dialog(self) -> _DummyUIElement:
        return _DummyUIElement(self)

    def button(self, *args, **kwargs) -> _DummyUIElement:
        element = _DummyUIElement(
            self,
            text=args[0] if args and isinstance(args[0], str) else None,
            icon=kwargs.get('icon'),
        )
        on_click = kwargs.get('on_click')
        if callable(on_click):
            element.on('click', on_click)
        self.buttons.append(element)
        return element

    def checkbox(self, _text: str, value=False) -> _DummyUIElement:
        element = _DummyUIElement(self, value=value)
        self.checkboxes.append(element)
        return element

    def number(self, *, value=0, min=None, step=None) -> _DummyUIElement:
        element = _DummyUIElement(self, value=value)
        element.min = min
        element._props['step'] = step
        self.numbers.append(element)
        return element

    def select(self, *, options=None, value=None, multiple=False, label=None) -> _DummyUIElement:
        element = _DummyUIElement(self, value=value)
        element.options = options or []
        element.multiple = multiple
        element.label = label
        self.selects.append(element)
        return element

    def label(self, text: str) -> _DummyUIElement:
        self.labels.append(text)
        return _DummyUIElement(self, text=text)

    def icon(self, name: str) -> _DummyUIElement:
        return _DummyUIElement(self, icon=name)

    def linear_progress(self, **_kwargs) -> _DummyUIElement:
        return _DummyUIElement(self, value=0.0)

    def separator(self) -> _DummyUIElement:
        return _DummyUIElement(self)

    def tooltip(self, text: str) -> _DummyUIElement:
        return _DummyUIElement(self, text=text)

    def timer(self, _interval: float, callback, **_kwargs) -> _DummyTimer:
        timer = _DummyTimer(callback)
        self.timers.append(timer)
        return timer


class _FakeLogger:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, tuple[object, ...]]] = []

    def warning(self, message: str, *args) -> None:
        self.warning_calls.append((message, args))

    def info(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None

    def exception(self, *_args, **_kwargs) -> None:
        return None


def test_measurement_card_without_controller_stays_read_only_and_builds_no_local_runtime(monkeypatch) -> None:
    fake_ui = _FakeMeasurementUI()
    fake_logger = _FakeLogger()
    notifications: list[tuple[str, str | None]] = []

    class _UnexpectedEmailSystem:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError('create_measurement_card must not instantiate a local email system')

    class _MeasurementConfig:
        session_timeout_seconds = 0
        session_timeout_minutes = 0
        max_alerts_per_session = 3

        def get_session_timeout_seconds(self) -> int:
            return 0

        def set_session_timeout_seconds(self, seconds: int) -> None:
            self.session_timeout_seconds = seconds

    config = SimpleNamespace(
        measurement=_MeasurementConfig(),
        email=SimpleNamespace(),
    )

    monkeypatch.setattr(measurementcard, 'ui', fake_ui)
    monkeypatch.setattr(measurementcard, 'logger', fake_logger)
    monkeypatch.setattr(measurementcard, 'EMailSystem', _UnexpectedEmailSystem)
    monkeypatch.setattr(measurementcard, 'create_heading_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(measurementcard, 'get_global_config', lambda: config)
    monkeypatch.setattr(measurementcard, 'notify_user', lambda message, kind=None: notifications.append((message, kind)))

    measurementcard.create_measurement_card(
        measurement_controller=None,
        camera=None,
        show_recipients=False,
    )

    assert measurementcard._measurement_controller_notice_text() in fake_ui.labels
    assert any('without measurement controller' in message for message, _args in fake_logger.warning_calls)

    start_button = next(
        button for button in fake_ui.buttons
        if button.icon == 'play_arrow'
    )
    assert start_button.enabled is False

    start_button.trigger('click', None)

    assert notifications == [('Measurement controller unavailable', 'negative')]
    assert len(fake_ui.timers) == 1

    fake_ui.timers[0].callback()

    assert fake_ui.timers[0].cancelled is False


def test_measurement_card_debounces_duration_persistence_until_blur(monkeypatch) -> None:
    fake_ui = _FakeMeasurementUI()
    fake_logger = _FakeLogger()
    save_calls: list[str] = []
    update_calls: list[int] = []

    class _MeasurementConfig:
        session_timeout_seconds = 300
        session_timeout_minutes = 5
        max_alerts_per_session = 3

        def get_session_timeout_seconds(self) -> int:
            return self.session_timeout_seconds

        def set_session_timeout_seconds(self, seconds: int) -> None:
            self.session_timeout_seconds = seconds
            self.session_timeout_minutes = (seconds + 59) // 60

    config = SimpleNamespace(
        measurement=_MeasurementConfig(),
        email=SimpleNamespace(),
    )
    controller = SimpleNamespace(
        config=config.measurement,
        get_session_status=lambda: {
            'is_active': False,
            'session_id': None,
            'session_start_time': None,
            'duration': None,
            'alert_triggered': False,
            'session_timeout_seconds': config.measurement.get_session_timeout_seconds(),
            'session_timeout_minutes': config.measurement.session_timeout_minutes,
            'recent_motion_detected': False,
            'time_since_motion': 0.0,
            'alert_countdown': None,
            'alerts_sent_count': 0,
            'max_alerts_per_session': 3,
            'cooldown_remaining': None,
            'can_send_alert': False,
        },
        update_config=lambda cfg: update_calls.append(cfg.get_session_timeout_seconds()),
        start_session=lambda: False,
        stop_session=lambda reason='manual': False,
        reset_alert_count=lambda: False,
        decrement_alert_count=lambda amount=1: False,
    )

    monkeypatch.setattr(measurementcard, 'ui', fake_ui)
    monkeypatch.setattr(measurementcard, 'logger', fake_logger)
    monkeypatch.setattr(measurementcard, 'create_heading_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(measurementcard, 'get_global_config', lambda: config)
    monkeypatch.setattr(measurementcard, 'save_global_config', lambda: save_calls.append('save') or True)
    monkeypatch.setattr(measurementcard, 'notify_user', lambda *_args, **_kwargs: None)

    measurementcard.create_measurement_card(
        measurement_controller=controller,
        camera=None,
        show_recipients=False,
    )

    duration_input = fake_ui.numbers[0]
    enable_limit = fake_ui.checkboxes[0]
    initial_timer_count = len(fake_ui.timers)
    assert enable_limit.value is True

    duration_input.value = 7
    duration_input.trigger('update:model-value', None)

    assert save_calls == []
    assert len(fake_ui.timers) == initial_timer_count + 1

    debounce_timer = fake_ui.timers[-1]
    duration_input.trigger('blur', None)

    assert debounce_timer.cancelled is True
    assert save_calls == ['save']
    assert update_calls == [420]


def test_sync_measurement_controller_email_system_keeps_existing_when_argument_is_omitted() -> None:
    controller = type("Controller", (), {"email_system": "existing"})()

    measurementcard._sync_measurement_controller_email_system(
        controller,
        None,
        provided=False,
    )

    assert controller.email_system == "existing"


def test_sync_measurement_controller_email_system_updates_when_explicit_system_is_provided() -> None:
    controller = type("Controller", (), {"email_system": "existing"})()

    measurementcard._sync_measurement_controller_email_system(
        controller,
        "replacement",
        provided=True,
    )

    assert controller.email_system == "replacement"


def test_sync_measurement_controller_email_system_allows_explicit_none() -> None:
    controller = type("Controller", (), {"email_system": "existing"})()

    measurementcard._sync_measurement_controller_email_system(
        controller,
        None,
        provided=True,
    )

    assert controller.email_system is None


def test_resolve_email_system_input_treats_sentinel_as_omitted() -> None:
    provided, effective_email_system = measurementcard._resolve_email_system_input(
        measurementcard._UNSET_EMAIL_SYSTEM
    )

    assert provided is False
    assert effective_email_system is None


def test_persist_active_groups_selection_skips_refresh_when_email_system_is_omitted(monkeypatch) -> None:
    email_config = type(
        "EmailConfig",
        (),
        {
            "active_groups": [],
            "enable_explicit_targeting": lambda self, materialize_legacy_targets=False: None,
        },
    )()
    config = type("Config", (), {"email": email_config})()
    save_calls: list[str] = []

    monkeypatch.setattr(measurementcard, "get_global_config", lambda: config)
    monkeypatch.setattr(
        measurementcard,
        "save_global_config",
        lambda: save_calls.append("save") or True,
    )

    measurementcard._persist_active_groups_selection(["ops"], None)

    assert email_config.active_groups == ["ops"]
    assert save_calls == ["save"]


def test_persist_active_groups_selection_refreshes_explicit_email_system(monkeypatch) -> None:
    email_config = type(
        "EmailConfig",
        (),
        {
            "active_groups": [],
            "enable_explicit_targeting": lambda self, materialize_legacy_targets=False: None,
        },
    )()
    config = type("Config", (), {"email": email_config})()
    save_calls: list[str] = []
    refresh_calls: list[str] = []

    class _EmailSystem:
        def refresh_config(self) -> None:
            refresh_calls.append("refresh")

    monkeypatch.setattr(measurementcard, "get_global_config", lambda: config)
    monkeypatch.setattr(
        measurementcard,
        "save_global_config",
        lambda: save_calls.append("save") or True,
    )

    measurementcard._persist_active_groups_selection(["ops"], _EmailSystem())

    assert email_config.active_groups == ["ops"]
    assert save_calls == ["save"]
    assert refresh_calls == ["refresh"]


def test_persist_active_groups_selection_raises_when_config_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(measurementcard, "get_global_config", lambda: None)

    with pytest.raises(RuntimeError, match="Configuration not available"):
        measurementcard._persist_active_groups_selection(["ops"], None)
