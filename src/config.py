from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Tuple
import re
import yaml
import logging
import logging.handlers

# Basic logging setup to capture early messages before configuration is loaded
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)
from enum import Enum

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
            ("brightness", self.brightness, 0, 255),
            ("hue", self.hue, -180, 180),
            ("contrast", self.contrast, 0, 255),
            ("saturation", self.saturation, 0, 255),
            ("sharpness", self.sharpness, 0, 255),
            ("gamma", self.gamma, 1, 500),
            ("gain", self.gain, 0, 255),
            ("backlight_compensation", self.backlight_compensation, 0, 255),
        ]
        for name, value, lo, hi in checks:
            if not lo <= value <= hi:
                errors.append(f"{name}: {value} außerhalb [{lo}, {hi}]")
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

    def validate(self, frame_w: int, frame_h: int) -> List[str]:
        if not self.enabled:
            return []
        errors: List[str] = []
        if self.x < 0 or self.y < 0:
            errors.append("ROI‑Koordinaten dürfen nicht negativ sein")
        if self.width <= 0 or self.height <= 0:
            errors.append("ROI‑Größe muss positiv sein")
        if self.x + self.width > frame_w:
            errors.append("ROI überschreitet Frame‑Breite")
        if self.y + self.height > frame_h:
            errors.append("ROI überschreitet Frame‑Höhe")
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
            errors.append("Sensitivity außerhalb [0.001, 1.0]")
        if not 0.001 <= self.background_learning_rate <= 1.0:
            errors.append("Learning‑Rate außerhalb [0.001, 1.0]")
        if self.min_contour_area < 1:
            errors.append("min_contour_area muss ≥1 sein")
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

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.alert_delay_seconds < 60:
            errors.append("alert_delay_seconds < 60")
        if self.session_timeout_minutes < 1:
            errors.append("session_timeout_minutes < 1")
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

    EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}$")

    def validate(self) -> List[str]:
        errors: List[str] = []
        for mail in [self.sender_email, *self.recipients]:
            if not self.EMAIL_RE.match(mail):
                errors.append(f"invalid email address: {mail}")
        if not 1 <= self.smtp_port <= 65535:
            errors.append("smtp_port must be between [1, 65535]")
        if not self.smtp_server:
            errors.append("smtp_server must not be empty")
        return errors

    def alert_template(self) -> EmailTemplate:
        data = self.templates.get("alert", {})
        
        # Robuster Default-Body mit allen verfügbaren Parametern
        default_body = (
            "No Motion detected since {timestamp}!\n"
            "Please check the website at: {website_url}\n\n"
            "Details:\n"
            "Session-ID: {session_id}\n"
            "Last motion at {last_motion_time}\n"
            "Camera: Index {camera_index}\n"
            "Sensitivity: {sensitivity}\n"
            "ROI enabled: {roi_enabled}\n\n"
            "Attached is the current webcam image."
        )
        
        return EmailTemplate(
            subject=data.get("subject", "CVD-Tracker: No Motion Detected - {timestamp}"),
            body=data.get("body", default_body),
        )
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
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, self.level.upper()))
        
        # Vorherige Handler entfernen
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
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
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        for handler in handlers:
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        logger.info(f"🚀 Logging initialized: {self.file} (max: {self.max_file_size_mb}MB, backups: {self.backup_count})")
        return logger

# ---------------------------------------------------------------------------
# Top‑Level AppConfig
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
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
        logger.error("❌ config file not found: %s", path)
        logger.info("🔧 Using default config")
        return _create_default_config()

    except yaml.YAMLError as e:
        logger.error("❌ YAML parsing error: %s", e)
        logger.info("🔧 Using default config")
        return _create_default_config()

    except Exception as e:
        logger.error("❌ Unexpected error loading config: %s", e)
        logger.info("🔧 Using default config")
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
        for section, e in errs.items():
            for msg in e:
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
        webcam=WebcamConfig(
            camera_index=0,
            default_resolution={"width": 1920, "height": 1080},
            fps=30,
            resolution=[{"width": 640, "height": 480},
                        {"width": 320, "height": 240},
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
            exposure=Exposure(auto=True, value=100)
        ),
        motion_detection=MotionDetectionConfig(
            region_of_interest={"enabled": False, "x": 100, "y": 100, "width": 300, "height": 200},
            sensitivity=0.01, background_learning_rate=0.01, min_contour_area=500
        ),
        measurement=MeasurementConfig(
            auto_start=False, session_timeout_minutes=60, save_alert_images=True,
            image_save_path="./alerts/", image_format="jpg", image_quality=85,
            alert_delay_seconds=300
        ),
        email=EmailConfig(
            website_url="http://134.28.91.48:8080",
            recipients=["user@example.com"],
            smtp_server="smtp.example.com",
            smtp_port=25,
            sender_email="sender@example.com",
            templates={"alert": {"subject": "CVD-Alert: no motion detected - {timestamp}", "body": 
                                 "Movement has not been detected since {timestamp}!"
                                 "\nPlease check the issue via the web application at: {website_url}."
                                 "\n\nDetails:"
                                 "\nSession-ID: {session_id}"
                                 "\nLast motion at: {last_motion_time}"
                                 "\nCamera: Index {camera_index}"
                                 "\nSensitivity: {sensitivity}"
                                 "\nROI active: {roi_enabled}"
                                 "\n\nAttached is the current webcam image."
                                 }}
                                 
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

def save_config(cfg: AppConfig, path: str = "config/config.yaml") -> None:
    """Konfiguration als YAML speichern"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(cfg), f, indent=2, allow_unicode=True)
        logger.info("✅ Config saved → %s", path)
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
