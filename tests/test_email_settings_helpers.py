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
