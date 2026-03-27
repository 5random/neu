from src.config import _create_default_config


def _email_cfg():
    cfg = _create_default_config()
    email = cfg.email
    email.recipients = []
    email.groups = {}
    email.active_groups = []
    email.static_recipients = []
    email.explicit_targeting = False
    email.notifications = {"on_start": False, "on_end": False, "on_stop": False}
    email.group_prefs = {}
    email.recipient_prefs = {}
    return email


def test_get_target_recipients_uses_legacy_recipients_without_explicit_targeting() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]

    assert email.get_target_recipients() == ["a@example.com", "b@example.com"]


def test_get_target_recipients_returns_empty_with_explicit_targeting_and_no_targets() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]
    email.explicit_targeting = True

    assert email.get_target_recipients() == []


def test_get_measurement_event_recipients_respects_legacy_recipient_prefs() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]
    email.notifications["on_start"] = True
    email.recipient_prefs = {"b@example.com": {"on_start": False}}

    assert email.get_measurement_event_recipients("on_start") == ["a@example.com"]


def test_get_measurement_event_recipients_returns_empty_with_explicit_targeting_and_no_targets() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]
    email.notifications["on_start"] = True
    email.explicit_targeting = True

    assert email.get_measurement_event_recipients("on_start") == []


def test_get_target_recipients_unions_static_and_active_groups() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com", "c@example.com"]
    email.static_recipients = ["a@example.com", "c@example.com"]
    email.groups = {
        "ops": ["b@example.com", "c@example.com"],
        "lab": ["c@example.com"],
    }
    email.active_groups = ["ops", "lab"]

    assert email.get_target_recipients() == ["a@example.com", "c@example.com", "b@example.com"]


def test_get_measurement_event_recipients_uses_or_rule_across_static_and_groups() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com", "c@example.com"]
    email.notifications["on_start"] = True
    email.static_recipients = ["a@example.com", "c@example.com"]
    email.groups = {
        "ops": ["b@example.com", "c@example.com"],
        "lab": ["c@example.com"],
    }
    email.active_groups = ["ops", "lab"]
    email.group_prefs = {
        "ops": {"on_start": True},
        "lab": {"on_start": False},
    }
    email.recipient_prefs = {
        "a@example.com": {"on_start": True},
        "c@example.com": {"on_start": False},
    }

    assert email.get_measurement_event_recipients("on_start") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_get_measurement_event_recipients_returns_empty_when_no_source_allows_event() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]
    email.notifications["on_start"] = True
    email.static_recipients = ["a@example.com"]
    email.groups = {"ops": ["b@example.com"]}
    email.active_groups = ["ops"]
    email.group_prefs = {"ops": {"on_start": False}}
    email.recipient_prefs = {"a@example.com": {"on_start": False}}

    assert email.get_measurement_event_recipients("on_start") == []


def test_get_known_recipients_includes_group_and_pref_only_addresses() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com"]
    email.groups = {"ops": ["b@example.com"]}
    email.static_recipients = ["c@example.com"]
    email.recipient_prefs = {"d@example.com": {"on_start": True}}

    assert email.get_known_recipients() == [
        "a@example.com",
        "c@example.com",
        "b@example.com",
        "d@example.com",
    ]


def test_enable_explicit_targeting_materializes_legacy_targets_when_requested() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]

    email.enable_explicit_targeting(materialize_legacy_targets=True)

    assert email.explicit_targeting is True
    assert email.static_recipients == ["a@example.com", "b@example.com"]


def test_validate_rejects_unknown_group_prefs() -> None:
    email = _email_cfg()
    email.group_prefs = {"missing": {"on_start": True}}

    errors = email.validate()

    assert any("unknown group" in error for error in errors)


def test_validate_rejects_unknown_event_keys_in_notifications_and_prefs() -> None:
    email = _email_cfg()
    email.notifications = {"on_star": True}
    email.groups = {"ops": ["a@example.com"]}
    email.group_prefs = {"ops": {"on_start": True, "on_star": False}}
    email.recipient_prefs = {"a@example.com": {"on_star": False}}

    errors = email.validate()

    assert any("notifications contains unknown event key" in error for error in errors)
    assert any("group_prefs['ops'] contains unknown event key" in error for error in errors)
    assert any("recipient_prefs['a@example.com'] contains unknown event key" in error for error in errors)
