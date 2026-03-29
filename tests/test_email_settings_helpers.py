import pytest
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


def test_snapshot_and_restore_email_config_state_rolls_back_mutations() -> None:
    email = _email_cfg()
    email.recipients = ["base@example.com"]
    email.groups = {"ops": ["a@example.com"]}
    email.active_groups = ["ops"]
    email.static_recipients = ["base@example.com"]
    email.group_prefs = {"ops": {"on_start": False}}
    email.notifications = {"on_start": True}

    snapshot = email_settings._snapshot_email_config_state(email)

    email.recipients.append("changed@example.com")
    email.groups["ops"].append("b@example.com")
    email.active_groups = []
    email.static_recipients = []
    email.group_prefs["ops"]["on_start"] = True
    email.notifications["on_start"] = False

    email_settings._restore_email_config_state(email, snapshot)

    assert email.recipients == ["base@example.com"]
    assert email.groups == {"ops": ["a@example.com"]}
    assert email.active_groups == ["ops"]
    assert email.static_recipients == ["base@example.com"]
    assert email.group_prefs == {"ops": {"on_start": False}}
    assert email.notifications == {"on_start": True}


def test_group_editor_snapshot_restore_restores_selected_and_draft_values() -> None:
    group_editor = {
        "selected": "ops",
        "name": "night",
        "members": ["a@example.com", "a@example.com", "invalid"],
        "event_prefs": {"on_start": False},
    }

    snapshot = email_settings._snapshot_group_editor_state(group_editor)

    group_editor["selected"] = "night"
    group_editor["name"] = "broken"
    group_editor["members"] = ["broken@example.com"]
    group_editor["event_prefs"] = {"on_stop": False}

    email_settings._restore_group_editor_state(group_editor, snapshot)

    assert group_editor == {
        "selected": "ops",
        "name": "night",
        "members": ["a@example.com"],
        "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
    }


@pytest.mark.parametrize(
    ("group_editor", "expected"),
    [
        (
            {
                "selected": "ops",
                "name": "ops",
                "members": ["a@example.com"],
                "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
            },
            False,
        ),
        (
            {
                "selected": "ops",
                "name": "night",
                "members": ["a@example.com"],
                "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
            },
            True,
        ),
        (
            {
                "selected": "ops",
                "name": "ops",
                "members": ["a@example.com", "b@example.com"],
                "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
            },
            True,
        ),
        (
            {
                "selected": "ops",
                "name": "ops",
                "members": ["a@example.com"],
                "event_prefs": {"on_start": True, "on_end": True, "on_stop": True},
            },
            True,
        ),
        (
            {
                "selected": None,
                "name": "",
                "members": [],
                "event_prefs": {"on_start": True, "on_end": True, "on_stop": True},
            },
            False,
        ),
        (
            {
                "selected": None,
                "name": "draft",
                "members": [],
                "event_prefs": {"on_start": True, "on_end": True, "on_stop": True},
            },
            True,
        ),
        (
            {
                "selected": None,
                "name": "",
                "members": ["a@example.com"],
                "event_prefs": {"on_start": True, "on_end": True, "on_stop": True},
            },
            True,
        ),
        (
            {
                "selected": None,
                "name": "",
                "members": [],
                "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
            },
            True,
        ),
    ],
)
def test_is_group_editor_dirty_detects_loaded_and_new_draft_changes(group_editor, expected) -> None:
    assert email_settings._is_group_editor_dirty(
        group_editor,
        {"ops": ["a@example.com"]},
        {"ops": {"on_start": False}},
    ) is expected


def test_resolve_group_delete_target_uses_loaded_group_when_draft_is_renamed() -> None:
    assert email_settings._resolve_group_delete_target(
        {
            "selected": "ops",
            "name": "night",
            "members": ["a@example.com"],
            "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
        },
        {"ops": ["a@example.com"], "night": ["b@example.com"]},
    ) == "ops"


def test_resolve_group_delete_target_returns_none_for_new_group_draft() -> None:
    assert email_settings._resolve_group_delete_target(
        {
            "selected": None,
            "name": "night",
            "members": ["a@example.com"],
            "event_prefs": {"on_start": False, "on_end": True, "on_stop": True},
        },
        {"ops": ["a@example.com"]},
    ) is None


def test_event_model_value_reads_select_payload_from_args() -> None:
    assert email_settings._event_model_value(SimpleNamespace(args="ops")) == "ops"
    assert email_settings._event_model_value(SimpleNamespace(args={"value": "ops"})) == "ops"


def test_event_model_value_unwraps_single_item_args_list() -> None:
    assert email_settings._event_model_value(SimpleNamespace(args=["ops"])) == "ops"


def test_event_model_value_falls_back_to_args_when_value_is_none() -> None:
    assert email_settings._event_model_value(SimpleNamespace(value=None, args="ops")) == "ops"


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
