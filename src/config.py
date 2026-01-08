from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import re
import yaml
import logging
import logging.handlers
import threading
from collections import OrderedDict

logger = logging.getLogger(__name__)
logger.propagate = False  # Verhindert, dass Logs an die Root-Logger propagiert werden

from enum import Enum

_configured_loggers: set[str] = set()
_configured_loggers_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Metadaten
# ---------------------------------------------------------------------------

@dataclass
class Metadata:
    version: str = "1.0"
    description: str = "CVD-Tracker"
    cvd_id: int = 0
    cvd_name: str = "Default_CVD"
    released_at: str = "2023-01-01"

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

    def get_default_resolution(self) -> Resolution:
        return Resolution(**self.default_resolution)

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

    def get_roi(self) -> ROI:
        return ROI(**self.region_of_interest)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not 0.001 <= self.sensitivity <= 1.0:
            errors.append("Sensitivity outside [0.001, 1.0]")
        if not 0.001 <= self.background_learning_rate <= 1.0:
            errors.append("Learning rate outside [0.001, 1.0]")
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
    image_save_path: str
    image_format: str
    image_quality: int
    alert_delay_seconds: int
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
    # History path for alert events (Issue #10)
    history_path: str = "data/history"

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.alert_delay_seconds < 30:
            errors.append("alert_delay_seconds < 30")
        if self.session_timeout_minutes < 0:
            errors.append("session_timeout_minutes < 0")
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
        Path(self.image_save_path).mkdir(parents=True, exist_ok=True)

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
    # Recipient groups and active group selection
    groups: Dict[str, List[str]] = field(default_factory=dict)
    active_groups: List[str] = field(default_factory=list)
    # Measurement notification toggles
    notifications: Dict[str, bool] = field(default_factory=dict)
    # Per-recipient notification preferences (overrides global notifications)
    # Mapping: email -> { 'on_start': bool, 'on_end': bool, 'on_stop': bool }
    recipient_prefs: Dict[str, Dict[str, bool]] = field(default_factory=dict)

    EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}$")

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

        # notifications
        if not isinstance(self.notifications, dict):
            raise TypeError(f"EmailConfig.notifications must be Dict[str, bool], got {type(self.notifications).__name__}: {self.notifications!r}")
        for k, notif_enabled in self.notifications.items():
            if not isinstance(k, str):
                raise TypeError(f"EmailConfig.notifications keys must be str, got {type(k).__name__}: {k!r}")
            if not isinstance(notif_enabled, bool):
                raise TypeError(f"EmailConfig.notifications['{k}'] must be bool, got {type(notif_enabled).__name__}: {notif_enabled!r}")

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
                for mail in addrs or []:
                    if not self.EMAIL_RE.match(mail):
                        errors.append(f"invalid email address in group '{gname}': {mail}")
        if self.active_groups:
            unknown = [g for g in self.active_groups if g not in (self.groups or {})]
            if unknown:
                errors.append(f"unknown active groups: {unknown}")
        # Notifications flags (optional)
        if self.notifications is not None and isinstance(self.notifications, dict):
            for k, v in self.notifications.items():
                if not isinstance(v, bool):
                    errors.append(f"notification flag '{k}' must be a bool, got {type(v).__name__}")
        # recipient_prefs validation (ensure keys are emails and values are bools)
        if self.recipient_prefs is not None and isinstance(self.recipient_prefs, dict):
            for email, prefs in self.recipient_prefs.items():
                if not isinstance(email, str) or not self.EMAIL_RE.match(email):
                    errors.append(f"recipient_prefs has invalid email key: {email!r}")
                if not isinstance(prefs, dict):
                    errors.append(f"recipient_prefs['{email}'] must be dict, got {type(prefs).__name__}")
                    continue
                for k, v in prefs.items():
                    if not isinstance(v, bool):
                        errors.append(f"recipient_prefs['{email}']['{k}'] must be bool, got {type(v).__name__}")
        # SMTP
        if not 1 <= self.smtp_port <= 65535:
            errors.append("smtp_port must be between [1, 65535]")
        if not self.smtp_server:
            errors.append("smtp_server must not be empty")
        return errors

    def alert_template(self) -> EmailTemplate:
        data = self.templates.get("alert", {})
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
            "Attached is the current webcam image."
        )
        
        return EmailTemplate(
            subject=data.get("subject", default_subject),
            body=data.get("body", default_body),
        )
    
    def test_template(self) -> EmailTemplate:
        data = self.templates.get("test", {})
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
        
        return EmailTemplate(
            subject=data.get("subject", default_subject),
            body=data.get("body", default_body),
        )
    
    def measurement_start_template(self) -> EmailTemplate:
        data = self.templates.get("measurement_start", {})
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
        
        return EmailTemplate(
            subject=data.get("subject", default_subject),
            body=data.get("body", default_body),
        )
    
    def measurement_end_template(self) -> EmailTemplate:
        data = self.templates.get("measurement_end", {})
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
        
        return EmailTemplate(
            subject=data.get("subject", default_subject),
            body=data.get("body", default_body),
        )
    
    def measurement_stop_template(self) -> EmailTemplate:
        data = self.templates.get("measurement_stop", {})
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
        
        return EmailTemplate(
            subject=data.get("subject", default_subject),
            body=data.get("body", default_body),
        )

    def get_target_recipients(self) -> List[str]:
        """Compute effective recipients based on active groups."""
        if self.active_groups and self.groups:
            collected: List[str] = []
            for g in self.active_groups:
                for addr in (self.groups.get(g, []) or []):
                    if self.EMAIL_RE.match(addr):
                        collected.append(addr)
            if collected:
                return list(dict.fromkeys(collected))
        return list(self.recipients)
