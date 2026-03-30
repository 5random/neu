from typing import Any, Callable, Optional

def bind_number_slider(
    number_ctrl: Any,
    slider_ctrl: Any,
    *,
    min_value: float,
    max_value: float,
    as_int: bool = False,
    on_change: Optional[Callable[[float | int], None]] = None,
    caster: Optional[Callable[[Any, float], Optional[float]]] = None,
    fallback_value: float = 0.0,
) -> None:
    """
    Binds a NiceGUI number input and a slider together with two-way synchronization.
    
    Args:
        number_ctrl: The ui.number element.
        slider_ctrl: The ui.slider element.
        min_value: Minimum allowed value.
        max_value: Maximum allowed value.
        as_int: If True, values are rounded to integers.
        on_change: Optional callback invoked when the value changes (committed).
        caster: Optional function to cast/validate input values. 
                Signature: (value, fallback) -> float | None.
                If None, a default float/int cast is used.
        fallback_value: Value used if casting fails (when using default caster).
    """
    
    syncing = {'active': False}

    def _default_caster(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return fallback

    def _fmt(value: float) -> float | int:
        return int(round(value)) if as_int else value

    def _set_control_value(ctrl: Any, value: Any) -> None:
        try:
            if hasattr(ctrl, 'set_value'):
                ctrl.set_value(value)
            else:
                setattr(ctrl, 'value', value)
                if hasattr(ctrl, 'update'):
                    ctrl.update()
        except Exception:
            pass

    def _clamp(value: Any) -> Optional[float]:
        if caster:
            v = caster(value, fallback_value)
        else:
            v = _default_caster(value, fallback_value)
            
        if v is None:
            return None
        v = max(min_value, v)
        v = min(max_value, v)
        return v

    def _from_slider(event: Any, commit: bool = False) -> None:
        # Avoid infinite loops
        if syncing['active']:
            return

        # Get value from event or control
        raw = getattr(event, 'value', getattr(slider_ctrl, 'value', min_value))
        
        v = _clamp(raw)
        if v is None:
            return

        syncing['active'] = True
        try:
            formatted_val = _fmt(v)
            _set_control_value(number_ctrl, formatted_val)
            if commit and on_change:
                on_change(formatted_val)
        finally:
            syncing['active'] = False

    def _from_number(event: Any, commit: bool = False) -> None:
        if syncing['active']:
            return

        # Get value from event or control
        # Note: ui.number events might be just the value or an event object
        raw = getattr(event, 'value', getattr(number_ctrl, 'value', min_value)) if event is not None else getattr(number_ctrl, 'value', min_value)
        
        v = _clamp(raw)
        if v is None:
            return

        syncing['active'] = True
        try:
            # Slider usually expects float/int, not formatted string
            # But we want to snap the slider to the formatted value if as_int is True
            slider_val = _fmt(v)
            _set_control_value(slider_ctrl, slider_val)
            
            if commit and on_change:
                on_change(slider_val)
        finally:
            syncing['active'] = False

    # Bind events
    # Slider: 'update:model-value' is live dragging, 'change' is release
    slider_ctrl.on('update:model-value', lambda e: _from_slider(e, commit=False))
    slider_ctrl.on('change', lambda e: _from_slider(e, commit=True))
    
    # Number: 'update:model-value' is typing, 'blur' is focus loss
    # We usually want to update slider while typing, but maybe only commit on blur?
    # Existing implementations differed. 
    # camera_settings: update:model-value -> commit=True
    # measurement_settings: update:model-value -> commit=True (via notify_change)
    
    number_ctrl.on('update:model-value', lambda e: _from_number(e, commit=True))
    number_ctrl.on('blur', lambda e: _from_number(e, commit=True))
