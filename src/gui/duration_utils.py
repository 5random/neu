from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DURATION_UNIT_SECONDS = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0}
DURATION_UNIT_OPTIONS = {
    "s": "Seconds",
    "min": "Minutes",
    "h": "Hours",
    "d": "Days",
}
DURATION_UNIT_SUFFIXES = {"s": "s", "min": "min", "h": "h", "d": "d"}
DURATION_UNIT_STEP = {"s": 1.0, "min": 0.1, "h": 0.01, "d": 0.001}
DURATION_UNIT_PRECISION = {"s": 0, "min": 1, "h": 2, "d": 3}
DEFAULT_DURATION_SECONDS = 60
MIN_DURATION_SECONDS = 1


@dataclass(frozen=True)
class DurationDisplayConfig:
    unit: str
    min_value: float
    max_value: float
    step: float
    suffix: str
    format: str
    display_value: float


def _resolve_allowed_units(allowed_units: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if not allowed_units:
        return tuple(DURATION_UNIT_SECONDS.keys())
    normalized = tuple(unit for unit in allowed_units if unit in DURATION_UNIT_SECONDS)
    return normalized or tuple(DURATION_UNIT_SECONDS.keys())


def normalize_duration_unit(
    unit: Any,
    *,
    allowed_units: tuple[str, ...] | list[str] | None = None,
    default: str = "min",
) -> str:
    allowed = _resolve_allowed_units(allowed_units)
    candidate = str(unit) if unit in DURATION_UNIT_SECONDS else default
    if candidate in allowed:
        return candidate
    if default in allowed:
        return default
    return allowed[0]


def get_duration_step(unit: str, *, allowed_units: tuple[str, ...] | list[str] | None = None) -> float:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    return DURATION_UNIT_STEP[normalized_unit]


def get_duration_precision(unit: str, *, allowed_units: tuple[str, ...] | list[str] | None = None) -> int:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    return DURATION_UNIT_PRECISION[normalized_unit]


def get_duration_format(unit: str, *, allowed_units: tuple[str, ...] | list[str] | None = None) -> str:
    precision = get_duration_precision(unit, allowed_units=allowed_units)
    return f"%.{precision}f"


def round_duration_value(
    value: float,
    unit: str,
    *,
    allowed_units: tuple[str, ...] | list[str] | None = None,
) -> float:
    precision = get_duration_precision(unit, allowed_units=allowed_units)
    return round(float(value), precision)


def get_duration_min_value(
    unit: str,
    *,
    min_seconds: float = MIN_DURATION_SECONDS,
    allowed_units: tuple[str, ...] | list[str] | None = None,
    allow_zero: bool = False,
) -> float:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    if allow_zero and min_seconds <= 0:
        return 0.0
    return max(
        float(min_seconds) / DURATION_UNIT_SECONDS[normalized_unit],
        get_duration_step(normalized_unit, allowed_units=allowed_units),
    )


def get_duration_max_value(
    unit: str,
    *,
    max_seconds: float,
    allowed_units: tuple[str, ...] | list[str] | None = None,
) -> float:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    return round_duration_value(
        float(max_seconds) / DURATION_UNIT_SECONDS[normalized_unit],
        normalized_unit,
        allowed_units=allowed_units,
    )


def seconds_to_duration_value(
    total_seconds: float,
    unit: str,
    *,
    allowed_units: tuple[str, ...] | list[str] | None = None,
) -> float:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    value = max(0.0, float(total_seconds or 0.0)) / DURATION_UNIT_SECONDS[normalized_unit]
    return round_duration_value(value, normalized_unit, allowed_units=allowed_units)


def build_duration_display_config(
    total_seconds: float,
    unit: str,
    *,
    min_seconds: float = MIN_DURATION_SECONDS,
    max_seconds: float,
    allowed_units: tuple[str, ...] | list[str] | None = None,
    allow_zero: bool = False,
) -> DurationDisplayConfig:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    display_value = seconds_to_duration_value(
        total_seconds,
        normalized_unit,
        allowed_units=allowed_units,
    )
    if allow_zero and float(total_seconds or 0.0) <= 0:
        display_value = 0.0

    return DurationDisplayConfig(
        unit=normalized_unit,
        min_value=get_duration_min_value(
            normalized_unit,
            min_seconds=min_seconds,
            allowed_units=allowed_units,
            allow_zero=allow_zero,
        ),
        max_value=get_duration_max_value(
            normalized_unit,
            max_seconds=max_seconds,
            allowed_units=allowed_units,
        ),
        step=get_duration_step(normalized_unit, allowed_units=allowed_units),
        suffix=DURATION_UNIT_SUFFIXES[normalized_unit],
        format=get_duration_format(normalized_unit, allowed_units=allowed_units),
        display_value=display_value,
    )


def pick_duration_unit(
    total_seconds: float,
    *,
    allowed_units: tuple[str, ...] | list[str] | None = None,
    default: str = "min",
) -> str:
    normalized_default = normalize_duration_unit(default, allowed_units=allowed_units)
    if total_seconds <= 0:
        return normalized_default
    allowed = _resolve_allowed_units(allowed_units)
    for unit in ("d", "h", "min", "s"):
        if unit not in allowed:
            continue
        divisor = DURATION_UNIT_SECONDS[unit]
        if float(total_seconds) % divisor == 0:
            return unit
    return normalized_default if normalized_default in allowed else allowed[-1]


def coerce_duration_value(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def duration_value_to_seconds(
    value: Any,
    unit: str,
    *,
    minimum_seconds: float = MIN_DURATION_SECONDS,
    allowed_units: tuple[str, ...] | list[str] | None = None,
    allow_zero: bool = False,
) -> float:
    normalized_unit = normalize_duration_unit(unit, allowed_units=allowed_units)
    normalized_value = coerce_duration_value(value, default=0.0)
    seconds = normalized_value * DURATION_UNIT_SECONDS[normalized_unit]
    if allow_zero and seconds <= 0:
        return 0.0
    return max(float(minimum_seconds), seconds)
