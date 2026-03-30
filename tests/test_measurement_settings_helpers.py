from src.gui.duration_utils import DurationDisplayConfig
from src.gui.settings_elements import measurement_settings


class _FakeNumberControl:
    def __init__(self) -> None:
        self.min = None
        self.max = None
        self.format = None
        self.value = None
        self.props_calls: list[tuple[str | None, str | None]] = []
        self.updated = 0

    def props(self, add: str | None = None, *, remove: str | None = None):
        self.props_calls.append((add, remove))
        return self

    def update(self) -> None:
        self.updated += 1


def test_apply_duration_control_display_uses_public_number_api_only() -> None:
    number_ctrl = _FakeNumberControl()
    display = DurationDisplayConfig(
        unit="min",
        min_value=0.5,
        max_value=60.0,
        step=0.1,
        suffix="min",
        format="%.1f",
        display_value=1.5,
    )

    measurement_settings._apply_duration_control_display(number_ctrl, display)

    assert number_ctrl.min == 0.5
    assert number_ctrl.max == 60.0
    assert number_ctrl.format == "%.1f"
    assert number_ctrl.value == 1.5
    assert number_ctrl.props_calls == [
        (None, "step suffix"),
        ('step=0.1 suffix="min"', None),
    ]
    assert number_ctrl.updated == 1
