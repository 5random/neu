from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, asdict, field, fields, is_dataclass
from pathlib import Path
from typing import Iterator, List, Dict, Any, Tuple, Optional, Callable, cast
import re
import yaml
import logging
import logging.handlers
import threading
from collections import OrderedDict
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
logger.propagate = False  # Verhindert, dass Logs an die Root-Logger propagiert werden

from enum import Enum

_configured_loggers: dict[str, Tuple[str, str, int, int, bool]] = {}
_configured_loggers_lock = threading.Lock()
_fallback_logger_lock = threading.Lock()
_retired_handler_batches_lock = threading.Lock()
_FALLBACK_HANDLER_MARKER_ATTR = "_cvd_tracker_fallback_handler"
_STARTUP_CONFIG_WARNINGS_ATTR = "_cvd_tracker_startup_config_warnings"
_LOGGER_HANDLER_RETIRE_GRACE_SECONDS = 0.25
_retired_handler_batches: dict[str, list[tuple[tuple[logging.Handler, ...], threading.Timer | None]]] = {}


class ConfigLoadError(RuntimeError):
    """Raised when a present configuration file cannot be loaded safely."""


@dataclass
class _LoggerConfigurationSnapshot:
    handlers: List[logging.Handler]
    level: int
    propagate: bool
    signature: Tuple[str, str, int, int, bool] | None
    had_signature: bool


def _is_fallback_main_handler(handler: logging.Handler) -> bool:
    return bool(getattr(handler, _FALLBACK_HANDLER_MARKER_ATTR, False))


def _is_console_stream_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)


def _clear_logger_handlers(target_logger: logging.Logger) -> None:
    for handler in target_logger.handlers[:]:
        target_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _close_handlers(handlers: List[logging.Handler]) -> None:
    for handler in handlers:
        try:
            handler.close()
        except Exception:
            pass


@contextmanager
def _logging_module_lock() -> Iterator[None]:
    acquire = getattr(logging, "_acquireLock", None)
    release = getattr(logging, "_releaseLock", None)
    if callable(acquire) and callable(release):
        acquire()
        try:
            yield
        finally:
            release()
        return
    yield


def _capture_logger_configuration(name: str, target_logger: logging.Logger) -> _LoggerConfigurationSnapshot:
    return _LoggerConfigurationSnapshot(
        handlers=list(target_logger.handlers),
        level=int(target_logger.level),
        propagate=bool(target_logger.propagate),
        signature=_configured_loggers.get(name),
        had_signature=name in _configured_loggers,
    )


def _restore_logger_configuration(
    name: str,
    target_logger: logging.Logger,
    snapshot: _LoggerConfigurationSnapshot,
) -> None:
    with _logging_module_lock():
        target_logger.setLevel(snapshot.level)
        target_logger.propagate = snapshot.propagate
        target_logger.handlers = list(snapshot.handlers)
        if snapshot.had_signature and snapshot.signature is not None:
            _configured_loggers[name] = snapshot.signature
        else:
            _configured_loggers.pop(name, None)


def _close_retired_handler_batch(name: str, handlers: tuple[logging.Handler, ...]) -> None:
    with _retired_handler_batches_lock:
        batches = _retired_handler_batches.get(name, [])
        remaining_batches = [
            (batch_handlers, batch_timer)
            for batch_handlers, batch_timer in batches
            if batch_handlers is not handlers
        ]
        if remaining_batches:
            _retired_handler_batches[name] = remaining_batches
        else:
            _retired_handler_batches.pop(name, None)
    _close_handlers(list(handlers))


def _retire_logger_handlers(name: str, handlers: List[logging.Handler]) -> None:
    if not handlers:
        return

    retired_handlers = tuple(handlers)
    delay_seconds = max(0.0, float(_LOGGER_HANDLER_RETIRE_GRACE_SECONDS))
    if delay_seconds <= 0:
        _close_handlers(list(retired_handlers))
        return

    retirement_timer = threading.Timer(
        delay_seconds,
        _close_retired_handler_batch,
        args=(name, retired_handlers),
    )
    retirement_timer.daemon = True
    with _retired_handler_batches_lock:
        _retired_handler_batches.setdefault(name, []).append((retired_handlers, retirement_timer))
    retirement_timer.start()


def _drain_retired_logger_handlers(name: str) -> None:
    with _retired_handler_batches_lock:
        retired_batches = _retired_handler_batches.pop(name, [])

    for handlers, retirement_timer in retired_batches:
        if retirement_timer is not None:
            try:
                retirement_timer.cancel()
            except Exception:
                pass
        _close_handlers(list(handlers))


def _logger_has_output_handlers(target_logger: logging.Logger | None) -> bool:
    current_logger = target_logger
    visited: set[int] = set()
    while isinstance(current_logger, logging.Logger):
        logger_id = id(current_logger)
        if logger_id in visited:
            return False
        visited.add(logger_id)
        if current_logger.handlers:
            return True
        if not current_logger.propagate:
            return False
        current_logger = current_logger.parent
    return False


def _get_bootstrap_config_logger() -> logging.Logger:
    main_logger = logging.getLogger("cvd_tracker")
    if _logger_has_output_handlers(main_logger):
        return main_logger.getChild("config")
    return _get_fallback_main_logger().getChild("config")


def _get_fallback_main_logger() -> logging.Logger:
    """Return a minimal logger that never triggers config loading."""
    fallback_logger = logging.getLogger("cvd_tracker")
    with _configured_loggers_lock:
        with _fallback_logger_lock:
            marked_handlers = [
                handler for handler in fallback_logger.handlers
                if _is_fallback_main_handler(handler)
            ]
            if not marked_handlers:
                console_handler = logging.StreamHandler()
                setattr(console_handler, _FALLBACK_HANDLER_MARKER_ATTR, True)
                console_handler.setFormatter(
                    logging.Formatter(
                        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                        datefmt="%d.%m.%Y %H:%M:%S",
                    )
                )
                fallback_logger.addHandler(console_handler)
            elif len(marked_handlers) > 1:
                for duplicate_handler in marked_handlers[1:]:
                    fallback_logger.removeHandler(duplicate_handler)
                    try:
                        duplicate_handler.close()
                    except Exception:
                        pass
            fallback_logger.setLevel(logging.INFO)
            fallback_logger.propagate = False
    return fallback_logger


def _reset_configured_logger(name: str) -> None:
    target_logger = logging.getLogger(name)
    with _configured_loggers_lock:
        _configured_loggers.pop(name, None)
        _clear_logger_handlers(target_logger)
    _drain_retired_logger_handlers(name)


def _resolve_config_path(path: str) -> Path:
    """Resolve a config path relative to the project root."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    project_root = Path(__file__).parents[1]
    return (project_root / candidate).resolve(strict=False)


def _is_absolute_http_url(value: object | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parts = urlsplit(text)
    except Exception:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


# ---------------------------------------------------------------------------
# Metadaten
# ---------------------------------------------------------------------------

@dataclass
class Metadata:
    version: str = "2.0"
    description: str = "CVD-Tracker"
    cvd_id: int = 0
    cvd_name: str = "Default_CVD"
    released_at: str = "2026-04-14"

# ---------------------------------------------------------------------------
# Logging Enums & Classes
# ---------------------------------------------------------------------------

class LogLevel(Enum):
    """Gültige Log-Level für das System"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    
    @classmethod
    def is_valid(cls, level: str) -> bool:
        """Prüfen ob Log-Level gültig ist"""
        return level.upper() in [member.value for member in cls]

# ---------------------------------------------------------------------------
# Hilfs­klassen
# ---------------------------------------------------------------------------

@dataclass
class Resolution:
    width: int
    height: int

# ---------------------------------------------------------------------------
# Webcam & UVC
# ---------------------------------------------------------------------------

@dataclass
class WhiteBalance:
    auto: bool
    value: int  # nur wenn auto == False

@dataclass
class Exposure:
    auto: bool
    value: int  # nur wenn auto == False

@dataclass
class UVCConfig:
    brightness: int
    hue: int
    contrast: int
    saturation: int
    sharpness: int
    gamma: int
    white_balance: WhiteBalance
    gain: int
    backlight_compensation: int
    exposure: Exposure

    def validate(self) -> List[str]:
        errors: List[str] = []
        checks = [
            ("brightness", self.brightness, -64, 64),
            ("hue", self.hue, -180, 180),
            ("contrast", self.contrast, 0, 64),
            ("saturation", self.saturation, 0, 128),
            ("sharpness", self.sharpness, 0, 14),
            ("gamma", self.gamma, 72, 500),
            ("gain", self.gain, 0, 100),
            ("backlight_compensation", self.backlight_compensation, 0, 160),
        ]
        for name, value, lo, hi in checks:
            if not lo <= value <= hi:
                errors.append(f"{name}: {value} outside [{lo}, {hi}]")
        return errors

@dataclass
class WebcamConfig:
    camera_index: int
    default_resolution: Dict[str, int]
    fps: int
    resolution: List[Dict[str, int]]
    preview_fps: int = 15
    preview_max_width: int = 1280
    preview_jpeg_quality: int = 65

    def get_default_resolution(self) -> Resolution:
        return Resolution(**self.default_resolution)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.camera_index < 0:
            errors.append("camera_index must be >= 0")
        if self.fps < 1:
            errors.append("fps must be >= 1")
        if self.preview_fps < 1:
            errors.append("preview_fps must be >= 1")
        if self.preview_max_width < 1:
            errors.append("preview_max_width must be >= 1")
        if not 1 <= self.preview_jpeg_quality <= 100:
            errors.append("preview_jpeg_quality must be within [1, 100]")
        return errors

# ---------------------------------------------------------------------------
# Bewegungserkennung
# ---------------------------------------------------------------------------

@dataclass
class ROI:
    enabled: bool
    x: int
    y: int
    width: int
    height: int
    points: List[List[int]] = field(default_factory=list)  # List of [x, y] coordinates

    def validate(self, frame_w: int, frame_h: int) -> List[str]:
        if not self.enabled:
            return []
        errors: List[str] = []
        
        # Validate Polygon Points if present
        if self.points:
            if len(self.points) < 3:
                errors.append("Polygon ROI must have at least 3 points")
            for i, pt in enumerate(self.points):
                if len(pt) != 2:
                    errors.append(f"Point {i} must have 2 coordinates")
                    continue
                px, py = pt
                if px < 0 or py < 0:
                    errors.append(f"Point {i} coordinates must not be negative")
                if px > frame_w:
                    errors.append(f"Point {i} x={px} exceeds frame width {frame_w}")
                if py > frame_h:
                    errors.append(f"Point {i} y={py} exceeds frame height {frame_h}")
        else:
            # Fallback to Rectangle validation
            if self.x < 0 or self.y < 0:
                errors.append("ROI coordinates must not be negative")
            if self.width <= 0 or self.height <= 0:
                errors.append("ROI size must be positive")
            if self.x + self.width > frame_w:
                errors.append("ROI exceeds frame width")
            if self.y + self.height > frame_h:
                errors.append("ROI exceeds frame height")
        return errors

@dataclass
class MotionDetectionConfig:
    region_of_interest: Dict[str, Any]
    sensitivity: float
    background_learning_rate: float
    min_contour_area: int
    frame_skip: int = 1
    processing_max_width: int = 800

    def get_roi(self) -> ROI:
        return ROI(**self.region_of_interest)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not 0.001 <= self.sensitivity <= 1.0:
            errors.append("Sensitivity outside [0.001, 1.0]")
        if not 0.001 <= self.background_learning_rate <= 1.0:
            errors.append("Learning rate outside [0.001, 1.0]")
        if self.frame_skip < 1:
            errors.append("frame_skip must be >= 1")
        if self.processing_max_width < 1:
            errors.append("processing_max_width must be >= 1")
        if self.min_contour_area < 1:
            errors.append("min_contour_area must be ≥1")
        return errors

# ---------------------------------------------------------------------------
# Messungs­steuerung
# ---------------------------------------------------------------------------