# ---------------------------------------------------------------------------
# GUI & Logging
# ---------------------------------------------------------------------------

@dataclass
class GUIConfig:
    title: str
    host: str
    port: int
    auto_open_browser: bool
    update_interval_ms: int

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
    
    def setup_logger(self, name: str = "cvd_tracker") -> logging.Logger:
        """RotatingFileHandler-Logger einrichten"""
        global _initialized_logger
        logger = logging.getLogger(name)

        with _configured_loggers_lock:
            if name in _configured_loggers:
                return logger
            # Prevent duplicate setup - check if already configured
            if logger.handlers and any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
                _configured_loggers.add(name)
                return logger
              
        logger.setLevel(getattr(logging, self.level.upper()))

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        logger.propagate = False  # Verhindert, dass Logs an die Root-Logger propagiert werden
        
        # Log-Verzeichnis erstellen
        log_path = Path(self.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # RotatingFileHandler einrichten
        file_handler = logging.handlers.RotatingFileHandler(
            filename=self.file,
            maxBytes=self.max_file_size_mb * 1024 * 1024,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        
        # Handler-Liste erstellen
        handlers: List[logging.Handler] = [file_handler]
        if self.console_output:
            console_handler = logging.StreamHandler()
            handlers.append(console_handler)
        
        # Formatter für alle Handler
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%d.%m.%Y %H:%M:%S'
        )
        
        for handler in handlers:
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        with _configured_loggers_lock:
            _configured_loggers.add(name)  # Logger als konfiguriert markieren

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

    def validate_all(self) -> Dict[str, List[str]]:
        res: Dict[str, List[str]] = {}
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
        if (e := self.logging.validate()):
            res["logging"] = e
        return res

# ---------------------------------------------------------------------------
# Laden / Speichern
# ---------------------------------------------------------------------------

def load_config(path: str = "config/config.yaml") -> AppConfig:
    """Konfiguration mit RotatingFileHandler-Support laden"""
    global logger
    try:
        # Absoluten Pfad konstruieren falls nötig
        if not Path(path).is_absolute():
            project_root = Path(__file__).parents[1]
            config_path = project_root / path
        else:
            config_path = Path(path)

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

    except FileNotFoundError:
        print(f"❌ Config file not found: {path}")  # Fallback print statt logger
        print("🔧 Using default config")
        return _create_default_config()

    except yaml.YAMLError as e:
        print(f"❌ YAML parsing error: {e}")  # Fallback print statt logger
        print("🔧 Using default config")
        return _create_default_config()

    except Exception as e:
        print(f"❌ Unexpected error loading config: {e}")  # Fallback print statt logger
        print("🔧 Using default config")
        return _create_default_config()
    
    # Defaults für Logging anwenden
    data = _apply_defaults(data)

    # LoggingConfig mit erweiterten Parametern
    logging_data = data.get("logging", {})
    logging_config = LoggingConfig(
        level=logging_data.get("level", "INFO"),
        file=logging_data.get("file", "logs/cvd_tracker.log"),
        max_file_size_mb=logging_data.get("max_file_size_mb", 10),
        backup_count=logging_data.get("backup_count", 5),
        console_output=logging_data.get("console_output", True)
    )

    # Jetzt den finalen Logger initialisieren
    app_logger = logging_config.setup_logger("cvd_tracker")
    logger = app_logger.getChild("config")
    logger.info("✅ Config loaded: %s", config_path)
    cfg = AppConfig(
        metadata=Metadata(**data.get("metadata", {
            "version": 1.0,
            "description": "CVD-Tracker",
            "cvd_id": 0,
            "cvd_name": "Default_CVD",
            "released_at": "2023-01-01",
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
        logging=logging_config,  # Erweiterte Logging-Config
    )
    if errs := cfg.validate_all():
        logger.warning("⚠️ Config warnings:")
        for section, err_list in errs.items():
            for msg in err_list:
                logger.warning("  %s: %s", section, msg)
    cfg.measurement.ensure_save_path()
    return cfg

def _apply_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """Smart Defaults für fehlende Config-Abschnitte"""
    defaults = {
        "logging": {
            "level": "INFO",
            "file": "logs/cvd_tracker.log",
            "max_file_size_mb": 10,
            "backup_count": 5,
            "console_output": True
        }
    }
    
    for section, default_values in defaults.items():
        if section not in data:
            data[section] = default_values
        else:
            for key, value in default_values.items():
                if key not in data[section]:
                    data[section][key] = value
    
    return data

def _create_default_config() -> AppConfig:
    """Fallback-Konfiguration für Notfälle"""
    logger.info("creating default config")
    return AppConfig(
        metadata=Metadata(
            version="1.0",
            description="CVD-Tracker",
            cvd_id=0,
            cvd_name="Default_CVD",
            released_at="2023-01-01",
        ),
        webcam=WebcamConfig(
            camera_index=0,
            default_resolution={"width": 1920, "height": 1080},
            fps=30,
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
            sensitivity=0.1, background_learning_rate=0.005, min_contour_area=252
        ),
        measurement=MeasurementConfig(
            auto_start=False, session_timeout_minutes=60, save_alert_images=True,
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
            website_url="http://134.28.91.48:8080",
            recipients=["user@example.com"],
            smtp_server="smtp.example.com",
            smtp_port=25,
            sender_email="sender@example.com",
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
                        "\n\nAttached is the current webcam image."
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
            notifications={"on_start": False, "on_end": False},
            recipient_prefs={}
        ),
        gui=GUIConfig(
            title="CVD-Tracker", host="localhost", port=8080,
            auto_open_browser=False, update_interval_ms=100
        ),
        logging=LoggingConfig(
            level="INFO", file="logs/cvd_tracker.log",
            max_file_size_mb=10, backup_count=5, console_output=True
        )
    )

_global_config: Optional[AppConfig] = None
_config_path: str = "config/config.yaml"

def set_global_config(config: AppConfig, path: str = "config/config.yaml") -> None:
    """Setzt die globale Config-Instanz"""
    global _global_config, _config_path
    _global_config = config
    _config_path = path

def get_global_config() -> Optional[AppConfig]:
    """Holt die globale Config-Instanz"""
    return _global_config

def save_global_config() -> bool:
    """Speichert die globale Config"""
    global _global_config, _config_path
    if _global_config:
        try:
            save_config(_global_config, _config_path)
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
        # Fallback falls Config noch nicht geladen
        _global_config = load_config()
    
    main_logger = _global_config.logging.setup_logger("cvd_tracker")
    
    if name == "cvd_tracker":
        return main_logger
    else:
        return main_logger.getChild(name)
    
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

def save_config(cfg: AppConfig, path: str = "config/config.yaml") -> None:
    """Konfiguration als gut lesbare YAML speichern (mit Abschnitts-Kommentaren)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Dataclasses -> dict und für Ausgabe aufbereiten
        raw = asdict(cfg)
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
