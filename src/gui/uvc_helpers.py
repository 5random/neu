from __future__ import annotations

from typing import Any


def auto_exposure_value_is_auto(value: Any) -> bool:
    """Interpret OpenCV auto-exposure flags across common backends.

    Known values:
    - Windows / DirectShow: 0.75 = auto, 0.25 = manual
    - Linux / V4L2: 3 = auto, 1 = manual
    - Some drivers expose plain booleans / 0 / 1
    """
    if isinstance(value, bool):
        return value

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return bool(value)

    if abs(numeric_value - 3.0) < 1e-6:
        return True
    if abs(numeric_value - 1.0) < 1e-6:
        return False
    if abs(numeric_value - 0.75) < 1e-6:
        return True
    if abs(numeric_value - 0.25) < 1e-6:
        return False
    if abs(numeric_value) < 1e-6:
        return False

    return bool(value)


def set_nested_config_value(root: Any, path: str, value: Any) -> None:
    """Set a dotted config path and raise a descriptive error for invalid paths."""
    parts = path.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError(f"Invalid config path: {path!r}")

    target = root
    traversed: list[str] = []

    for part in parts[:-1]:
        traversed.append(part)
        if not hasattr(target, part):
            resolved_parent = ".".join(traversed[:-1]) or type(root).__name__
            raise AttributeError(
                f"Missing config field '{part}' under '{resolved_parent}' while resolving '{path}'"
            )
        target = getattr(target, part)
        if target is None:
            raise AttributeError(
                f"Config field '{'.'.join(traversed)}' is None while resolving '{path}'"
            )

    final_field = parts[-1]
    if not hasattr(target, final_field):
        resolved_parent = ".".join(parts[:-1]) or type(root).__name__
        raise AttributeError(
            f"Missing config field '{final_field}' under '{resolved_parent}' while resolving '{path}'"
        )

    setattr(target, final_field, value)