@dataclass
class MeasurementConfig:
    auto_start: bool
    session_timeout_minutes: int
    save_alert_images: bool
    # Legacy config field kept for backward-compatible imports.
    # New alert images are persisted under history_path.
    image_save_path: str
    image_format: str
    image_quality: int
    alert_delay_seconds: int
    session_timeout_seconds: int = 0
    # New alert/runtime tuning parameters (optional with safe defaults)
    max_alerts_per_session: int = 5
    alert_check_interval: float = 5.0
    alert_cooldown_seconds: int = 300
    alert_include_snapshot: bool = True
    # Session inactivity timeout separate from hard session limit (0 = disabled)
    inactivity_timeout_minutes: int = 60
    # Motion summary logging controls
    motion_summary_interval_seconds: int = 60
    enable_motion_summary_logs: bool = True
    # Primary persistence path for alert history JSON and alert images.
    history_path: str = "data/history"

    def get_session_timeout_seconds(self) -> int:
        """Return the effective hard session timeout in seconds."""
        raw_seconds = int(getattr(self, "session_timeout_seconds", 0) or 0)
        if raw_seconds > 0:
            return raw_seconds
        raw_minutes = int(getattr(self, "session_timeout_minutes", 0) or 0)
        return max(0, raw_minutes * 60)

    def set_session_timeout_seconds(self, seconds: int) -> None:
        """Persist timeout in both the new seconds field and the legacy minutes field."""
        normalized_seconds = max(0, int(seconds or 0))
        self.session_timeout_seconds = normalized_seconds
        self.session_timeout_minutes = ((normalized_seconds + 59) // 60) if normalized_seconds > 0 else 0

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.alert_delay_seconds < 30:
            errors.append("alert_delay_seconds < 30")
        if self.session_timeout_minutes < 0:
            errors.append("session_timeout_minutes < 0")
        if self.session_timeout_seconds < 0:
            errors.append("session_timeout_seconds < 0")
        if self.max_alerts_per_session < 1:
            errors.append("max_alerts_per_session must be ≥ 1")
        if self.alert_check_interval <= 0:
            errors.append("alert_check_interval must be > 0")
        if self.alert_cooldown_seconds < 0:
            errors.append("alert_cooldown_seconds must be ≥ 0")
        if self.inactivity_timeout_minutes < 0:
            errors.append("inactivity_timeout_minutes must be ≥ 0 (0 disables)")
        if self.motion_summary_interval_seconds < 5:
            errors.append("motion_summary_interval_seconds must be ≥ 5")
        img_fmt = self.image_format.lower()
        if img_fmt in ("jpg", "jpeg") and not 1 <= self.image_quality <= 100:
            errors.append("image_quality außerhalb [1, 100]")
        return errors

    def ensure_save_path(self) -> None:
        history_path = str(getattr(self, "history_path", "") or "").strip()
        if history_path:
            Path(history_path).mkdir(parents=True, exist_ok=True)
            return

        legacy_image_path = str(getattr(self, "image_save_path", "") or "").strip()
        if legacy_image_path:
            Path(legacy_image_path).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# E‑Mail
# ---------------------------------------------------------------------------

@dataclass
class EmailTemplate:
    subject: str
    body: str

@dataclass
class EmailConfig:
    website_url: str
    recipients: List[str]
    smtp_server: str
    smtp_port: int
    sender_email: str
    templates: Dict[str, Dict[str, str]]
    send_as_html: bool = True
    website_url_source: str = "runtime_persist"
    # Recipient groups and active group selection
    groups: Dict[str, List[str]] = field(default_factory=dict)
    active_groups: List[str] = field(default_factory=list)
    # Recipients that always receive notifications, regardless of active groups
    static_recipients: List[str] = field(default_factory=list)
    # Explicit targeting disables the legacy fallback to the shared address book
    explicit_targeting: bool = False
    # Measurement notification toggles
    notifications: Dict[str, bool] = field(default_factory=dict)
    # Per-group notification preferences
    # Mapping: group_name -> { 'on_start': bool, 'on_end': bool, 'on_stop': bool }
    group_prefs: Dict[str, Dict[str, bool]] = field(default_factory=dict)
    # Per-recipient notification preferences (overrides global notifications)
    # Mapping: email -> { 'on_start': bool, 'on_end': bool, 'on_stop': bool }
    recipient_prefs: Dict[str, Dict[str, bool]] = field(default_factory=dict)

    EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}$")
    EVENT_PREF_KEYS = ("on_start", "on_end", "on_stop")
    SYSTEM_STATIC_GROUP = "__static__"
    WEBSITE_URL_SOURCE_CONFIG = "config"
    WEBSITE_URL_SOURCE_RUNTIME = "runtime"
    WEBSITE_URL_SOURCE_RUNTIME_PERSIST = "runtime_persist"
    WEBSITE_URL_SOURCES = (
        WEBSITE_URL_SOURCE_CONFIG,
        WEBSITE_URL_SOURCE_RUNTIME,
        WEBSITE_URL_SOURCE_RUNTIME_PERSIST,
    )

    def __post_init__(self) -> None:
        """Runtime type validation for new fields.

        Ensures:
        - groups is Dict[str, List[str]] (each value list of str)
        - active_groups is List[str]
        - notifications is Dict[str, bool]
        Raises TypeError with a clear message on violations.
        """
        # groups
        if not isinstance(self.groups, dict):
            raise TypeError(f"EmailConfig.groups must be Dict[str, List[str]], got {type(self.groups).__name__}: {self.groups!r}")
        for k, v in self.groups.items():
            if not isinstance(k, str):
                raise TypeError(f"EmailConfig.groups keys must be str, got {type(k).__name__}: {k!r}")
            if not isinstance(v, list):
                raise TypeError(f"EmailConfig.groups['{k}'] must be List[str], got {type(v).__name__}: {v!r}")
            group_members = v
            for idx, item in enumerate(group_members):
                if not isinstance(item, str):
                    raise TypeError(f"EmailConfig.groups['{k}'][{idx}] must be str, got {type(item).__name__}: {item!r}")

        # active_groups
        if not isinstance(self.active_groups, list):
            raise TypeError(f"EmailConfig.active_groups must be List[str], got {type(self.active_groups).__name__}: {self.active_groups!r}")
        for idx, g in enumerate(self.active_groups):
            if not isinstance(g, str):
                raise TypeError(f"EmailConfig.active_groups[{idx}] must be str, got {type(g).__name__}: {g!r}")

        # static_recipients
        if not isinstance(self.static_recipients, list):
            raise TypeError(
                f"EmailConfig.static_recipients must be List[str], got {type(self.static_recipients).__name__}: {self.static_recipients!r}"
            )
        for idx, email in enumerate(self.static_recipients):
            if not isinstance(email, str):
                raise TypeError(f"EmailConfig.static_recipients[{idx}] must be str, got {type(email).__name__}: {email!r}")

        if not isinstance(self.explicit_targeting, bool):
            raise TypeError(
                f"EmailConfig.explicit_targeting must be bool, got {type(self.explicit_targeting).__name__}: {self.explicit_targeting!r}"
            )

        if not isinstance(self.send_as_html, bool):
            raise TypeError(
                f"EmailConfig.send_as_html must be bool, got {type(self.send_as_html).__name__}: {self.send_as_html!r}"
            )
        if not isinstance(self.website_url, str):
            raise TypeError(
                f"EmailConfig.website_url must be str, got {type(self.website_url).__name__}: {self.website_url!r}"
            )
        if not isinstance(self.website_url_source, str):
            raise TypeError(
                f"EmailConfig.website_url_source must be str, got {type(self.website_url_source).__name__}: {self.website_url_source!r}"
            )

        # notifications
        if not isinstance(self.notifications, dict):
            raise TypeError(f"EmailConfig.notifications must be Dict[str, bool], got {type(self.notifications).__name__}: {self.notifications!r}")
        for k, notif_enabled in self.notifications.items():
            if not isinstance(k, str):
                raise TypeError(f"EmailConfig.notifications keys must be str, got {type(k).__name__}: {k!r}")
            if not isinstance(notif_enabled, bool):
                raise TypeError(f"EmailConfig.notifications['{k}'] must be bool, got {type(notif_enabled).__name__}: {notif_enabled!r}")

        # group_prefs
        if not isinstance(self.group_prefs, dict):
            raise TypeError(
                f"EmailConfig.group_prefs must be Dict[str, Dict[str, bool]], got {type(self.group_prefs).__name__}: {self.group_prefs!r}"
            )
        for group_name, prefs in self.group_prefs.items():
            if not isinstance(group_name, str):
                raise TypeError(f"EmailConfig.group_prefs keys must be str (group), got {type(group_name).__name__}: {group_name!r}")
            if not isinstance(prefs, dict):
                raise TypeError(f"EmailConfig.group_prefs['{group_name}'] must be Dict[str, bool], got {type(prefs).__name__}: {prefs!r}")
            for k, pref_value in prefs.items():
                if not isinstance(k, str):
                    raise TypeError(f"EmailConfig.group_prefs['{group_name}'] keys must be str, got {type(k).__name__}: {k!r}")
                if not isinstance(pref_value, bool):
                    raise TypeError(f"EmailConfig.group_prefs['{group_name}']['{k}'] must be bool, got {type(pref_value).__name__}: {pref_value!r}")

        # recipient_prefs
        if not isinstance(self.recipient_prefs, dict):
            raise TypeError(
                f"EmailConfig.recipient_prefs must be Dict[str, Dict[str, bool]], got {type(self.recipient_prefs).__name__}: {self.recipient_prefs!r}"
            )
        for email, prefs in self.recipient_prefs.items():
            if not isinstance(email, str):
                raise TypeError(f"EmailConfig.recipient_prefs keys must be str (email), got {type(email).__name__}: {email!r}")
            if not isinstance(prefs, dict):
                raise TypeError(f"EmailConfig.recipient_prefs['{email}'] must be Dict[str, bool], got {type(prefs).__name__}: {prefs!r}")
            for k, pref_value in prefs.items():
                if not isinstance(k, str):
                    raise TypeError(f"EmailConfig.recipient_prefs['{email}'] keys must be str, got {type(k).__name__}: {k!r}")
                if not isinstance(pref_value, bool):
                    raise TypeError(f"EmailConfig.recipient_prefs['{email}']['{k}'] must be bool, got {type(pref_value).__name__}: {pref_value!r}")

    def validate(self) -> List[str]:
        errors: List[str] = []
        # Base emails
        if not self.sender_email:
            errors.append("missing sender email address")
        elif not self.EMAIL_RE.match(self.sender_email):
            errors.append(f"invalid sender email address: {self.sender_email}")
        for mail in self.recipients or []:
            if not self.EMAIL_RE.match(mail):
                errors.append(f"invalid email address: {mail}")
        # Groups and active groups
        if self.groups:
            for gname, addrs in self.groups.items():
                if not gname or not isinstance(gname, str):
                    errors.append(f"invalid group name: {gname!r}")
                elif self.is_reserved_group_name(gname):
                    errors.append(f"reserved group name is managed by the system: {gname!r}")
                for mail in addrs or []:
                    if not self.EMAIL_RE.match(mail):
                        errors.append(f"invalid email address in group '{gname}': {mail}")
        if self.active_groups:
            reserved_active = [g for g in self.active_groups if self.is_reserved_group_name(g)]
            if reserved_active:
                errors.append(f"reserved active groups are managed by the system: {sorted(set(reserved_active))}")
            unknown = [g for g in self.active_groups if g not in (self.groups or {})]
            if unknown:
                errors.append(f"unknown active groups: {unknown}")
        for mail in self.static_recipients or []:
            if not self.EMAIL_RE.match(mail):
                errors.append(f"invalid static recipient email address: {mail}")
        if not isinstance(self.explicit_targeting, bool):
            errors.append(f"explicit_targeting must be bool, got {type(self.explicit_targeting).__name__}")
        if not isinstance(self.send_as_html, bool):
            errors.append(f"send_as_html must be bool, got {type(self.send_as_html).__name__}")
        normalized_website_url_source = str(self.website_url_source or "").strip().lower()
        if normalized_website_url_source not in self.WEBSITE_URL_SOURCES:
            errors.append(
                f"website_url_source must be one of {list(self.WEBSITE_URL_SOURCES)}, got {self.website_url_source!r}"
            )
        website_url_required = normalized_website_url_source != self.WEBSITE_URL_SOURCE_RUNTIME
        if not isinstance(self.website_url, str):
            errors.append(f"website_url must be str, got {type(self.website_url).__name__}")
            website_url = ""
        else:
            website_url = self.website_url.strip()
            if not website_url:
                if website_url_required:
                    errors.append("website_url must not be empty")
            elif not _is_absolute_http_url(website_url):
                errors.append("website_url must be an absolute http(s) URL")
        # Notifications flags (optional)
        if self.notifications is not None and isinstance(self.notifications, dict):
            for k, v in self.notifications.items():
                if k not in self.EVENT_PREF_KEYS:
                    errors.append(f"notifications contains unknown event key: {k!r}")
                if not isinstance(v, bool):
                    errors.append(f"notification flag '{k}' must be a bool, got {type(v).__name__}")
        if self.group_prefs is not None and isinstance(self.group_prefs, dict):
            for group_name, prefs in self.group_prefs.items():
                if not isinstance(group_name, str) or not group_name:
                    errors.append(f"group_prefs has invalid group key: {group_name!r}")
                    continue
                if self.is_reserved_group_name(group_name):
                    errors.append(f"group_prefs references reserved system group: {group_name!r}")
                    continue
                if group_name not in (self.groups or {}):
                    errors.append(f"group_prefs references unknown group: {group_name!r}")
                if not isinstance(prefs, dict):
                    errors.append(f"group_prefs['{group_name}'] must be dict, got {type(prefs).__name__}")
                    continue
                for k, v in prefs.items():
                    if k not in self.EVENT_PREF_KEYS:
                        errors.append(f"group_prefs['{group_name}'] contains unknown event key: {k!r}")
                    if not isinstance(v, bool):
                        errors.append(f"group_prefs['{group_name}']['{k}'] must be bool, got {type(v).__name__}")
        # recipient_prefs validation (ensure keys are emails and values are bools)
        if self.recipient_prefs is not None and isinstance(self.recipient_prefs, dict):
            for email, prefs in self.recipient_prefs.items():
                if not isinstance(email, str) or not self.EMAIL_RE.match(email):
                    errors.append(f"recipient_prefs has invalid email key: {email!r}")
                if not isinstance(prefs, dict):
                    errors.append(f"recipient_prefs['{email}'] must be dict, got {type(prefs).__name__}")
                    continue
                for k, v in prefs.items():
                    if k not in self.EVENT_PREF_KEYS:
                        errors.append(f"recipient_prefs['{email}'] contains unknown event key: {k!r}")
                    if not isinstance(v, bool):
                        errors.append(f"recipient_prefs['{email}']['{k}'] must be bool, got {type(v).__name__}")
        # SMTP
        if not 1 <= self.smtp_port <= 65535:
            errors.append("smtp_port must be between [1, 65535]")
        if not self.smtp_server:
            errors.append("smtp_server must not be empty")
        return errors

    @staticmethod
    def _normalize_template_text(value: Any, fallback: str, *, multiline: bool) -> str:
        text = fallback if not isinstance(value, str) else value
        if multiline:
            # Repair legacy YAML values that stored escaped newlines literally.
            if "\\n" in text:
                text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
            return text

        if "\\r\\n" in text or "\\n" in text:
            text = text.replace("\\r\\n", " ").replace("\\n", " ")
        if "\r" in text or "\n" in text:
            text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text

    def _build_template(self, name: str, default_subject: str, default_body: str) -> EmailTemplate:
        data = self.templates.get(name, {})
        if not isinstance(data, dict):
            data = {}
        return EmailTemplate(
            subject=self._normalize_template_text(
                data.get("subject"),
                default_subject,
                multiline=False,
            ),
            body=self._normalize_template_text(
                data.get("body"),
                default_body,
                multiline=True,
            ),
        )

    def alert_template(self) -> EmailTemplate:
        default_subject = "CVD-TRACKER{cvd_id}-{cvd_name}-Alert: no motion detected - {timestamp}"
        default_body = (
            "CVD-Tracker{cvd_id} {cvd_name}: Alert!\n"
            "Movement has not been detected since: {last_motion_time}!\n"
            "Please check the issue via the web application at: {website_url}.\n"
            "\n"
            "Details:\n"
            "   CVD-ID:         {cvd_id}\n"
            "   CVD-Name:       {cvd_name}\n"
            "   Website URL:    {website_url}\n"
            "\n"
            "   Session-ID:     {session_id}\n"
            "   Last motion at: {last_motion_time}\n"
            "   Start:          {start_time}\n"
            "   End:            {end_time}\n"
            "   Duration:       {duration}\n"
            "   Reason:         {reason}\n"
            "\n"
            "   Camera: Index   {camera_index}\n"
            "   Sensitivity:    {sensitivity}\n"
            "   ROI enabled:    {roi_enabled}\n"
            "\n"
            "{snapshot_note}"
        )
        return self._build_template("alert", default_subject, default_body)
    
    def test_template(self) -> EmailTemplate:
        default_subject = "CVD-TRACKER{cvd_id}-{cvd_name}: Test email - {timestamp}"
        default_body = (
            "This is a test email from CVD-TRACKER{cvd_id}-{cvd_name} sent at {timestamp}.\n"
            "If you received this email, the email configuration is correct.\n"
            "\n"
            "You can access the web application at: {website_url}.\n"
            "\n"
            "Details:\n"
            "   CVD-ID:         {cvd_id}\n"
            "   CVD-Name:       {cvd_name}\n"
            "   Website URL:    {website_url}\n"
            "\n"
            "   Session-ID:     {session_id}\n"
            "   Last motion at: {last_motion_time}\n"
            "   Start:          {start_time}\n"
            "   End:            {end_time}\n"
            "   Duration:       {duration}\n"
            "   Reason:         {reason}\n"
            "\n"
            "   Camera: Index   {camera_index}\n"
            "   Sensitivity:    {sensitivity}\n"
            "   ROI enabled:    {roi_enabled}\n"
            "\n"
        )
        return self._build_template("test", default_subject, default_body)
    
    def measurement_start_template(self) -> EmailTemplate:
        default_subject = "CVD-TRACKER{cvd_id}-{cvd_name}: Measurement started - {timestamp}"
        default_body = (
            "CVD-Tracker{cvd_id}-{cvd_name} Measurement Started\n"
            "A new measurement session has started at {timestamp}.\n"
            "\n"
            "You can monitor the session via the web application at: {website_url}.\n"
            "\n"
            "Details:\n"
            "   CVD-ID:         {cvd_id}\n"
            "   CVD-Name:       {cvd_name}\n"
            "   Website URL:    {website_url}\n"
            "\n"
            "   Session-ID:     {session_id}\n"
            "   Last motion at: {last_motion_time}\n"
            "   Start:          {start_time}\n"
            "   End:            {end_time}\n"
            "   Duration:       {duration}\n"
            "   Reason:         {reason}\n"
            "\n"
            "   Camera: Index   {camera_index}\n"
            "   Sensitivity:    {sensitivity}\n"
            "   ROI enabled:    {roi_enabled}\n"
            "\n"
        )
        return self._build_template("measurement_start", default_subject, default_body)
    
    def measurement_end_template(self) -> EmailTemplate:
        default_subject = "CVD-TRACKER{cvd_id}-{cvd_name}: Measurement ended - {timestamp}"
        default_body = (
            "CVD-Tracker{cvd_id}-{cvd_name} Measurement Ended\n"
            "The measurement session has ended at {timestamp}.\n"
            "\n"
            "You can monitor the session via the web application at: {website_url}.\n"
            "\n"
            "Details:\n"
            "   CVD-ID:         {cvd_id}\n"
            "   CVD-Name:       {cvd_name}\n"
            "   Website URL:    {website_url}\n"
            "\n"
            "   Session-ID:     {session_id}\n"
            "   Last motion at: {last_motion_time}\n"
            "   Start:          {start_time}\n"
            "   End:            {end_time}\n"
            "   Duration:       {duration}\n"
            "   Reason:         {reason}\n"
            "\n"
            "   Camera: Index   {camera_index}\n"
            "   Sensitivity:    {sensitivity}\n"
            "   ROI enabled:    {roi_enabled}\n"
            "\n"
        )
        return self._build_template("measurement_end", default_subject, default_body)
    
    def measurement_stop_template(self) -> EmailTemplate:
        default_subject = "CVD-TRACKER{cvd_id}-{cvd_name}: Measurement stopped - {timestamp}"
        default_body = (
            "CVD-Tracker{cvd_id}-{cvd_name} Measurement Stopped\n"
            "The measurement session has ended at {timestamp}.\n"
            "\n"
            "You can monitor the session via the web application at: {website_url}.\n"
            "\n"
            "Details:\n"
            "   CVD-ID:         {cvd_id}\n"
            "   CVD-Name:       {cvd_name}\n"
            "   Website URL:    {website_url}\n"
            "\n"
            "   Session-ID:     {session_id}\n"
            "   Last motion at: {last_motion_time}\n"
            "   Start:          {start_time}\n"
            "   End:            {end_time}\n"
            "   Duration:       {duration}\n"
            "   Reason:         {reason}\n"
            "\n"
            "   Camera: Index   {camera_index}\n"
            "   Sensitivity:    {sensitivity}\n"
            "   ROI enabled:    {roi_enabled}\n"
            "\n"
        )
        return self._build_template("measurement_stop", default_subject, default_body)

    @staticmethod
    def _dedupe_valid_emails(addresses: List[str]) -> List[str]:
        seen: Dict[str, None] = {}
        for addr in addresses or []:
            candidate = str(addr or "").strip()
            if candidate and EmailConfig.EMAIL_RE.match(candidate) and candidate not in seen:
                seen[candidate] = None
        return list(seen.keys())

    @classmethod
    def _pref_allows_event(cls, prefs: Dict[str, bool], event_key: str, *, default: bool = True) -> bool:
        if event_key not in cls.EVENT_PREF_KEYS:
            raise ValueError(f"unsupported event key: {event_key}")
        if not isinstance(prefs, dict):
            return default
        return bool(prefs.get(event_key, default))

    @classmethod
    def _normalize_event_pref_key(cls, key: Any, *, context: str) -> str:
        normalized_key = _coerce_string(key, allow_empty=False)
        if normalized_key not in cls.EVENT_PREF_KEYS:
            allowed = ", ".join(cls.EVENT_PREF_KEYS)
            raise ValueError(f"{context} contains unknown event key: {normalized_key!r}; expected one of [{allowed}]")
        return normalized_key

    @classmethod
    def is_reserved_group_name(cls, name: Any) -> bool:
        return isinstance(name, str) and name.strip() == cls.SYSTEM_STATIC_GROUP

    @classmethod
    def ensure_group_name_allowed(cls, name: str, *, context: str = "group") -> str:
        normalized_name = _coerce_string(name, allow_empty=False)
        if cls.is_reserved_group_name(normalized_name):
            raise ValueError(f"{context} uses reserved group name: {normalized_name!r}")
        return normalized_name

    def get_visible_groups(self) -> Dict[str, List[str]]:
        return {
            name: list(members or [])
            for name, members in (self.groups or {}).items()
            if not self.is_reserved_group_name(name)
        }

    def get_visible_group_names(self) -> List[str]:
        return list(self.get_visible_groups().keys())

    def get_visible_active_groups(self) -> List[str]:
        visible = set(self.get_visible_group_names())
        return [group for group in self.active_groups or [] if group in visible]

    def get_runtime_groups(self) -> Dict[str, List[str]]:
        runtime_groups = self.get_visible_groups()
        if self.static_recipients:
            runtime_groups[self.SYSTEM_STATIC_GROUP] = list(self.static_recipients or [])
        return runtime_groups

    def get_runtime_active_groups(self) -> List[str]:
        runtime_active_groups: List[str] = []
        if self.static_recipients:
            runtime_active_groups.append(self.SYSTEM_STATIC_GROUP)
        runtime_active_groups.extend(self.get_visible_active_groups())
        return runtime_active_groups

    def get_known_recipients(self) -> List[str]:
        """Return the shared address book with all referenced emails included."""
        addresses: List[str] = list(self.recipients or [])
        addresses.extend(self.static_recipients or [])
        for members in (self.groups or {}).values():
            addresses.extend(members or [])
        addresses.extend((self.recipient_prefs or {}).keys())
        return self._dedupe_valid_emails(addresses)

    def uses_explicit_targeting(self) -> bool:
        """Return whether explicit targeting is active for delivery resolution."""
        return bool(self.explicit_targeting or self.static_recipients or self.active_groups)

    def get_static_recipients_for_editor(self) -> List[str]:
        """Return the static-recipient selection shown in GUI editors.

        Legacy configs without explicit targeting expose the shared address book as
        the effective static-recipient set until the first explicit save happens.
        """
        configured = self._dedupe_valid_emails(list(self.static_recipients or []))
        if configured or self.active_groups or self.explicit_targeting:
            return configured
        return self.get_known_recipients()

    def enable_explicit_targeting(self, *, materialize_legacy_targets: bool = False) -> None:
        """Switch the config to explicit targeting mode.

        When requested, legacy fallback recipients are materialized into
        ``static_recipients`` first so the effective target set stays stable.
        """
        if materialize_legacy_targets and not self.uses_explicit_targeting():
            self.static_recipients = self.get_target_recipients()
        self.explicit_targeting = True

    def get_target_recipients(self) -> List[str]:
        """Compute effective recipients for alerts/test mail.

        Explicit targeting is the union of static recipients and active groups.
        If neither is configured, fall back to the legacy behaviour of sending to
        the full recipients list.
        """
        explicit_targeting = self.uses_explicit_targeting()
        runtime_groups = self.get_runtime_groups()
        collected: List[str] = []
        for group_name in self.get_runtime_active_groups():
            collected.extend(runtime_groups.get(group_name, []) or [])
        deduped = self._dedupe_valid_emails(collected)
        if deduped:
            return deduped
        if explicit_targeting:
            return []
        return self._dedupe_valid_emails(list(self.recipients or []))

    def get_measurement_event_recipients(self, event_key: str) -> List[str]:
        """Resolve lifecycle notification recipients for a specific event."""
        if event_key not in self.EVENT_PREF_KEYS:
            raise ValueError(f"unsupported event key: {event_key}")
        if not bool((self.notifications or {}).get(event_key, False)):
            return []

        explicit_targeting = self.uses_explicit_targeting()
        collected: List[str] = []
        runtime_groups = self.get_runtime_groups()

        for group_name in self.get_runtime_active_groups():
            if group_name == self.SYSTEM_STATIC_GROUP:
                for addr in runtime_groups.get(group_name, []) or []:
                    prefs = (self.recipient_prefs or {}).get(addr, {})
                    if self._pref_allows_event(prefs, event_key, default=True):
                        collected.append(addr)
                continue
            prefs = (self.group_prefs or {}).get(group_name, {})
            if not self._pref_allows_event(prefs, event_key, default=True):
                continue
            collected.extend(runtime_groups.get(group_name, []) or [])

        deduped = self._dedupe_valid_emails(collected)
        if deduped:
            return deduped
        if explicit_targeting:
            return []

        legacy: List[str] = []
        for addr in self.recipients or []:
            prefs = (self.recipient_prefs or {}).get(addr, {})
            if self._pref_allows_event(prefs, event_key, default=True):
                legacy.append(addr)
        return self._dedupe_valid_emails(legacy)
