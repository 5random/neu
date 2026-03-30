from src.gui import duration_utils


def test_pick_duration_unit_prefers_matching_larger_units() -> None:
    assert duration_utils.pick_duration_unit(3600, allowed_units=("s", "min", "h"), default="min") == "h"
    assert duration_utils.pick_duration_unit(120, allowed_units=("s", "min"), default="s") == "min"


def test_seconds_to_duration_value_respects_allowed_units() -> None:
    assert duration_utils.seconds_to_duration_value(90, "min", allowed_units=("s", "min")) == 1.5
    assert duration_utils.seconds_to_duration_value(30, "s", allowed_units=("s", "min")) == 30.0


def test_duration_value_to_seconds_supports_zero_for_optional_fields() -> None:
    assert (
        duration_utils.duration_value_to_seconds(
            0,
            "min",
            minimum_seconds=0,
            allowed_units=("min", "h"),
            allow_zero=True,
        )
        == 0.0
    )
    assert (
        duration_utils.duration_value_to_seconds(
            1.5,
            "min",
            minimum_seconds=30,
            allowed_units=("s", "min", "h"),
        )
        == 90.0
    )


def test_build_duration_display_config_exposes_public_number_settings() -> None:
    display = duration_utils.build_duration_display_config(
        90.0,
        "min",
        min_seconds=30.0,
        max_seconds=3600.0,
        allowed_units=("s", "min", "h"),
    )

    assert display.unit == "min"
    assert display.min_value == 0.5
    assert display.max_value == 60.0
    assert display.step == 0.1
    assert display.suffix == "min"
    assert display.format == "%.1f"
    assert display.display_value == 1.5


def test_build_duration_display_config_keeps_zero_for_optional_fields() -> None:
    display = duration_utils.build_duration_display_config(
        0.0,
        "h",
        min_seconds=0.0,
        max_seconds=3600.0,
        allowed_units=("min", "h"),
        allow_zero=True,
    )

    assert display.unit == "h"
    assert display.min_value == 0.0
    assert display.display_value == 0.0


def test_build_duration_display_config_recalculates_value_for_changed_unit() -> None:
    minutes = duration_utils.build_duration_display_config(
        3600.0,
        "min",
        min_seconds=30.0,
        max_seconds=7200.0,
        allowed_units=("s", "min", "h"),
    )
    hours = duration_utils.build_duration_display_config(
        3600.0,
        "h",
        min_seconds=30.0,
        max_seconds=7200.0,
        allowed_units=("s", "min", "h"),
    )

    assert minutes.display_value == 60.0
    assert hours.display_value == 1.0
