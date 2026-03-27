"""Tests for group email sanitization in the email card UI helpers.

These tests verify that invalid email addresses are not stored in groups
and duplicates are removed while preserving order.
"""
from src.config import _create_default_config
from src.gui.settings_elements.email_settings import (
    SUPPORTED_TEMPLATE_PLACEHOLDERS,
    extract_rename_addresses,
    get_template_overview,
    sanitize_group_addresses,
    sanitize_groups_dict,
)


def test_sanitize_group_addresses_filters_invalid_and_dupes():
    emails = [
        "valid@example.com",
        "invalid@",
        " valid@example.com ",  # duplicate with spaces
        "also.valid.tag@domain.org",
        "no-at-symbol",
        "also.valid.tag@domain.org",  # duplicate
    ]
    cleaned = sanitize_group_addresses(emails)
    assert cleaned == [
        "valid@example.com",
        "also.valid.tag@domain.org",
    ]


def test_sanitize_groups_dict_applies_to_all_groups():
    groups = {
        "A": ["a@x.io", "bad", "a@x.io"],
        "B": [" good@yy.de ", "also.bad@", "ok@ok.ok"],
    }
    clean = sanitize_groups_dict(groups)
    assert clean == {
        "A": ["a@x.io"],
        "B": ["good@yy.de", "ok@ok.ok"],
    }


def test_extract_rename_addresses_accepts_dict_and_sequence_payloads():
    assert extract_rename_addresses({"oldAddress": "old@example.com", "newAddress": "new@example.com"}) == (
        "old@example.com",
        "new@example.com",
    )
    assert extract_rename_addresses(["old@example.com", "new@example.com"]) == (
        "old@example.com",
        "new@example.com",
    )


def test_get_template_overview_returns_all_effective_templates():
    overview = get_template_overview(_create_default_config().email)

    assert [entry["key"] for entry in overview] == [
        "alert",
        "test",
        "measurement_start",
        "measurement_end",
        "measurement_stop",
    ]
    assert all(entry["subject"] for entry in overview)
    assert all(entry["body"] for entry in overview)
    assert "{timestamp}" in SUPPORTED_TEMPLATE_PLACEHOLDERS