# ---------------------------------------------------------------------------
# GUI & Logging
# ---------------------------------------------------------------------------

@dataclass
class GUIConfig:
    title: str
    host: str
    port: int
    auto_open_browser: bool = False
    update_interval_ms: int = 100
    status_refresh_interval_ms: int = 1000
    reverse_proxy_enabled: bool = False
    forwarded_allow_ips: str = "127.0.0.1"
    root_path: str = ""
    session_cookie_https_only: bool = False

    def validate(self) -> List[str]:
        errors: List[str] = []
        try:
            host = _coerce_strict_string(self.host, allow_empty=False).strip()
        except ValueError as exc:
            errors.append(f"host {exc}")
        else:
            if not host:
                errors.append("host must not be empty")
        try:
            normalized_port = int(self.port)
        except (TypeError, ValueError):
            errors.append("port must be an integer")
        else:
            if not 1 <= normalized_port <= 65535:
                errors.append("port must be between [1, 65535]")
        try:
            normalized_interval = int(self.update_interval_ms)
        except (TypeError, ValueError):
            errors.append("update_interval_ms must be an integer")
        else:
            if normalized_interval < 1:
                errors.append("update_interval_ms must be >= 1")
        try:
            normalized_status_interval = int(self.status_refresh_interval_ms)
        except (TypeError, ValueError):
            errors.append("status_refresh_interval_ms must be an integer")
        else:
            if normalized_status_interval < 1:
                errors.append("status_refresh_interval_ms must be >= 1")
        if not isinstance(self.auto_open_browser, bool):
            errors.append("auto_open_browser must be a bool")
        if not isinstance(self.reverse_proxy_enabled, bool):
            errors.append("reverse_proxy_enabled must be a bool")
        if not isinstance(self.session_cookie_https_only, bool):
            errors.append("session_cookie_https_only must be a bool")

        try:
            forwarded_allow_ips = _coerce_strict_string(self.forwarded_allow_ips, allow_empty=False).strip()
        except ValueError as exc:
            errors.append(f"forwarded_allow_ips {exc}")
            forwarded_allow_ips = ""
        if self.reverse_proxy_enabled and not forwarded_allow_ips:
            errors.append("forwarded_allow_ips must not be empty when reverse_proxy_enabled is true")

        try:
            _normalize_root_path(self.root_path)
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    def get_status_refresh_interval_ms(self) -> int:
        raw_value = getattr(self, "status_refresh_interval_ms", 0) or 0
        try:
            normalized = int(raw_value)
        except (TypeError, ValueError):
            normalized = 0
        if normalized > 0:
            return normalized
        return max(1, int(getattr(self, "update_interval_ms", 100) or 100))

@dataclass
class LoggingConfig:
    level: str
    file: str
    max_file_size_mb: int = 10
    backup_count: int = 5
    console_output: bool = True
    
    def validate(self) -> List[str]:
        """Logging-Parameter validieren"""
        errors: List[str] = []
        
        # Log-Level validieren
        if not LogLevel.is_valid(self.level):
            valid_levels = [level.value for level in LogLevel]
            errors.append(f"invalid Log-Level '{self.level}'. valid: {valid_levels}")
        
        if not isinstance(self.console_output, bool):
            errors.append("console_output must be a bool")
        
        # Datei-Parameter validieren
        if self.max_file_size_mb < 1:
            errors.append("max_file_size_mb must be at least 1 MB")
        elif self.max_file_size_mb > 100:
            errors.append("max_file_size_mb must not be greater than 100 MB")

        if self.backup_count < 0:
            errors.append("backup_count must not be negative")
        elif self.backup_count > 20:
            errors.append("backup_count must not be greater than 20")
        return errors

    def _has_desired_logger_configuration(
        self,
        target_logger: logging.Logger,
        desired_signature: Tuple[str, str, int, int, bool],
    ) -> bool:
        expected_level, expected_file, expected_max_size_mb, expected_backup_count, expected_console = desired_signature
        rotating_handlers = [
            handler
            for handler in target_logger.handlers
            if isinstance(handler, logging.handlers.RotatingFileHandler)
        ]
        console_handlers = [
            handler
            for handler in target_logger.handlers
            if _is_console_stream_handler(handler)
        ]
        expected_total_handlers = 1 + int(expected_console)
        if (
            len(rotating_handlers) != 1
            or len(console_handlers) != int(expected_console)
            or len(target_logger.handlers) != expected_total_handlers
        ):
            return False

        file_handler = rotating_handlers[0]
        configured_path = _resolve_config_path(str(expected_file))
        actual_path = Path(str(getattr(file_handler, "baseFilename", ""))).resolve(strict=False)
        return (
            actual_path == configured_path
            and int(getattr(file_handler, "maxBytes", -1)) == int(expected_max_size_mb) * 1024 * 1024
            and int(getattr(file_handler, "backupCount", -1)) == int(expected_backup_count)
            and target_logger.level == getattr(logging, str(expected_level).upper())
            and target_logger.propagate is False
        )

    def _build_handlers(self) -> List[logging.Handler]:
        log_path = _resolve_config_path(self.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handlers: List[logging.Handler] = []
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_path),
                maxBytes=self.max_file_size_mb * 1024 * 1024,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            handlers.append(file_handler)
            if self.console_output:
                handlers.append(logging.StreamHandler())

            formatter = logging.Formatter(
                fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%d.%m.%Y %H:%M:%S'
            )
            for handler in handlers:
                handler.setFormatter(formatter)
            return handlers
        except Exception:
            _close_handlers(handlers)
            raise

    def _activate_logger_configuration(
        self,
        target_logger: logging.Logger,
        *,
        handlers: List[logging.Handler],
    ) -> None:
        target_logger.setLevel(getattr(logging, self.level.upper()))
        target_logger.propagate = False  # Verhindert, dass Logs an die Root-Logger propagiert werden
        target_logger.handlers = list(handlers)

    def _commit_logger_configuration(
        self,
        name: str,
        target_logger: logging.Logger,
        *,
        handlers: List[logging.Handler],
        signature: Tuple[str, str, int, int, bool],
    ) -> None:
        with _logging_module_lock():
            self._activate_logger_configuration(target_logger, handlers=handlers)
            _configured_loggers[name] = signature
    
    def setup_logger(self, name: str = "cvd_tracker") -> logging.Logger:
        """RotatingFileHandler-Logger einrichten"""
        logger = logging.getLogger(name)
        desired_signature = (
            str(self.level).upper(),
            str(_resolve_config_path(self.file)),
            int(self.max_file_size_mb),
            int(self.backup_count),
            bool(self.console_output),
        )

        previous_snapshot: _LoggerConfigurationSnapshot | None = None
        with _configured_loggers_lock:
            if (
                _configured_loggers.get(name) == desired_signature
                and self._has_desired_logger_configuration(logger, desired_signature)
            ):
                return logger
            previous_snapshot = _capture_logger_configuration(name, logger)
            new_handlers = self._build_handlers()
            try:
                self._commit_logger_configuration(
                    name,
                    logger,
                    handlers=new_handlers,
                    signature=desired_signature,
                )
            except Exception:
                _restore_logger_configuration(name, logger, previous_snapshot)
                _close_handlers(new_handlers)
                raise
        if previous_snapshot is not None:
            _retire_logger_handlers(name, previous_snapshot.handlers)
        logger.info(
            f"🚀 Logging initialized: {self.file} (max: {self.max_file_size_mb}MB, backups: {self.backup_count})"
        )

        return logger

