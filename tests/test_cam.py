import os
import platform
import pprint
import time

import cv2
import pytest


def _backend_name(api_pref: int) -> str:
    mapping = {
        getattr(cv2, "CAP_DSHOW", -1): "CAP_DSHOW",
        getattr(cv2, "CAP_MSMF", -1): "CAP_MSMF",
        getattr(cv2, "CAP_ANY", 0): "CAP_ANY",
        0: "CAP_ANY",
    }
    return mapping.get(api_pref, str(api_pref))


def _parse_indices_env() -> list[int]:
    s = os.getenv("TEST_CAMERA_INDICES", "0,1,2")
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out or [0]


def _backends_to_try() -> list[int]:
    is_windows = platform.system() == "Windows"
    dshow = getattr(cv2, "CAP_DSHOW", 700)
    msmf = getattr(cv2, "CAP_MSMF", 1400)
    cap_any = getattr(cv2, "CAP_ANY", 0)
    return [dshow, msmf, cap_any] if is_windows else [cap_any]


def _try_open(idx: int, api_pref: int) -> tuple[bool, dict | None, str]:
    """Attempt to open a camera and read basic properties.

    Returns (opened, props, message). Ensures release in all paths.
    """
    cap = None
    msg = ""
    try:
        cap = cv2.VideoCapture(idx, api_pref)
        if cap is None or not cap.isOpened():
            return False, None, f"open failed (idx={idx}, backend={_backend_name(api_pref)})"

        # Optional: set small buffer to reduce lag
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        # Warm-up a few reads; some drivers need this
        got = False
        for _ in range(15):
            ret, _frame = cap.read()
            if ret:
                got = True
                break
            time.sleep(0.03)

        props = {p: cap.get(p) for p in range(48)}
        if not got:
            return True, props, (
                f"opened but could not read a frame after warmup (idx={idx}, backend={_backend_name(api_pref)})"
            )
        return True, props, "ok"
    finally:
        # Always release if we opened something
        try:
            if cap is not None and cap.isOpened():
                cap.release()
        except Exception:
            pass


def test_camera_open_with_fallbacks(caplog):
    indices = _parse_indices_env()
    backends = _backends_to_try()

    attempts: list[str] = []
    success_tuple: tuple[int, int] | None = None
    opened_but_no_frame: list[str] = []
    last_props: dict | None = None

    for idx in indices:
        for be in backends:
            opened, props, message = _try_open(idx, be)
            attempts.append(f"idx={idx}, backend={_backend_name(be)} -> {message}")
            if opened and message == "ok":
                success_tuple = (idx, be)
                last_props = props
                break
            if opened and message != "ok":
                opened_but_no_frame.append(f"idx={idx}, backend={_backend_name(be)}")
        if success_tuple:
            break

    # If nothing opened cleanly, skip gracefully with detailed message
    if not success_tuple:
        details = " ; ".join(attempts)
        reason = (
            "No usable camera found. Attempts: " + details +
            (" ; opened but no frame: " + ", ".join(opened_but_no_frame) if opened_but_no_frame else "")
        )
        pytest.skip(reason)

    idx, be = success_tuple
    caplog.set_level("INFO")
    print(f"Opened camera idx={idx} backend={_backend_name(be)} successfully")
    if last_props is not None:
        # Pretty print a compact subset of properties that are often relevant
        interesting = {
            "width": int(last_props.get(cv2.CAP_PROP_FRAME_WIDTH, 0)),
            "height": int(last_props.get(cv2.CAP_PROP_FRAME_HEIGHT, 0)),
            "fps": last_props.get(cv2.CAP_PROP_FPS, 0.0),
            "fourcc": int(last_props.get(cv2.CAP_PROP_FOURCC, 0)),
        }
        print("Camera properties:")
        pprint.pprint(interesting)
