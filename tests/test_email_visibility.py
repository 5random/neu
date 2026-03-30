from src.config import _create_default_config
from src.gui import email_visibility


class _PlainEmailStub:
    def __init__(self) -> None:
        self.groups = {
            "ops": ["a@example.com"],
            "__static__": ["hidden@example.com"],
        }
        self.active_groups = ["__static__", "ops", "ops", "missing"]


class _GetterEmailStub:
    def get_visible_groups(self):
        return {
            "lab": ["b@example.com"],
            "__static__": ["hidden@example.com"],
        }

    def get_visible_active_groups(self):
        return ["__static__", "lab", "lab"]


class _MalformedEmailStub:
    groups = ["not-a-mapping"]
    active_groups = "ops"


def test_visible_groups_hide_reserved_group_for_email_config() -> None:
    email = _create_default_config().email
    email.groups = {
        "ops": ["a@example.com"],
        email.SYSTEM_STATIC_GROUP: ["hidden@example.com"],
    }
    email.active_groups = ["ops", email.SYSTEM_STATIC_GROUP]

    assert email_visibility.get_visible_groups(email) == {"ops": ["a@example.com"]}
    assert email_visibility.get_visible_group_names(email) == ["ops"]
    assert email_visibility.get_visible_active_groups(email) == ["ops"]


def test_visible_groups_hide_reserved_group_for_plain_email_like_object() -> None:
    email = _PlainEmailStub()

    assert email_visibility.get_visible_groups(email) == {"ops": ["a@example.com"]}
    assert email_visibility.get_visible_group_names(email) == ["ops"]
    assert email_visibility.get_visible_active_groups(email) == ["ops"]


def test_visible_group_getters_are_filtered_defensively() -> None:
    email = _GetterEmailStub()

    assert email_visibility.get_visible_groups(email) == {"lab": ["b@example.com"]}
    assert email_visibility.get_visible_active_groups(email) == ["lab"]


def test_visible_group_helpers_handle_malformed_fallback_data() -> None:
    email = _MalformedEmailStub()

    assert email_visibility.get_visible_groups(email) == {}
    assert email_visibility.get_visible_group_names(email) == []
    assert email_visibility.get_visible_active_groups(email) == []