# ---------------------------------------------------------------------------
# Top‑Level AppConfig
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    metadata: Metadata
    webcam: WebcamConfig
    uvc_controls: UVCConfig
    motion_detection: MotionDetectionConfig
    measurement: MeasurementConfig
    email: EmailConfig
    gui: GUIConfig
    logging: LoggingConfig

    def __init__(
        self,
        metadata: Metadata,
        webcam: WebcamConfig,
        uvc_controls: UVCConfig,
        motion_detection: MotionDetectionConfig,
        measurement: MeasurementConfig,
        email: EmailConfig,
        gui: GUIConfig,
        logging: LoggingConfig,
    ) -> None:
        self.metadata = metadata
        self.webcam = webcam
        self.uvc_controls = uvc_controls
        self.motion_detection = motion_detection
        self.measurement = measurement
        self.email = email
        self.gui = gui
        self.logging = logging

    def validate_all(self) -> Dict[str, List[str]]:
        res: Dict[str, List[str]] = {}
        if (e := self.webcam.validate()):
            res["webcam"] = e
        if (e := self.uvc_controls.validate()):
            res["uvc_controls"] = e
        if (e := self.motion_detection.validate()):
            res["motion_detection"] = e
        # ROI gegen Default‑Auflösung
        w, h = self.webcam.get_default_resolution().width, self.webcam.get_default_resolution().height
        if (e := self.motion_detection.get_roi().validate(w, h)):
            res["roi"] = e
        if (e := self.measurement.validate()):
            res["measurement"] = e
        if (e := self.email.validate()):
            res["email"] = e
        if (e := self.gui.validate()):
            res["gui"] = e
        if (e := self.logging.validate()):
            res["logging"] = e
        return res


def _attach_startup_config_warnings(config: AppConfig, warnings: List[str]) -> None:
    setattr(config, _STARTUP_CONFIG_WARNINGS_ATTR, [str(item) for item in warnings if str(item).strip()])


def _get_attached_startup_config_warnings(config: Optional[AppConfig]) -> List[str]:
    if config is None:
        return []
    raw_warnings = getattr(config, _STARTUP_CONFIG_WARNINGS_ATTR, [])
    if not isinstance(raw_warnings, list):
        return []
    return [str(item) for item in raw_warnings if str(item).strip()]


def _clear_attached_startup_config_warnings(config: Optional[AppConfig]) -> None:
    if config is None:
        return
    setattr(config, _STARTUP_CONFIG_WARNINGS_ATTR, [])


def _finalize_loaded_config(
    cfg: AppConfig,
    *,
    config_path: Path,
    defaulting_warnings: List[str],
    startup_warnings: List[str],
) -> AppConfig:
    global logger

    _attach_startup_config_warnings(cfg, startup_warnings)
    try:
        app_logger = cfg.logging.setup_logger("cvd_tracker")
    except Exception as exc:
        raise ConfigLoadError(f"Failed to initialize logging from {config_path}: {exc}") from exc

    logger = app_logger.getChild("config")
    if startup_warnings:
        logger.info("Default config activated for %s after recoverable config fallback", config_path)
    else:
        logger.info("Config loaded: %s", config_path)
    for warning in startup_warnings:
        logger.warning(warning)
    for warning in defaulting_warnings:
        logger.warning(warning)

    cfg.measurement.ensure_save_path()
    return cfg


def _app_config_asdict(config: AppConfig) -> Dict[str, Any]:
    return cast(Dict[str, Any], asdict(cast(Any, config)))


@dataclass
class ConfigImportEntry:
    path: str
    status: str
    current_value: Any = None
    imported_value: Any = None
    reason: str = ""


@dataclass
class ConfigImportPreview:
    source_name: str
    entries: List[ConfigImportEntry] = field(default_factory=list)
    ready_updates: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for entry in self.entries if entry.status == status)


@dataclass
class ConfigImportApplyResult:
    applied_paths: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ConfigRuntimeSyncResult:
    refreshed_targets: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


_CONFIG_IMPORT_STATUS_ORDER = {
    "ready": 0,
    "same": 1,
    "invalid": 2,
    "missing": 3,
    "unknown": 4,
}

_CONFIG_IMPORT_PATHS: Dict[str, List[str]] = {
    "metadata": [
        "metadata.version",
        "metadata.description",
        "metadata.cvd_id",
        "metadata.cvd_name",
        "metadata.released_at",
    ],
    "webcam": [
        "webcam.camera_index",
        "webcam.default_resolution",
        "webcam.fps",
        "webcam.resolution",
        "webcam.preview_fps",
        "webcam.preview_max_width",
        "webcam.preview_jpeg_quality",
    ],
    "uvc_controls": [
        "uvc_controls.brightness",
        "uvc_controls.hue",
        "uvc_controls.contrast",
        "uvc_controls.saturation",
        "uvc_controls.sharpness",
        "uvc_controls.gamma",
        "uvc_controls.white_balance.auto",
        "uvc_controls.white_balance.value",
        "uvc_controls.gain",
        "uvc_controls.backlight_compensation",
        "uvc_controls.exposure.auto",
        "uvc_controls.exposure.value",
    ],
    "motion_detection": [
        "motion_detection.region_of_interest.enabled",
        "motion_detection.region_of_interest.x",
        "motion_detection.region_of_interest.y",
        "motion_detection.region_of_interest.width",
        "motion_detection.region_of_interest.height",
        "motion_detection.region_of_interest.points",
        "motion_detection.sensitivity",
        "motion_detection.background_learning_rate",
        "motion_detection.min_contour_area",
        "motion_detection.frame_skip",
        "motion_detection.processing_max_width",
    ],
    "measurement": [
        "measurement.auto_start",
        "measurement.session_timeout_minutes",
        "measurement.session_timeout_seconds",
        "measurement.save_alert_images",
        "measurement.image_save_path",
        "measurement.image_format",
        "measurement.image_quality",
        "measurement.alert_delay_seconds",
        "measurement.max_alerts_per_session",
        "measurement.alert_check_interval",
        "measurement.alert_cooldown_seconds",
        "measurement.alert_include_snapshot",
        "measurement.inactivity_timeout_minutes",
        "measurement.motion_summary_interval_seconds",
        "measurement.enable_motion_summary_logs",
        "measurement.history_path",
    ],
    "email": [
        "email.website_url",
        "email.website_url_source",
        "email.recipients",
        "email.smtp_server",
        "email.smtp_port",
        "email.sender_email",
        "email.send_as_html",
        "email.templates.alert.subject",
        "email.templates.alert.body",
        "email.templates.test.subject",
        "email.templates.test.body",
        "email.templates.measurement_start.subject",
        "email.templates.measurement_start.body",
        "email.templates.measurement_end.subject",
        "email.templates.measurement_end.body",
        "email.templates.measurement_stop.subject",
        "email.templates.measurement_stop.body",
        "email.groups",
        "email.active_groups",
        "email.static_recipients",
        "email.explicit_targeting",
        "email.notifications",
        "email.group_prefs",
        "email.recipient_prefs",
    ],
    "gui": [
        "gui.title",
        "gui.host",
        "gui.port",
        "gui.reverse_proxy_enabled",
        "gui.forwarded_allow_ips",
        "gui.root_path",
        "gui.session_cookie_https_only",
        "gui.auto_open_browser",
        "gui.update_interval_ms",
        "gui.status_refresh_interval_ms",
    ],
    "logging": [
        "logging.level",
        "logging.file",
        "logging.max_file_size_mb",
        "logging.backup_count",
        "logging.console_output",
    ],
}

_CONFIG_IMPORT_PATH_ORDER = {
    path: index
    for index, path in enumerate(
        [item for section_paths in _CONFIG_IMPORT_PATHS.values() for item in section_paths]
    )
}
_CONFIG_IMPORT_SENTINEL = object()


def _get_config_value_by_path(data: Dict[str, Any], path: str, default: Any = _CONFIG_IMPORT_SENTINEL) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_config_value_by_path(target: Any, path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        if is_dataclass(current):
            current = getattr(current, part)
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise TypeError(f"Cannot traverse config path '{path}' at '{part}'")
    leaf = parts[-1]
    copied = deepcopy(value)
    if is_dataclass(current):
        setattr(current, leaf, copied)
    elif isinstance(current, dict):
        current[leaf] = copied
    else:
        raise TypeError(f"Cannot assign config path '{path}'")


def _sync_config_in_place(target: Any, source: Any) -> Any:
    if is_dataclass(target) and is_dataclass(source):
        for dc_field in fields(target):
            current_value = getattr(target, dc_field.name)
            new_value = getattr(source, dc_field.name)
            synced = _sync_config_in_place(current_value, new_value)
            if synced is not None:
                setattr(target, dc_field.name, synced)
        return None
    if isinstance(target, dict) and isinstance(source, dict):
        for key in list(target.keys()):
            if key not in source:
                del target[key]
        for key, new_value in source.items():
            if key in target:
                synced = _sync_config_in_place(target[key], new_value)
                if synced is not None:
                    target[key] = synced
            else:
                target[key] = deepcopy(new_value)
        return None
    if isinstance(target, list) and isinstance(source, list):
        target[:] = deepcopy(source)
        return None
    return deepcopy(source)


def _flatten_validation_errors(errors: Dict[str, List[str]]) -> set[str]:
    return {
        f"{section}:{message}"
        for section, messages in errors.items()
        for message in messages
    }


def _sort_import_entries(entries: List[ConfigImportEntry]) -> List[ConfigImportEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            _CONFIG_IMPORT_PATH_ORDER.get(entry.path, 10_000),
            _CONFIG_IMPORT_STATUS_ORDER.get(entry.status, 99),
            entry.path,
        ),
    )


def _paths_include_prefix(applied_paths: Optional[List[str]], *prefixes: str) -> bool:
    if applied_paths is None:
        return True
    for path in applied_paths:
        for prefix in prefixes:
            if path == prefix or path.startswith(f"{prefix}."):
                return True
    return False


def _coerce_string(value: Any, *, allow_empty: bool = True) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise ValueError("must be a string")
    text = value if isinstance(value, str) else str(value)
    if not allow_empty and not text.strip():
        raise ValueError("must not be empty")
    return text


