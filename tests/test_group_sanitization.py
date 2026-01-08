"""Tests for group email sanitization in the email card UI helpers.

These tests verify that invalid email addresses are not stored in groups
and duplicates are removed while preserving order.
"""
from src.gui.settings_elements.email_settings import (
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
