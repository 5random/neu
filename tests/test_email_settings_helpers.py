from types import SimpleNamespace

from src.config import _create_default_config
from src.gui.settings_elements import email_settings, measurement_settings


def _email_cfg():
    cfg = _create_default_config()
    email = cfg.email
    email.recipients = []
    email.groups = {}
    email.active_groups = []
    email.static_recipients = []
    email.explicit_targeting = False
    email.group_prefs = {}
    email.recipient_prefs = {}
    return email


def test_finalize_structural_email_config_materializes_legacy_targets() -> None:
    email = _email_cfg()
    email.recipients = ["a@example.com", "b@example.com"]

    email_settings._finalize_structural_email_config(email)

    assert email.explicit_targeting is True
    assert email.static_recipients == ["a@example.com", "b@example.com"]
    assert email.recipient_prefs == {
        "a@example.com": {"on_start": True, "on_end": True, "on_stop": True},
        "b@example.com": {"on_start": True, "on_end": True, "on_stop": True},
    }


def test_finalize_structural_email_config_drops_reserved_group_name() -> None:
    email = _email_cfg()
    email.groups = {
        "ops": ["a@example.com"],
        email.SYSTEM_STATIC_GROUP: ["hidden@example.com"],
    }
    email.active_groups = ["ops", email.SYSTEM_STATIC_GROUP]
    email.group_prefs = {
        "ops": {"on_start": True},
        email.SYSTEM_STATIC_GROUP: {"on_start": False},
    }

    email_settings._finalize_structural_email_config(email)

    assert email.groups == {"ops": ["a@example.com"]}
    assert email.active_groups == ["ops"]
    assert email.group_prefs == {"ops": {"on_start": True, "on_end": True, "on_stop": True}}


def test_rename_group_routing_refs_moves_active_groups_and_prefs() -> None:
    email = _email_cfg()
    email.groups = {"ops": ["a@example.com"], "lab": ["b@example.com"]}
    email.active_groups = ["ops", "lab"]
    email.group_prefs = {"ops": {"on_start": False}, "lab": {"on_start": True}}

    email_settings._rename_group_routing_refs(email, "ops", "night")

    assert email.active_groups == ["night", "lab"]
    assert email.group_prefs == {"night": {"on_start": False}, "lab": {"on_start": True}}


def test_delete_group_routing_refs_removes_group_everywhere() -> None:
    email = _email_cfg()
    email.groups = {"ops": ["a@example.com"]}
    email.active_groups = ["ops"]
    email.group_prefs = {"ops": {"on_start": True}}

    email_settings._delete_group_routing_refs(email, "ops")

    assert email.active_groups == []
    assert email.group_prefs == {}


def test_rename_recipient_routing_refs_moves_static_and_prefs() -> None:
    email = _email_cfg()
    email.static_recipients = ["a@example.com"]
    email.recipient_prefs = {"a@example.com": {"on_stop": False}}

    email_settings._rename_recipient_routing_refs(email, "a@example.com", "renamed@example.com")

    assert email.static_recipients == ["renamed@example.com"]
    assert email.recipient_prefs == {"renamed@example.com": {"on_stop": False}}


def test_delete_recipient_routing_refs_removes_static_and_prefs() -> None:
    email = _email_cfg()
    email.static_recipients = ["a@example.com", "b@example.com"]
    email.recipient_prefs = {
        "a@example.com": {"on_start": True},
        "b@example.com": {"on_start": False},
    }

    email_settings._delete_recipient_routing_refs(email, ["b@example.com"])

    assert email.static_recipients == ["a@example.com"]
    assert email.recipient_prefs == {"a@example.com": {"on_start": True}}


def test_email_stepper_back_tooltips_exist() -> None:
    assert email_settings.EMAIL_TOOLTIP_TEXTS["group_back_select"]
    assert email_settings.EMAIL_TOOLTIP_TEXTS["group_back_review"]


def test_measurement_apply_tooltip_exists() -> None:
    assert measurement_settings.NOTIFICATION_TOOLTIPS["apply"]


def test_resolve_group_editor_state_loads_existing_group_for_stepper() -> None:
    state, step = email_settings._resolve_group_editor_state(
        {"ops": ["a@example.com", "invalid"]},
        {"ops": {"on_start": False}},
        "ops",
    )

    assert step == "members"
    assert state == {
        "selected": "ops",
        "name": "ops",
        "members": ["a@example.com"],
        "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
    }


def test_resolve_group_editor_state_resets_when_group_is_missing() -> None:
    state, step = email_settings._resolve_group_editor_state(
        {"ops": ["a@example.com"]},
        {"ops": {"on_start": False}},
        "missing",
    )

    assert step == "select"
    assert state == email_settings._default_group_editor_state()


def test_event_model_value_reads_select_payload_from_args() -> None:
    assert email_settings._event_model_value(SimpleNamespace(args="ops")) == "ops"
    assert email_settings._event_model_value(SimpleNamespace(args={"value": "ops"})) == "ops"


def test_validate_group_name_rejects_duplicate_for_new_group_in_step_one() -> None:
    message = email_settings._validate_group_name(
        "ops",
        {"ops": ["a@example.com"]},
        selected_name=None,
    )

    assert message == "Group name already exists"


def test_validate_group_name_allows_loaded_existing_group_name() -> None:
    message = email_settings._validate_group_name(
        "ops",
        {"ops": ["a@example.com"]},
        selected_name="ops",
    )

    assert message is None