def _coerce_strict_string(value: Any, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if not allow_empty and not value.strip():
        raise ValueError("must not be empty")
    return value


def _normalize_website_url_source(value: Any) -> str:
    normalized = _coerce_strict_string(value, allow_empty=False).strip().lower()
    if normalized not in EmailConfig.WEBSITE_URL_SOURCES:
        raise ValueError(f"website_url_source must be one of {list(EmailConfig.WEBSITE_URL_SOURCES)}")
    return normalized


def _normalize_absolute_http_url_string(value: Any) -> str:
    normalized = _coerce_strict_string(value, allow_empty=False).strip()
    if not _is_absolute_http_url(normalized):
        raise ValueError("must be an absolute http(s) URL")
    return normalized


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("must be a boolean")


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError as exc:
            raise ValueError("must be an integer") from exc
        if parsed.is_integer():
            return int(parsed)
    raise ValueError("must be an integer")


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError("must be a number") from exc
    raise ValueError("must be a number")


def _validate_min(value: float, minimum: float, *, label: str) -> Optional[str]:
    if value < minimum:
        return f"{label} must be >= {minimum}"
    return None


def _validate_positive(value: float, *, label: str) -> Optional[str]:
    if value <= 0:
        return f"{label} must be > 0"
    return None


def _validate_range(value: float, minimum: float, maximum: float, *, label: str) -> Optional[str]:
    if value < minimum or value > maximum:
        return f"{label} must be within [{minimum}, {maximum}]"
    return None


def _validate_absolute_http_url(value: str, *, label: str) -> Optional[str]:
    if not value:
        return None
    if not _is_absolute_http_url(value):
        return f"{label} must be an absolute http(s) URL"
    return None


def _normalize_root_path(value: Any) -> str:
    text = _coerce_string(value).strip()
    if not text or text == "/":
        return ""
    if not text.startswith("/"):
        raise ValueError("root_path must start with '/' or be empty")
    normalized = text.rstrip("/")
    if not normalized:
        return ""
    return normalized


def _normalize_loaded_scalar(
    value: Any,
    *,
    label: str,
    default: Any,
    converter: Callable[[Any], Any],
    logger: logging.Logger,
    validator: Optional[Callable[[Any], Optional[str]]] = None,
) -> Any:
    try:
        normalized = converter(value)
    except Exception as exc:
        raise ValueError(f"{label}: {exc}") from exc
    if validator is not None:
        validation_error = validator(normalized)
        if validation_error:
            raise ValueError(f"{label}: {validation_error}")
    return normalized


def _normalize_loaded_gui_data(gui_data: Any, logger: logging.Logger) -> Dict[str, Any]:
    if not isinstance(gui_data, dict):
        raise ValueError(f"gui section must be a mapping, got {type(gui_data).__name__}")
    return {
        "title": _normalize_loaded_scalar(
            gui_data.get("title", "CVD-Tracker"),
            label="gui.title",
            default="CVD-Tracker",
            converter=_coerce_string,
            logger=logger,
        ),
        "host": _normalize_loaded_scalar(
            gui_data.get("host", "localhost"),
            label="gui.host",
            default="localhost",
            converter=lambda value: _coerce_strict_string(value, allow_empty=False).strip(),
            logger=logger,
        ),
        "port": _normalize_loaded_scalar(
            gui_data.get("port", 8080),
            label="gui.port",
            default=8080,
            converter=_coerce_int,
            logger=logger,
            validator=lambda value: _validate_range(value, 1, 65535, label="port"),
        ),
        "auto_open_browser": _normalize_loaded_scalar(
            gui_data.get("auto_open_browser", False),
            label="gui.auto_open_browser",
            default=False,
            converter=_coerce_bool,
            logger=logger,
        ),
        "update_interval_ms": _normalize_loaded_scalar(
            gui_data.get("update_interval_ms", 100),
            label="gui.update_interval_ms",
            default=100,
            converter=_coerce_int,
            logger=logger,
            validator=lambda value: _validate_min(value, 1, label="update_interval_ms"),
        ),
        "status_refresh_interval_ms": _normalize_loaded_scalar(
            gui_data.get("status_refresh_interval_ms", gui_data.get("update_interval_ms", 1000)),
            label="gui.status_refresh_interval_ms",
            default=1000,
            converter=_coerce_int,
            logger=logger,
            validator=lambda value: _validate_min(value, 1, label="status_refresh_interval_ms"),
        ),
        "reverse_proxy_enabled": _normalize_loaded_scalar(
            gui_data.get("reverse_proxy_enabled", False),
            label="gui.reverse_proxy_enabled",
            default=False,
            converter=_coerce_bool,
            logger=logger,
        ),
        "forwarded_allow_ips": _normalize_loaded_scalar(
            gui_data.get("forwarded_allow_ips", "127.0.0.1"),
            label="gui.forwarded_allow_ips",
            default="127.0.0.1",
            converter=lambda value: _coerce_strict_string(value, allow_empty=False).strip(),
            logger=logger,
        ),
        "root_path": _normalize_loaded_scalar(
            gui_data.get("root_path", ""),
            label="gui.root_path",
            default="",
            converter=_normalize_root_path,
            logger=logger,
        ),
        "session_cookie_https_only": _normalize_loaded_scalar(
            gui_data.get("session_cookie_https_only", False),
            label="gui.session_cookie_https_only",
            default=False,
            converter=_coerce_bool,
            logger=logger,
        ),
    }


def _normalize_loaded_email_data(
    email_data: Any,
    logger: logging.Logger,
    *,
    default_email_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if default_email_data is None:
        resolved_default_email_data = cast(
            Dict[str, Any],
            _app_config_asdict(_create_default_config(log_creation=False))["email"],
        )
    else:
        resolved_default_email_data = default_email_data
    if isinstance(email_data, dict):
        normalized_source_email_data = email_data
    else:
        raise ValueError(f"email section must be a mapping, got {type(email_data).__name__}")
    normalized_email_data = deepcopy(resolved_default_email_data)
    normalized_email_data.update(normalized_source_email_data)
    normalized_email_data["website_url"] = _normalize_loaded_scalar(
        normalized_source_email_data.get(
            "website_url",
            resolved_default_email_data.get("website_url", "http://localhost:8080/"),
        ),
        label="email.website_url",
        default=resolved_default_email_data.get("website_url", "http://localhost:8080/"),
        converter=_normalize_absolute_http_url_string,
        logger=logger,
    )
    normalized_email_data["website_url_source"] = _normalize_loaded_scalar(
        normalized_source_email_data.get(
            "website_url_source",
            resolved_default_email_data.get("website_url_source", EmailConfig.WEBSITE_URL_SOURCE_RUNTIME_PERSIST),
        ),
        label="email.website_url_source",
        default=resolved_default_email_data.get("website_url_source", EmailConfig.WEBSITE_URL_SOURCE_RUNTIME_PERSIST),
        converter=_normalize_website_url_source,
        logger=logger,
    )
    return normalized_email_data


def _normalize_resolution_dict(value: Any, *, label: str) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    unknown = sorted(key for key in value.keys() if key not in {"width", "height"})
    if unknown:
        raise ValueError(f"{label} contains unsupported keys: {unknown}")
    if "width" not in value or "height" not in value:
        raise ValueError(f"{label} must contain width and height")
    width = _coerce_int(value["width"])
    height = _coerce_int(value["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} width and height must be > 0")
    return {"width": width, "height": height}


def _normalize_resolution_list(value: Any) -> List[Dict[str, int]]:
    if not isinstance(value, list):
        raise ValueError("resolution must be a list")
    result: List[Dict[str, int]] = []
    for index, item in enumerate(value):
        result.append(_normalize_resolution_dict(item, label=f"resolution[{index}]"))
    if not result:
        raise ValueError("resolution must not be empty")
    return result


def _normalize_roi_points(value: Any) -> List[List[int]]:
    if not isinstance(value, list):
        raise ValueError("points must be a list")
    normalized: List[List[int]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"points[{index}] must contain exactly two coordinates")
        px = _coerce_int(point[0])
        py = _coerce_int(point[1])
        normalized.append([px, py])
    return normalized


def _normalize_email_list(value: Any, *, label: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of email addresses")
    result: List[str] = []
    for index, item in enumerate(value):
        email = _coerce_string(item, allow_empty=False).strip()
        if not EmailConfig.EMAIL_RE.match(email):
            raise ValueError(f"{label}[{index}] is not a valid email address")
        result.append(email)
    return result


def _normalize_groups(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        raise ValueError("groups must be a mapping of group names to email lists")
    result: Dict[str, List[str]] = {}
    for group_name, members in value.items():
        normalized_name = EmailConfig.ensure_group_name_allowed(group_name, context="groups")
        result[normalized_name] = _normalize_email_list(members, label=f"groups['{normalized_name}']")
    return result


def _normalize_active_groups(value: Any, *, known_groups: Dict[str, List[str]]) -> List[str]:
    if not isinstance(value, list):
        raise ValueError("active_groups must be a list of group names")
    result: List[str] = []
    unknown_groups: List[str] = []
    for item in value:
        name = EmailConfig.ensure_group_name_allowed(item, context="active_groups")
        result.append(name)
        if name not in known_groups:
            unknown_groups.append(name)
    if unknown_groups:
        raise ValueError(f"unknown groups referenced: {sorted(set(unknown_groups))}")
    return result


def _normalize_notifications(value: Any) -> Dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("notifications must be a mapping")
    result: Dict[str, bool] = {}
    for key, item in value.items():
        normalized_key = EmailConfig._normalize_event_pref_key(key, context="notifications")
        result[normalized_key] = _coerce_bool(item)
    return result


def _normalize_explicit_targeting(value: Any) -> bool:
    return _coerce_bool(value)


def _normalize_group_prefs(value: Any, *, known_groups: Dict[str, List[str]]) -> Dict[str, Dict[str, bool]]:
    if not isinstance(value, dict):
        raise ValueError("group_prefs must be a mapping")
    result: Dict[str, Dict[str, bool]] = {}
    unknown_groups: List[str] = []
    for group_name, prefs in value.items():
        normalized_name = EmailConfig.ensure_group_name_allowed(group_name, context="group_prefs")
        if normalized_name not in known_groups:
            unknown_groups.append(normalized_name)
        if not isinstance(prefs, dict):
            raise ValueError(f"group_prefs['{normalized_name}'] must be a mapping")
        normalized_prefs: Dict[str, bool] = {}
        for pref_name, pref_value in prefs.items():
            normalized_key = EmailConfig._normalize_event_pref_key(
                pref_name,
                context=f"group_prefs['{normalized_name}']",
            )
            normalized_prefs[normalized_key] = _coerce_bool(pref_value)
        result[normalized_name] = normalized_prefs
    if unknown_groups:
        raise ValueError(f"unknown groups referenced in group_prefs: {sorted(set(unknown_groups))}")
    return result


def _normalize_recipient_prefs(value: Any) -> Dict[str, Dict[str, bool]]:
    if not isinstance(value, dict):
        raise ValueError("recipient_prefs must be a mapping")
    result: Dict[str, Dict[str, bool]] = {}
    for email, prefs in value.items():
        normalized_email = _coerce_string(email, allow_empty=False).strip()
        if not EmailConfig.EMAIL_RE.match(normalized_email):
            raise ValueError(f"recipient_prefs contains invalid email key: {normalized_email!r}")
        if not isinstance(prefs, dict):
            raise ValueError(f"recipient_prefs['{normalized_email}'] must be a mapping")
        normalized_prefs: Dict[str, bool] = {}
        for pref_name, pref_value in prefs.items():
            normalized_key = EmailConfig._normalize_event_pref_key(
                pref_name,
                context=f"recipient_prefs['{normalized_email}']",
            )
            normalized_prefs[normalized_key] = _coerce_bool(pref_value)
        result[normalized_email] = normalized_prefs
    return result


def _normalize_logging_level(value: Any) -> str:
    level = _coerce_string(value, allow_empty=False).upper()
    if not LogLevel.is_valid(level):
        valid_levels = [member.value for member in LogLevel]
        raise ValueError(f"level must be one of {valid_levels}")
    return level


def _normalize_image_format(value: Any) -> str:
    image_format = _coerce_string(value, allow_empty=False).lower()
    if image_format not in {"jpg", "jpeg", "png"}:
        raise ValueError("image_format must be one of ['jpg', 'jpeg', 'png']")
    return image_format


class _ConfigImportCollector:
    def __init__(self, current_config: AppConfig) -> None:
        self.current_raw = _app_config_asdict(current_config)
        self.entries: List[ConfigImportEntry] = []
        self.ready_updates: Dict[str, Any] = {}

    def _current_value(self, path: str) -> Any:
        value = _get_config_value_by_path(self.current_raw, path)
        return None if value is _CONFIG_IMPORT_SENTINEL else deepcopy(value)

    def add_valid(self, path: str, imported_value: Any, normalized_value: Any, reason: str = "") -> None:
        current_value = self._current_value(path)
        same_value = current_value == normalized_value
        self.entries.append(
            ConfigImportEntry(
                path=path,
                status="same" if same_value else "ready",
                current_value=current_value,
                imported_value=deepcopy(imported_value),
                reason=reason if reason else ("already matches current config" if same_value else ""),
            )
        )
        if not same_value:
            self.ready_updates[path] = deepcopy(normalized_value)

    def add_invalid(self, path: str, imported_value: Any, reason: str) -> None:
        self.entries.append(
            ConfigImportEntry(
                path=path,
                status="invalid",
                current_value=self._current_value(path),
                imported_value=deepcopy(imported_value),
                reason=reason,
            )
        )

    def add_missing(self, path: str, reason: str = "not present in imported config") -> None:
        self.entries.append(
            ConfigImportEntry(
                path=path,
                status="missing",
                current_value=self._current_value(path),
                imported_value=None,
                reason=reason,
            )
        )

    def add_unknown(self, path: str, imported_value: Any, reason: str = "setting is not supported by this version") -> None:
        self.entries.append(
            ConfigImportEntry(
                path=path,
                status="unknown",
                current_value=None,
                imported_value=deepcopy(imported_value),
                reason=reason,
            )
        )


def _mark_missing_paths(collector: _ConfigImportCollector, expected_paths: List[str], seen_paths: set[str]) -> None:
    for path in expected_paths:
        if path not in seen_paths:
            collector.add_missing(path)


def _process_scalar_field(
    collector: _ConfigImportCollector,
    section_data: Dict[str, Any],
    *,
    key: str,
    path: str,
    seen_paths: set[str],
    converter: Any,
    validator: Optional[Any] = None,
) -> Optional[Any]:
    if key not in section_data:
        return None
    seen_paths.add(path)
    raw_value = section_data[key]
    try:
        normalized = converter(raw_value)
        if validator is not None:
            validation_error = validator(normalized)
            if validation_error:
                raise ValueError(validation_error)
    except ValueError as exc:
        collector.add_invalid(path, raw_value, str(exc))
        return None
    collector.add_valid(path, raw_value, normalized)
    return normalized


def _resolve_section_mapping(
    imported_data: Dict[str, Any],
    section_name: str,
    collector: _ConfigImportCollector,
) -> Optional[Dict[str, Any]]:
    expected_paths = _CONFIG_IMPORT_PATHS[section_name]
    if section_name not in imported_data:
        for path in expected_paths:
            collector.add_missing(path)
        return None
    section_data = imported_data[section_name]
    if not isinstance(section_data, dict):
        for path in expected_paths:
            collector.add_invalid(path, section_data, f"'{section_name}' must be a mapping")
        return None
    return section_data


def _analyze_metadata_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "metadata", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {"version", "description", "cvd_id", "cvd_name", "released_at"}:
            collector.add_unknown(f"metadata.{key}", value)
    _process_scalar_field(collector, section_data, key="version", path="metadata.version", seen_paths=seen_paths, converter=_coerce_string)
    _process_scalar_field(collector, section_data, key="description", path="metadata.description", seen_paths=seen_paths, converter=_coerce_string)
    _process_scalar_field(collector, section_data, key="cvd_id", path="metadata.cvd_id", seen_paths=seen_paths, converter=_coerce_int)
    _process_scalar_field(collector, section_data, key="cvd_name", path="metadata.cvd_name", seen_paths=seen_paths, converter=_coerce_string)
    _process_scalar_field(collector, section_data, key="released_at", path="metadata.released_at", seen_paths=seen_paths, converter=_coerce_string)
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["metadata"], seen_paths)


def _analyze_webcam_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "webcam", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {
            "camera_index",
            "default_resolution",
            "fps",
            "preview_fps",
            "preview_max_width",
            "preview_jpeg_quality",
            "resolution",
        }:
            collector.add_unknown(f"webcam.{key}", value)
    _process_scalar_field(
        collector,
        section_data,
        key="camera_index",
        path="webcam.camera_index",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 0, label="camera_index"),
    )
    if "default_resolution" in section_data:
        path = "webcam.default_resolution"
        seen_paths.add(path)
        raw_value = section_data["default_resolution"]
        try:
            normalized = _normalize_resolution_dict(raw_value, label="default_resolution")
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)
    if "fps" in section_data:
        _process_scalar_field(
            collector,
            section_data,
            key="fps",
            path="webcam.fps",
            seen_paths=seen_paths,
            converter=_coerce_int,
            validator=lambda value: _validate_min(value, 1, label="fps"),
        )
    _process_scalar_field(
        collector,
        section_data,
        key="preview_fps",
        path="webcam.preview_fps",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="preview_fps"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="preview_max_width",
        path="webcam.preview_max_width",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="preview_max_width"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="preview_jpeg_quality",
        path="webcam.preview_jpeg_quality",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_range(value, 1, 100, label="preview_jpeg_quality"),
    )
    if "resolution" in section_data:
        path = "webcam.resolution"
        seen_paths.add(path)
        raw_value = section_data["resolution"]
        try:
            normalized = _normalize_resolution_list(raw_value)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["webcam"], seen_paths)


def _analyze_uvc_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "uvc_controls", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {
            "brightness",
            "hue",
            "contrast",
            "saturation",
            "sharpness",
            "gamma",
            "white_balance",
            "gain",
            "backlight_compensation",
            "exposure",
        }:
            collector.add_unknown(f"uvc_controls.{key}", value)
    scalar_specs = [
        ("brightness", "uvc_controls.brightness", -64, 64),
        ("hue", "uvc_controls.hue", -180, 180),
        ("contrast", "uvc_controls.contrast", 0, 64),
        ("saturation", "uvc_controls.saturation", 0, 128),
        ("sharpness", "uvc_controls.sharpness", 0, 14),
        ("gamma", "uvc_controls.gamma", 72, 500),
        ("gain", "uvc_controls.gain", 0, 100),
        ("backlight_compensation", "uvc_controls.backlight_compensation", 0, 160),
    ]
    for key, path, minimum, maximum in scalar_specs:
        _process_scalar_field(
            collector,
            section_data,
            key=key,
            path=path,
            seen_paths=seen_paths,
            converter=_coerce_int,
            validator=lambda value, lo=minimum, hi=maximum, name=key: _validate_range(value, lo, hi, label=name),
        )

    if "white_balance" in section_data:
        white_balance = section_data["white_balance"]
        expected_paths = ["uvc_controls.white_balance.auto", "uvc_controls.white_balance.value"]
        if not isinstance(white_balance, dict):
            for path in expected_paths:
                seen_paths.add(path)
                collector.add_invalid(path, white_balance, "white_balance must be a mapping")
        else:
            for key, value in white_balance.items():
                if key not in {"auto", "value"}:
                    collector.add_unknown(f"uvc_controls.white_balance.{key}", value)
            _process_scalar_field(
                collector,
                white_balance,
                key="auto",
                path="uvc_controls.white_balance.auto",
                seen_paths=seen_paths,
                converter=_coerce_bool,
            )
            _process_scalar_field(
                collector,
                white_balance,
                key="value",
                path="uvc_controls.white_balance.value",
                seen_paths=seen_paths,
                converter=_coerce_int,
            )

    if "exposure" in section_data:
        exposure = section_data["exposure"]
        expected_paths = ["uvc_controls.exposure.auto", "uvc_controls.exposure.value"]
        if not isinstance(exposure, dict):
            for path in expected_paths:
                seen_paths.add(path)
                collector.add_invalid(path, exposure, "exposure must be a mapping")
        else:
            for key, value in exposure.items():
                if key not in {"auto", "value"}:
                    collector.add_unknown(f"uvc_controls.exposure.{key}", value)
            _process_scalar_field(
                collector,
                exposure,
                key="auto",
                path="uvc_controls.exposure.auto",
                seen_paths=seen_paths,
                converter=_coerce_bool,
            )
            _process_scalar_field(
                collector,
                exposure,
                key="value",
                path="uvc_controls.exposure.value",
                seen_paths=seen_paths,
                converter=_coerce_int,
            )

    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["uvc_controls"], seen_paths)


def _analyze_motion_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "motion_detection", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {
            "region_of_interest",
            "sensitivity",
            "background_learning_rate",
            "min_contour_area",
            "frame_skip",
            "processing_max_width",
        }:
            collector.add_unknown(f"motion_detection.{key}", value)

    roi_expected = [
        "motion_detection.region_of_interest.enabled",
        "motion_detection.region_of_interest.x",
        "motion_detection.region_of_interest.y",
        "motion_detection.region_of_interest.width",
        "motion_detection.region_of_interest.height",
        "motion_detection.region_of_interest.points",
    ]
    if "region_of_interest" not in section_data:
        for path in roi_expected:
            collector.add_missing(path)
            seen_paths.add(path)
    else:
        roi_value = section_data["region_of_interest"]
        if not isinstance(roi_value, dict):
            for path in roi_expected:
                seen_paths.add(path)
                collector.add_invalid(path, roi_value, "region_of_interest must be a mapping")
        else:
            for key, value in roi_value.items():
                if key not in {"enabled", "x", "y", "width", "height", "points"}:
                    collector.add_unknown(f"motion_detection.region_of_interest.{key}", value)
            roi_updates: Dict[str, Any] = {}
            imported_roi_paths: List[str] = []
            invalid_roi_paths: set[str] = set()
            roi_path_keys = {
                "enabled": ("motion_detection.region_of_interest.enabled", _coerce_bool),
                "x": ("motion_detection.region_of_interest.x", _coerce_int),
                "y": ("motion_detection.region_of_interest.y", _coerce_int),
                "width": ("motion_detection.region_of_interest.width", _coerce_int),
                "height": ("motion_detection.region_of_interest.height", _coerce_int),
                "points": ("motion_detection.region_of_interest.points", _normalize_roi_points),
            }
            for key, (path, converter) in roi_path_keys.items():
                if key not in roi_value:
                    continue
                seen_paths.add(path)
                imported_roi_paths.append(path)
                raw_value = roi_value[key]
                try:
                    normalized = converter(raw_value)
                except ValueError as exc:
                    collector.add_invalid(path, raw_value, str(exc))
                    invalid_roi_paths.add(path)
                else:
                    roi_updates[key] = normalized
            candidate_roi = deepcopy(_get_config_value_by_path(collector.current_raw, "motion_detection.region_of_interest", {}))
            if not isinstance(candidate_roi, dict):
                candidate_roi = {}
            for key, value in roi_updates.items():
                candidate_roi[key] = deepcopy(value)
            current_resolution = deepcopy(_get_config_value_by_path(collector.current_raw, "webcam.default_resolution", {"width": 0, "height": 0}))
            candidate_resolution = deepcopy(collector.ready_updates.get("webcam.default_resolution", current_resolution))
            frame_width = candidate_resolution.get("width", 0) if isinstance(candidate_resolution, dict) else 0
            frame_height = candidate_resolution.get("height", 0) if isinstance(candidate_resolution, dict) else 0
            try:
                roi_errors = ROI(**candidate_roi).validate(frame_width, frame_height)
            except Exception as exc:
                roi_errors = [str(exc)]
            if roi_errors:
                error_text = "; ".join(roi_errors)
                for path in imported_roi_paths:
                    if path not in invalid_roi_paths:
                        raw_key = path.split(".")[-1]
                        collector.add_invalid(path, roi_value.get(raw_key), error_text)
                        invalid_roi_paths.add(path)
            for key, (path, _) in roi_path_keys.items():
                if path in imported_roi_paths and path not in invalid_roi_paths:
                    collector.add_valid(path, roi_value.get(key), roi_updates[key])
            _mark_missing_paths(collector, roi_expected, seen_paths)

    _process_scalar_field(
        collector,
        section_data,
        key="sensitivity",
        path="motion_detection.sensitivity",
        seen_paths=seen_paths,
        converter=_coerce_float,
        validator=lambda value: _validate_range(value, 0.001, 1.0, label="sensitivity"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="background_learning_rate",
        path="motion_detection.background_learning_rate",
        seen_paths=seen_paths,
        converter=_coerce_float,
        validator=lambda value: _validate_range(value, 0.001, 1.0, label="background_learning_rate"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="min_contour_area",
        path="motion_detection.min_contour_area",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="min_contour_area"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="frame_skip",
        path="motion_detection.frame_skip",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="frame_skip"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="processing_max_width",
        path="motion_detection.processing_max_width",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="processing_max_width"),
    )
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["motion_detection"], seen_paths)


def _analyze_measurement_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "measurement", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    allowed_keys = {
        "auto_start",
        "session_timeout_minutes",
        "session_timeout_seconds",
        "save_alert_images",
        "image_save_path",
        "image_format",
        "image_quality",
        "alert_delay_seconds",
        "max_alerts_per_session",
        "alert_check_interval",
        "alert_cooldown_seconds",
        "alert_include_snapshot",
        "inactivity_timeout_minutes",
        "motion_summary_interval_seconds",
        "enable_motion_summary_logs",
        "history_path",
    }
    for key, value in section_data.items():
        if key not in allowed_keys:
            collector.add_unknown(f"measurement.{key}", value)

    _process_scalar_field(collector, section_data, key="auto_start", path="measurement.auto_start", seen_paths=seen_paths, converter=_coerce_bool)
    _process_scalar_field(
        collector,
        section_data,
        key="session_timeout_minutes",
        path="measurement.session_timeout_minutes",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 0, label="session_timeout_minutes"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="session_timeout_seconds",
        path="measurement.session_timeout_seconds",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 0, label="session_timeout_seconds"),
    )
    _process_scalar_field(collector, section_data, key="save_alert_images", path="measurement.save_alert_images", seen_paths=seen_paths, converter=_coerce_bool)
    _process_scalar_field(
        collector,
        section_data,
        key="image_save_path",
        path="measurement.image_save_path",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_string(value, allow_empty=False),
    )
    image_format = _process_scalar_field(
        collector,
        section_data,
        key="image_format",
        path="measurement.image_format",
        seen_paths=seen_paths,
        converter=_normalize_image_format,
    )
    current_image_format = _get_config_value_by_path(collector.current_raw, "measurement.image_format", "jpg")
    candidate_image_format = image_format if image_format is not None else str(current_image_format).lower()
    _process_scalar_field(
        collector,
        section_data,
        key="image_quality",
        path="measurement.image_quality",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: (
            None
            if candidate_image_format not in {"jpg", "jpeg"}
            else _validate_range(value, 1, 100, label="image_quality")
        ),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="alert_delay_seconds",
        path="measurement.alert_delay_seconds",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 30, label="alert_delay_seconds"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="max_alerts_per_session",
        path="measurement.max_alerts_per_session",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="max_alerts_per_session"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="alert_check_interval",
        path="measurement.alert_check_interval",
        seen_paths=seen_paths,
        converter=_coerce_float,
        validator=lambda value: _validate_positive(value, label="alert_check_interval"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="alert_cooldown_seconds",
        path="measurement.alert_cooldown_seconds",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 0, label="alert_cooldown_seconds"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="alert_include_snapshot",
        path="measurement.alert_include_snapshot",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )
    _process_scalar_field(
        collector,
        section_data,
        key="inactivity_timeout_minutes",
        path="measurement.inactivity_timeout_minutes",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 0, label="inactivity_timeout_minutes"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="motion_summary_interval_seconds",
        path="measurement.motion_summary_interval_seconds",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 5, label="motion_summary_interval_seconds"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="enable_motion_summary_logs",
        path="measurement.enable_motion_summary_logs",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )
    _process_scalar_field(
        collector,
        section_data,
        key="history_path",
        path="measurement.history_path",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_string(value, allow_empty=False),
    )
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["measurement"], seen_paths)


def _analyze_email_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "email", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    allowed_keys = {
        "website_url",
        "website_url_source",
        "recipients",
        "smtp_server",
        "smtp_port",
        "sender_email",
        "send_as_html",
        "templates",
        "groups",
        "active_groups",
        "static_recipients",
        "explicit_targeting",
        "notifications",
        "group_prefs",
        "recipient_prefs",
    }
    for key, value in section_data.items():
        if key not in allowed_keys:
            collector.add_unknown(f"email.{key}", value)

    _process_scalar_field(
        collector,
        section_data,
        key="website_url",
        path="email.website_url",
        seen_paths=seen_paths,
        converter=_normalize_absolute_http_url_string,
    )
    _process_scalar_field(
        collector,
        section_data,
        key="website_url_source",
        path="email.website_url_source",
        seen_paths=seen_paths,
        converter=_normalize_website_url_source,
    )
    if "recipients" in section_data:
        path = "email.recipients"
        seen_paths.add(path)
        raw_value = section_data["recipients"]
        try:
            normalized = _normalize_email_list(raw_value, label="recipients")
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)
    _process_scalar_field(
        collector,
        section_data,
        key="smtp_server",
        path="email.smtp_server",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_string(value, allow_empty=False),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="smtp_port",
        path="email.smtp_port",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_range(value, 1, 65535, label="smtp_port"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="sender_email",
        path="email.sender_email",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_string(value, allow_empty=False).strip(),
        validator=lambda value: None if EmailConfig.EMAIL_RE.match(value) else "sender_email must be a valid email address",
    )
    _process_scalar_field(
        collector,
        section_data,
        key="send_as_html",
        path="email.send_as_html",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )

    template_paths = [
        "email.templates.alert.subject",
        "email.templates.alert.body",
        "email.templates.test.subject",
        "email.templates.test.body",
        "email.templates.measurement_start.subject",
        "email.templates.measurement_start.body",
        "email.templates.measurement_end.subject",
        "email.templates.measurement_end.body",
        "email.templates.measurement_stop.subject",
        "email.templates.measurement_stop.body",
    ]
    if "templates" not in section_data:
        for path in template_paths:
            collector.add_missing(path)
            seen_paths.add(path)
    else:
        templates = section_data["templates"]
        if not isinstance(templates, dict):
            for path in template_paths:
                collector.add_invalid(path, templates, "templates must be a mapping")
                seen_paths.add(path)
        else:
            allowed_templates = {
                "alert",
                "test",
                "measurement_start",
                "measurement_end",
                "measurement_stop",
            }
            for key, value in templates.items():
                if key not in allowed_templates:
                    collector.add_unknown(f"email.templates.{key}", value)
            for template_name in allowed_templates:
                template_value = templates.get(template_name, _CONFIG_IMPORT_SENTINEL)
                subject_path = f"email.templates.{template_name}.subject"
                body_path = f"email.templates.{template_name}.body"
                if template_value is _CONFIG_IMPORT_SENTINEL:
                    collector.add_missing(subject_path)
                    collector.add_missing(body_path)
                    seen_paths.add(subject_path)
                    seen_paths.add(body_path)
                    continue
                if not isinstance(template_value, dict):
                    collector.add_invalid(subject_path, template_value, f"template '{template_name}' must be a mapping")
                    collector.add_invalid(body_path, template_value, f"template '{template_name}' must be a mapping")
                    seen_paths.add(subject_path)
                    seen_paths.add(body_path)
                    continue
                for key, value in template_value.items():
                    if key not in {"subject", "body"}:
                        collector.add_unknown(f"email.templates.{template_name}.{key}", value)
                _process_scalar_field(
                    collector,
                    template_value,
                    key="subject",
                    path=subject_path,
                    seen_paths=seen_paths,
                    converter=_coerce_string,
                )
                _process_scalar_field(
                    collector,
                    template_value,
                    key="body",
                    path=body_path,
                    seen_paths=seen_paths,
                    converter=_coerce_string,
                )

    if "groups" in section_data:
        path = "email.groups"
        seen_paths.add(path)
        raw_value = section_data["groups"]
        try:
            normalized = _normalize_groups(raw_value)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    current_groups = _get_config_value_by_path(collector.current_raw, "email.groups", {})
    candidate_groups = collector.ready_updates.get("email.groups", current_groups)
    if not isinstance(candidate_groups, dict):
        candidate_groups = {}

    if "active_groups" in section_data:
        path = "email.active_groups"
        seen_paths.add(path)
        raw_value = section_data["active_groups"]
        try:
            normalized = _normalize_active_groups(raw_value, known_groups=candidate_groups)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    if "static_recipients" in section_data:
        path = "email.static_recipients"
        seen_paths.add(path)
        raw_value = section_data["static_recipients"]
        try:
            normalized = _normalize_email_list(raw_value, label="static_recipients")
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    _process_scalar_field(
        collector,
        section_data,
        key="explicit_targeting",
        path="email.explicit_targeting",
        seen_paths=seen_paths,
        converter=_normalize_explicit_targeting,
    )

    if "notifications" in section_data:
        path = "email.notifications"
        seen_paths.add(path)
        raw_value = section_data["notifications"]
        try:
            normalized = _normalize_notifications(raw_value)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    if "group_prefs" in section_data:
        path = "email.group_prefs"
        seen_paths.add(path)
        raw_value = section_data["group_prefs"]
        try:
            normalized = _normalize_group_prefs(raw_value, known_groups=candidate_groups)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    if "recipient_prefs" in section_data:
        path = "email.recipient_prefs"
        seen_paths.add(path)
        raw_value = section_data["recipient_prefs"]
        try:
            normalized = _normalize_recipient_prefs(raw_value)
        except ValueError as exc:
            collector.add_invalid(path, raw_value, str(exc))
        else:
            collector.add_valid(path, raw_value, normalized)

    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["email"], seen_paths)


def _analyze_gui_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "gui", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {
            "title",
            "host",
            "port",
            "reverse_proxy_enabled",
            "forwarded_allow_ips",
            "root_path",
            "session_cookie_https_only",
            "auto_open_browser",
            "update_interval_ms",
            "status_refresh_interval_ms",
        }:
            collector.add_unknown(f"gui.{key}", value)
    _process_scalar_field(collector, section_data, key="title", path="gui.title", seen_paths=seen_paths, converter=_coerce_string)
    _process_scalar_field(
        collector,
        section_data,
        key="host",
        path="gui.host",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_strict_string(value, allow_empty=False),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="port",
        path="gui.port",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_range(value, 1, 65535, label="port"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="reverse_proxy_enabled",
        path="gui.reverse_proxy_enabled",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )
    _process_scalar_field(
        collector,
        section_data,
        key="forwarded_allow_ips",
        path="gui.forwarded_allow_ips",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_strict_string(value, allow_empty=False).strip(),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="root_path",
        path="gui.root_path",
        seen_paths=seen_paths,
        converter=_normalize_root_path,
    )
    _process_scalar_field(
        collector,
        section_data,
        key="session_cookie_https_only",
        path="gui.session_cookie_https_only",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )
    _process_scalar_field(collector, section_data, key="auto_open_browser", path="gui.auto_open_browser", seen_paths=seen_paths, converter=_coerce_bool)
    _process_scalar_field(
        collector,
        section_data,
        key="update_interval_ms",
        path="gui.update_interval_ms",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="update_interval_ms"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="status_refresh_interval_ms",
        path="gui.status_refresh_interval_ms",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_min(value, 1, label="status_refresh_interval_ms"),
    )
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["gui"], seen_paths)


def _analyze_logging_section(imported_data: Dict[str, Any], collector: _ConfigImportCollector) -> None:
    section_data = _resolve_section_mapping(imported_data, "logging", collector)
    if section_data is None:
        return
    seen_paths: set[str] = set()
    for key, value in section_data.items():
        if key not in {"level", "file", "max_file_size_mb", "backup_count", "console_output"}:
            collector.add_unknown(f"logging.{key}", value)
    _process_scalar_field(collector, section_data, key="level", path="logging.level", seen_paths=seen_paths, converter=_normalize_logging_level)
    _process_scalar_field(
        collector,
        section_data,
        key="file",
        path="logging.file",
        seen_paths=seen_paths,
        converter=lambda value: _coerce_string(value, allow_empty=False),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="max_file_size_mb",
        path="logging.max_file_size_mb",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_range(value, 1, 100, label="max_file_size_mb"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="backup_count",
        path="logging.backup_count",
        seen_paths=seen_paths,
        converter=_coerce_int,
        validator=lambda value: _validate_range(value, 0, 20, label="backup_count"),
    )
    _process_scalar_field(
        collector,
        section_data,
        key="console_output",
        path="logging.console_output",
        seen_paths=seen_paths,
        converter=_coerce_bool,
    )
    _mark_missing_paths(collector, _CONFIG_IMPORT_PATHS["logging"], seen_paths)


def analyze_imported_config_text(
    yaml_text: str,
    *,
    source_name: str = "uploaded config",
    current_config: Optional[AppConfig] = None,
) -> ConfigImportPreview:
    cfg = current_config or get_global_config() or load_config()
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return ConfigImportPreview(
            source_name=source_name,
            errors=[f"YAML parsing error: {exc}"],
        )
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return ConfigImportPreview(
            source_name=source_name,
            errors=["Top-level YAML document must be a mapping"],
            entries=[
                ConfigImportEntry(
                    path="config",
                    status="invalid",
                    current_value=None,
                    imported_value=parsed,
                    reason="top-level YAML document must be a mapping",
                )
            ],
        )

    collector = _ConfigImportCollector(cfg)
    for key, value in parsed.items():
        if key not in _CONFIG_IMPORT_PATHS:
            collector.add_unknown(key, value)

    _analyze_metadata_section(parsed, collector)
    _analyze_webcam_section(parsed, collector)
    _analyze_uvc_section(parsed, collector)
    _analyze_motion_section(parsed, collector)
    _analyze_measurement_section(parsed, collector)
    _analyze_email_section(parsed, collector)
    _analyze_gui_section(parsed, collector)
    _analyze_logging_section(parsed, collector)

    return ConfigImportPreview(
        source_name=source_name,
        entries=_sort_import_entries(collector.entries),
        ready_updates=collector.ready_updates,
    )


def sync_runtime_config_instances(
    config: AppConfig,
    *,
    applied_paths: Optional[List[str]] = None,
    camera: Any = None,
    measurement_controller: Any = None,
    email_system: Any = None,
) -> ConfigRuntimeSyncResult:
    result = ConfigRuntimeSyncResult()

    if camera is not None:
        try:
            if hasattr(camera, "app_config"):
                camera.app_config = config
            if hasattr(camera, "webcam_config"):
                camera.webcam_config = config.webcam
            if hasattr(camera, "uvc_config"):
                camera.uvc_config = config.uvc_controls
            if hasattr(camera, "measurement_config"):
                camera.measurement_config = config.measurement
            result.refreshed_targets.append("camera")
        except Exception as exc:
            result.errors.append(f"camera sync failed: {exc}")

        if (
            hasattr(camera, "motion_detector")
            and getattr(camera, "motion_detector", None) is not None
            and _paths_include_prefix(applied_paths, "motion_detection")
        ):
            try:
                motion_detector = camera.motion_detector
                motion_detector.config = config.motion_detection
                full_motion_sync = applied_paths is None
                sensitivity_changed = (
                    not full_motion_sync
                    and _paths_include_prefix(applied_paths, "motion_detection.sensitivity")
                )
                min_contour_area_changed = (
                    not full_motion_sync
                    and _paths_include_prefix(applied_paths, "motion_detection.min_contour_area")
                )
                roi_changed = (
                    not full_motion_sync
                    and _paths_include_prefix(applied_paths, "motion_detection.region_of_interest")
                )

                motion_detector.sensitivity = config.motion_detection.sensitivity
                motion_detector.learning_rate = config.motion_detection.background_learning_rate

                if full_motion_sync:
                    motion_detector.min_contour_area = config.motion_detection.min_contour_area
                elif sensitivity_changed or min_contour_area_changed:
                    if hasattr(motion_detector, "update_sensitivity"):
                        motion_detector.update_sensitivity(config.motion_detection.sensitivity)
                    else:
                        motion_detector.min_contour_area = config.motion_detection.min_contour_area

                if full_motion_sync or roi_changed:
                    try:
                        motion_detector.roi = config.motion_detection.get_roi()
                    except Exception:
                        motion_detector.roi = ROI(enabled=False, x=0, y=0, width=0, height=0, points=[])

                if roi_changed and hasattr(motion_detector, "reset_background_model"):
                    motion_detector.reset_background_model()
                result.refreshed_targets.append("motion_detector")
            except Exception as exc:
                result.errors.append(f"motion detector sync failed: {exc}")

    if measurement_controller is not None and _paths_include_prefix(applied_paths, "measurement"):
        try:
            if hasattr(measurement_controller, "update_config"):
                measurement_controller.update_config(config.measurement)
            elif hasattr(measurement_controller, "config"):
                measurement_controller.config = config.measurement
            else:
                raise AttributeError("measurement controller does not support update_config() or config assignment")
            result.refreshed_targets.append("measurement_controller")
        except Exception as exc:
            result.errors.append(f"measurement controller sync failed: {exc}")

    if email_system is not None and _paths_include_prefix(applied_paths, "email", "measurement", "webcam", "motion_detection"):
        try:
            if hasattr(email_system, "app_cfg"):
                email_system.app_cfg = config
            if hasattr(email_system, "refresh_config"):
                email_system.refresh_config()
            result.refreshed_targets.append("email_system")
        except Exception as exc:
            result.errors.append(f"email system sync failed: {exc}")

    return result


def apply_imported_config_preview(
    preview: ConfigImportPreview,
    *,
    selected_paths: Optional[List[str]] = None,
    target_config: Optional[AppConfig] = None,
    target_path: Optional[str] = None,
    persist: bool = False,
) -> ConfigImportApplyResult:
    cfg = target_config or get_global_config()
    if cfg is None:
        return ConfigImportApplyResult(errors=["No active configuration is loaded"])
    if preview.errors:
        return ConfigImportApplyResult(errors=list(preview.errors))

    available_paths = list(preview.ready_updates.keys())
    chosen_paths = available_paths if selected_paths is None else [path for path in selected_paths if path in preview.ready_updates]
    if not chosen_paths:
        return ConfigImportApplyResult(errors=["No valid config settings selected for import"])

    original_snapshot = deepcopy(cfg)
    candidate_config = deepcopy(cfg)
    for path in chosen_paths:
        _set_config_value_by_path(candidate_config, path, preview.ready_updates[path])

    base_errors = _flatten_validation_errors(cfg.validate_all())
    candidate_errors = _flatten_validation_errors(candidate_config.validate_all())
    new_errors = sorted(candidate_errors - base_errors)
    if new_errors:
        return ConfigImportApplyResult(errors=[f"Import would introduce invalid config values: {', '.join(new_errors)}"])

    persist_path: Optional[str] = None
    if persist:
        global_cfg = get_global_config()
        if target_path:
            persist_path = target_path
        elif global_cfg is not None and cfg is global_cfg:
            persist_path = _config_path
        else:
            return ConfigImportApplyResult(
                errors=["target_path is required when persisting into a non-global target_config"]
            )

    _sync_config_in_place(cfg, candidate_config)
    cfg.measurement.ensure_save_path()

    if persist_path is not None:
        try:
            _write_config_file(cfg, persist_path)
        except Exception as exc:
            _sync_config_in_place(cfg, original_snapshot)
            return ConfigImportApplyResult(errors=[f"Imported settings could not be saved: {exc}"])

    return ConfigImportApplyResult(applied_paths=chosen_paths)

# ---------------------------------------------------------------------------
# Laden / Speichern
# ---------------------------------------------------------------------------

def load_config(path: str = "config/config.yaml", *, startup_fallback: bool = False) -> AppConfig:
    """Konfiguration mit RotatingFileHandler-Support laden"""
    defaulting_warnings: List[str] = []
    startup_warnings: List[str] = []
    bootstrap_logger = _get_bootstrap_config_logger()
    config_path = _resolve_config_path(path)
    cfg: AppConfig | None = None
    data: Any = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        if not startup_fallback:
            bootstrap_logger.warning("Config file not found: %s", path)
            bootstrap_logger.warning("Using default config")
            return _create_default_config()
        startup_warnings.append(f"Config file not found at {path}; using default config.")
        cfg = _create_default_config(log_creation=False)
    except yaml.YAMLError as exc:
        if not startup_fallback:
            raise ConfigLoadError(f"YAML parsing error in {config_path}: {exc}") from exc
        bootstrap_logger.warning("YAML parsing failed for %s: %s", config_path, exc)
        startup_warnings.append(f"Config file {path} could not be parsed as YAML; using default config.")
        cfg = _create_default_config(log_creation=False)
    except Exception as exc:
        raise ConfigLoadError(f"Unexpected error loading config {config_path}: {exc}") from exc

    if cfg is not None:
        return _finalize_loaded_config(
            cfg,
            config_path=config_path,
            defaulting_warnings=defaulting_warnings,
            startup_warnings=startup_warnings,
        )

    if data is None:
        if not startup_fallback:
            raise ConfigLoadError(f"Empty configuration file in {config_path}")
        startup_warnings.append(f"Config file {path} is empty; using default config.")
        cfg = _create_default_config(log_creation=False)
        return _finalize_loaded_config(
            cfg,
            config_path=config_path,
            defaulting_warnings=defaulting_warnings,
            startup_warnings=startup_warnings,
        )

    try:
        data = _apply_defaults(data, warnings=defaulting_warnings)
    except Exception as exc:
        if isinstance(exc, ConfigLoadError):
            raise
        raise ConfigLoadError(f"Invalid configuration structure in {config_path}: {exc}") from exc

    try:
        logging_data = data.get("logging", {})
        if not isinstance(logging_data, dict):
            raise ConfigLoadError(
                f"Config section 'logging' must be a mapping, got {type(logging_data).__name__}"
            )
        logging_config = LoggingConfig(
            level=logging_data.get("level", "INFO"),
            file=logging_data.get("file", "logs/cvd_tracker.log"),
            max_file_size_mb=logging_data.get("max_file_size_mb", 10),
            backup_count=logging_data.get("backup_count", 5),
            console_output=logging_data.get("console_output", True),
        )
        data["email"] = _normalize_loaded_email_data(data.get("email", {}), bootstrap_logger)
        data["gui"] = _normalize_loaded_gui_data(data.get("gui", {}), bootstrap_logger)
        cfg = AppConfig(
            metadata=Metadata(**data.get("metadata", {
                "version": 2.0,
                "description": "CVD-Tracker",
                "cvd_id": 0,
                "cvd_name": "Default_CVD",
                "released_at": "2026-04-14",
            })),
            webcam=WebcamConfig(**data["webcam"]),
            uvc_controls=UVCConfig(
                brightness=data["uvc_controls"]["brightness"],
                hue=data["uvc_controls"]["hue"],
                contrast=data["uvc_controls"]["contrast"],
                saturation=data["uvc_controls"]["saturation"],
                sharpness=data["uvc_controls"]["sharpness"],
                gamma=data["uvc_controls"]["gamma"],
                white_balance=WhiteBalance(**data["uvc_controls"]["white_balance"]),
                gain=data["uvc_controls"]["gain"],
                backlight_compensation=data["uvc_controls"]["backlight_compensation"],
                exposure=Exposure(**data["uvc_controls"]["exposure"]),
            ),
            motion_detection=MotionDetectionConfig(**data["motion_detection"]),
            measurement=MeasurementConfig(**data["measurement"]),
            email=EmailConfig(**data["email"]),
            gui=GUIConfig(**data["gui"]),
            logging=logging_config,
        )
    except Exception as exc:
        if isinstance(exc, ConfigLoadError):
            raise
        raise ConfigLoadError(f"Invalid configuration structure in {config_path}: {exc}") from exc

    errs = cfg.validate_all()
    if errs:
        formatted_errors = "; ".join(
            f"{section}: {msg}"
            for section, err_list in errs.items()
            for msg in err_list
        )
        bootstrap_logger.error("Fatal config validation errors in %s: %s", config_path, formatted_errors)
        raise ConfigLoadError(f"Invalid configuration in {config_path}: {formatted_errors}")

    return _finalize_loaded_config(
        cfg,
        config_path=config_path,
        defaulting_warnings=defaulting_warnings,
        startup_warnings=startup_warnings,
    )

def _apply_defaults(data: Any, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Smart Defaults für fehlende Config-Abschnitte"""
    if warnings is None:
        warnings = []
    default_cfg = _app_config_asdict(_create_default_config(log_creation=False))
    defaults = {
        "email": deepcopy(default_cfg["email"]),
        "gui": deepcopy(default_cfg["gui"]),
        "logging": deepcopy(default_cfg["logging"]),
    }

    if not isinstance(data, dict):
        raise ConfigLoadError(
            f"Top-level config document must be a mapping, got {type(data).__name__}"
        )

    for section, default_values in defaults.items():
        current_section = data.get(section)
        if current_section is None:
            data[section] = deepcopy(default_values)
            warnings.append(f"Config section '{section}' missing; using defaults.")
            continue
        if not isinstance(current_section, dict):
            raise ConfigLoadError(
                f"Config section '{section}' must be a mapping, got {type(current_section).__name__}"
            )
        missing_keys: List[str] = []
        for key, value in default_values.items():
            if key not in current_section:
                missing_keys.append(key)
                current_section[key] = deepcopy(value)
        if missing_keys:
            warnings.append(
                f"Config section '{section}' missing keys {missing_keys}; using defaults."
            )

    return data

def _create_default_config(*, log_creation: bool = True) -> AppConfig:
    """Fallback-Konfiguration für Notfälle"""
    if log_creation:
        logger.info("creating default config")
    return AppConfig(
        metadata=Metadata(
            version="2.0",
            description="CVD-Tracker",
            cvd_id=0,
            cvd_name="Default_CVD",
            released_at="2026-04-14",
        ),
        webcam=WebcamConfig(
            camera_index=0,
            default_resolution={"width": 1280, "height": 720},
            fps=30,
            preview_fps=15,
            preview_max_width=1280,
            preview_jpeg_quality=70,
            resolution=[{"width": 320, "height": 240},
                        {"width": 352, "height": 288},
                        {"width": 640, "height": 480},
                        {"width": 800, "height": 600},
                        {"width": 1024, "height": 768},
                        {"width": 1280, "height": 720},
                        {"width": 1280, "height": 960},
                        {"width": 1280, "height": 1024},
                        {"width": 1920, "height": 1080}
                    ]
                ),
        uvc_controls=UVCConfig(
            brightness=0, hue=0, contrast=16, saturation=64,
            sharpness=2, gamma=164, gain=10, backlight_compensation=42,
            white_balance=WhiteBalance(auto=True, value=4000),
            exposure=Exposure(auto=True, value=-6)
        ),
        motion_detection=MotionDetectionConfig(
            region_of_interest={"enabled": False, "x": 100, "y": 100, "width": 300, "height": 200},
            sensitivity=0.1, background_learning_rate=0.005, min_contour_area=252,
            frame_skip=1, processing_max_width=800,
        ),
        measurement=MeasurementConfig(
            auto_start=False, session_timeout_minutes=60, session_timeout_seconds=3600, save_alert_images=True,
            image_save_path="./alerts/", image_format="jpg", image_quality=85,
            alert_delay_seconds=300,
            max_alerts_per_session=5,
            alert_check_interval=5.0,
            alert_cooldown_seconds=300,
            alert_include_snapshot=True,
            inactivity_timeout_minutes=60,
            motion_summary_interval_seconds=60,
            enable_motion_summary_logs=True
        ),
        email=EmailConfig(
            website_url="http://localhost:8080/",
            recipients=["user@example.com"],
            smtp_server="smtp.example.com",
            smtp_port=25,
            sender_email="sender@example.com",
            send_as_html=True,
            website_url_source=EmailConfig.WEBSITE_URL_SOURCE_RUNTIME_PERSIST,
            templates={
                "alert": {
                    "subject": "CVD-TRACKER{cvd_id}-{cvd_name}-Alert: no motion detected - {timestamp}",
                    "body": (
                        "Movement has not been detected since {timestamp}!"
                        "\nPlease check the issue via the web application at: {website_url}."
                        "\n\nDetails:"
                        "\nCVD-ID: {cvd_id}"
                        "\nCVD-Name: {cvd_name}"
                        "\nSession-ID: {session_id}"
                        "\nLast motion at: {last_motion_time}"
                        "\nCamera: Index {camera_index}"
                        "\nSensitivity: {sensitivity}"
                        "\nROI enabled: {roi_enabled}"
                        "\n\n{snapshot_note}"
                    )
                },
                "measurement_start": {
                    "subject": "CVD-TRACKER{cvd_id}-{cvd_name}: Measurement started - {timestamp}",
                    "body": (
                        "CVD-Tracker{cvd_id}-{cvd_name} Measurement Started\n"
                        "A new measurement session has started at {timestamp}.\n\n"
                        "You can monitor the session via the web application at: {website_url}.\n\n"
                        "Details:\n"
                        "   CVD-ID:         {cvd_id}\n"
                        "   CVD-Name:       {cvd_name}\n"
                        "   Website URL:    {website_url}\n\n"
                        "   Session-ID:     {session_id}\n"
                        "   Last motion at: {last_motion_time}\n"
                        "   Start:          {start_time}\n"
                        "   End:            {end_time}\n"
                        "   Duration:       {duration}\n"
                        "   Reason:         {reason}\n\n"
                        "   Camera: Index   {camera_index}\n"
                        "   Sensitivity:    {sensitivity}\n"
                        "   ROI enabled:    {roi_enabled}\n\n"
                    )
                },
                "measurement_end": {
                    "subject": "CVD-TRACKER{cvd_id}-{cvd_name}: Measurement ended - {timestamp}",
                    "body": (
                        "CVD-Tracker{cvd_id}-{cvd_name} Measurement Ended\n"
                        "The measurement session has ended at {timestamp}.\n\n"
                        "You can monitor the session via the web application at: {website_url}.\n\n"
                        "Details:\n"
                        "   CVD-ID:         {cvd_id}\n"
                        "   CVD-Name:       {cvd_name}\n"
                        "   Website URL:    {website_url}\n\n"
                        "   Session-ID:     {session_id}\n"
                        "   Last motion at: {last_motion_time}\n"
                        "   Start:          {start_time}\n"
                        "   End:            {end_time}\n"
                        "   Duration:       {duration}\n"
                        "   Reason:         {reason}\n\n"
                        "   Camera: Index   {camera_index}\n"
                        "   Sensitivity:    {sensitivity}\n"
                        "   ROI enabled:    {roi_enabled}\n\n"
                    )
                }
            },
            static_recipients=["user@example.com"],
            explicit_targeting=True,
            notifications={"on_start": False, "on_end": False, "on_stop": False},
            group_prefs={},
            recipient_prefs={}
        ),
        gui=GUIConfig(
            title="CVD-Tracker", host="localhost", port=8080,
            auto_open_browser=False,
            update_interval_ms=100,
            status_refresh_interval_ms=1000,
            reverse_proxy_enabled=False,
            forwarded_allow_ips="127.0.0.1",
            root_path="",
            session_cookie_https_only=False,
        ),
        logging=LoggingConfig(
            level="INFO", file="logs/cvd_tracker.log",
            max_file_size_mb=10, backup_count=5, console_output=True
        )
    )

_global_config: Optional[AppConfig] = None
_config_path: str = str(_resolve_config_path("config/config.yaml"))
_global_config_warnings: List[str] = []


@dataclass(frozen=True)
class _GlobalConfigRegistrySnapshot:
    config: Optional[AppConfig]
    path: str
    warnings: List[str]


def _snapshot_global_config_registry() -> _GlobalConfigRegistrySnapshot:
    """Capture the exact active config registry state for later restoration."""
    return _GlobalConfigRegistrySnapshot(
        config=_global_config,
        path=_config_path,
        warnings=list(_global_config_warnings),
    )


def _restore_global_config_registry(snapshot: _GlobalConfigRegistrySnapshot) -> None:
    """Restore a previously captured config registry snapshot verbatim."""
    global _global_config, _config_path, _global_config_warnings
    _global_config = snapshot.config
    _config_path = str(snapshot.path)
    _global_config_warnings = list(snapshot.warnings)

def set_global_config(config: AppConfig, path: str = "config/config.yaml") -> None:
    """Setzt die globale Config-Instanz"""
    global _global_config, _config_path, _global_config_warnings
    _global_config = config
    _config_path = str(_resolve_config_path(path))
    _global_config_warnings = _get_attached_startup_config_warnings(config)

def get_global_config() -> Optional[AppConfig]:
    """Holt die globale Config-Instanz"""
    return _global_config

def get_global_config_path() -> Optional[str]:
    """Return the path that was used to load the active global configuration."""
    if _global_config is None:
        return None
    return _config_path


def get_global_config_warnings() -> List[str]:
    """Return non-fatal startup warnings associated with the active config."""
    if _global_config is None:
        return []
    return list(_global_config_warnings)


def clear_global_config_warnings() -> None:
    """Clear non-fatal startup warnings associated with the active config."""
    global _global_config_warnings
    _clear_attached_startup_config_warnings(_global_config)
    _global_config_warnings = []

def save_global_config() -> bool:
    """Speichert die globale Config"""
    global _global_config, _config_path
    if _global_config:
        try:
            _write_config_file(_global_config, _config_path)
            clear_global_config_warnings()
            try:
                from src.gui import instances as gui_instances
                gui_instances.clear_startup_config_warnings()
            except Exception:
                pass
            logger.info(f"Global config saved to {_config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save global config: {e}")
            return False
    return False

def get_logger(name: str = "cvd_tracker") -> logging.Logger:
    """
    Hilfsfunktion um Logger konsistent zu bekommen.
    
    Args:
        name: Logger-Name oder Sub-Component (z.B. "camera", "gui", "motion")
        
    Returns:
        Konfigurierter Logger
    """
    global _global_config

    if _global_config is None:
        main_logger = _get_fallback_main_logger()
    else:
        main_logger = _global_config.logging.setup_logger("cvd_tracker")

    normalized_name = str(name or "cvd_tracker")
    if normalized_name == "cvd_tracker":
        return main_logger
    if normalized_name.startswith("cvd_tracker."):
        return logging.getLogger(normalized_name)
    return main_logger.getChild(normalized_name)
    
class ReadableDumper(yaml.SafeDumper):
    # Keine YAML‑Anker (&id001) bei wiederverwendeten Werten
    def ignore_aliases(self, data: Any) -> bool:
        return True

    # Sorgt dafür, dass Listenelemente eingerückt unter dem Schlüssel stehen
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)

def _repr_str_literal(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    # Mehrzeilige Strings im Literal-Block-Stil '|'
    style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

ReadableDumper.add_representer(str, _repr_str_literal)

def _repr_ordered_dict(dumper: yaml.SafeDumper, data: OrderedDict) -> yaml.nodes.MappingNode:
    return dumper.represent_mapping('tag:yaml.org,2002:map', list(data.items()))

ReadableDumper.add_representer(OrderedDict, _repr_ordered_dict)

def _order_map(d: dict, key_order: list[str]) -> OrderedDict:
    ordered = OrderedDict()
    for k in key_order:
        if k in d:
            ordered[k] = d[k]
    for k, v in d.items():
        if k not in ordered:
            ordered[k] = v
    return ordered

def _prepare_for_yaml(cfg_dict: dict) -> dict:
    """
    Optionale Reorganisation für besser lesbare YAML-Ausgabe.
    """
    data = dict(cfg_dict)  # flache Kopie

    # metadata: übliche Reihenfolge
    if "metadata" in data and isinstance(data["metadata"], dict):
        data["metadata"] = _order_map(
            data["metadata"],
            ["version", "description", "cvd_id", "cvd_name", "released_at"]
        )

    # webcam: width vor height
    if "webcam" in data:
        wc = data["webcam"]
        data["webcam"] = _order_map(
            wc,
            [
                "camera_index",
                "default_resolution",
                "fps",
                "preview_fps",
                "preview_max_width",
                "preview_jpeg_quality",
                "resolution",
            ],
        )
        wc = data["webcam"]
        if "default_resolution" in wc and isinstance(wc["default_resolution"], dict):
            wc["default_resolution"] = _order_map(wc["default_resolution"], ["width", "height"])
        if "resolution" in wc and isinstance(wc["resolution"], list):
            wc["resolution"] = [
                _order_map(item, ["width", "height"]) if isinstance(item, dict) else item
                for item in wc["resolution"]
            ]

    # motion_detection.region_of_interest: übliche Reihenfolge
    md = data.get("motion_detection", {})
    if isinstance(md.get("region_of_interest"), dict):
        roi = md["region_of_interest"]
        md["region_of_interest"] = _order_map(roi, ["enabled", "x", "y", "width", "height"])

    # email.templates.alert: subject vor body
    try:
        data["email"] = _order_map(
            data["email"],
            [
                "website_url",
                "website_url_source",
                "recipients",
                "smtp_server",
                "smtp_port",
                "sender_email",
                "send_as_html",
                "templates",
                "groups",
                "active_groups",
                "static_recipients",
                "explicit_targeting",
                "notifications",
                "group_prefs",
                "recipient_prefs",
            ],
        )
        alert = data["email"]["templates"]["alert"]
        if isinstance(alert, dict):
            data["email"]["templates"]["alert"] = _order_map(alert, ["subject", "body"])
    except Exception:
        pass

    # Also order measurement templates: subject before body
    try:
        tpls = data["email"]["templates"]
        for key in ("measurement_start", "measurement_end", "measurement_stop"):
            tpl = tpls.get(key)
            if isinstance(tpl, dict):
                tpls[key] = _order_map(tpl, ["subject", "body"])
    except Exception:
        pass

    if "gui" in data and isinstance(data["gui"], dict):
        data["gui"] = _order_map(
            data["gui"],
            [
                "title",
                "host",
                "port",
                "reverse_proxy_enabled",
                "forwarded_allow_ips",
                "root_path",
                "session_cookie_https_only",
                "auto_open_browser",
                "update_interval_ms",
                "status_refresh_interval_ms",
            ],
        )

    return data

def _dump_section(key: str, value: dict) -> str:
    """
    Gibt einen einzelnen Top-Level-Abschnitt als YAML (ohne Dokument-Header) zurück.
    """
    result = yaml.dump(
        {key: value},
        Dumper=ReadableDumper,
        allow_unicode=True,
        sort_keys=False,            # Reihenfolge behalten
        default_flow_style=False,   # Block-Stil
        indent=2,
        width=4096                  # kein erzwungenes Zeilenfalten
    )
    return str(result) if result is not None else ""

def _write_config_file(cfg: AppConfig, path: str = "config/config.yaml") -> None:
    p = _resolve_config_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    raw = _app_config_asdict(cfg)
    data = _prepare_for_yaml(raw)

    sections = [
        ("Metadata", "metadata"),
        ("Webcam", "webcam"),
        ("UVC Controls", "uvc_controls"),
        ("Motion Detection", "motion_detection"),
        ("Measurement", "measurement"),
        ("E‑Mail", "email"),
        ("GUI", "gui"),
        ("Logging", "logging"),
    ]

    header = (
        "# ---------------------------------------------------------------------------\n"
        "# CVD-Tracker configuration (generated)\n"
        "# Edit carefully — indentation defines structure\n"
        "# ---------------------------------------------------------------------------\n\n"
    )

    with open(p, "w", encoding="utf-8") as f:
        f.write(header)

        for title, key in sections:
            if key not in data:
                continue
            f.write("# ---------------------------------------------------------------------------\n")
            f.write(f"# {title}\n")
            f.write("# ---------------------------------------------------------------------------\n")
            f.write(_dump_section(key, data[key]))
            f.write("\n")

    logger.info("✅ Config saved → %s", p)

def save_config(cfg: AppConfig, path: str = "config/config.yaml") -> None:
    """Konfiguration als gut lesbare YAML speichern (mit Abschnitts-Kommentaren)."""
    try:
        _write_config_file(cfg, path)
        return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Dataclasses -> dict und für Ausgabe aufbereiten
        raw = _app_config_asdict(cfg)
        data = _prepare_for_yaml(raw)

        # Reihenfolge und sichtbare Abschnittstitel festlegen
        sections = [
            ("Metadata", "metadata"),
            ("Webcam", "webcam"),
            ("UVC Controls", "uvc_controls"),
            ("Motion Detection", "motion_detection"),
            ("Measurement", "measurement"),
            ("E‑Mail", "email"),
            ("GUI", "gui"),
            ("Logging", "logging"),
        ]

        header = (
            "# ---------------------------------------------------------------------------\n"
            "# CVD-Tracker configuration (generated)\n"
            "# Edit carefully — indentation defines structure\n"
            "# ---------------------------------------------------------------------------\n\n"
        )

        with open(p, "w", encoding="utf-8") as f:
            f.write(header)

            for title, key in sections:
                if key not in data:
                    continue
                # Abschnitts-Banner
                f.write("# ---------------------------------------------------------------------------\n")
                f.write(f"# {title}\n")
                f.write("# ---------------------------------------------------------------------------\n")
                # Abschnitt dumpen
                f.write(_dump_section(key, data[key]))
                # Leerzeile zwischen Abschnitten
                f.write("\n")

        logger.info("✅ Config saved → %s", p)
    except Exception as e:
        logger.error("❌ Error saving config: %s", e)

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Test der erweiterten Konfiguration mit RotatingFileHandler"""
    logger.info("🧪 Teste Konfigurationssystem mit RotatingFileHandler...")
    
    # Konfiguration laden
    config = load_config()
    
    # Logger einrichten und testen
    logger = config.logging.setup_logger()

    # Logger testen
    logger.info("Test message: Config loaded")
    logger.debug("Debug test")
    logger.warning("Warning test")
    logger.error("Error test")

    logger.info("✅ RotatingFileHandler test completed")
