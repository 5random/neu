"""
Einfache Bewegungserkennung für Webcam-System.

Dieses Modul implementiert eine minimalistische Bewegungserkennung mit OpenCV
für das Webcam-Überwachungssystem. Es bietet die grundlegenden Features:
- Bewegungserkennung mit konfigurierbarer Sensitivität
- ROI (Region of Interest) Support
- Integration mit Alert-System

"""

import cv2
import numpy as np
import time
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import MotionDetectionConfig, ROI


@dataclass
class MotionResult:
    """
    Einfaches Ergebnis einer Bewegungserkennung.
    
    Attributes:
        motion_detected: True wenn Bewegung erkannt wurde
        contour_area: Größe der erkannten Bewegung in Pixeln
        timestamp: Zeitstempel der Erkennung
        roi_used: True wenn ROI verwendet wurde
    """
    motion_detected: bool
    contour_area: float
    timestamp: float
    roi_used: bool = False


class MotionDetector:
    """
    Einfache Bewegungserkennung mit OpenCV Background Subtraction.
    
    Features:
    - MOG2 Background Subtractor für Bewegungserkennung
    - Konfigurierbare Sensitivität
    - ROI (Region of Interest) Support
    - Einfache API für GUI-Integration
    
    Usage:
        detector = MotionDetector(config)
        result = detector.detect_motion(frame)
        if result.motion_detected:
            print("Bewegung erkannt!")
    """
    
    def __init__(self, config: 'MotionDetectionConfig', logger: Optional[logging.Logger] = None):
        """
        Initialisiert den MotionDetector.
        
        Args:
            config: MotionDetectionConfig mit Sensitivität und ROI
            logger: Optional Logger für Debug-Output
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # OpenCV Background Subtractor
        self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
            detectShadows=True,
            varThreshold=16,
            history=300
        )
        
        # Bewegungsparameter
        self.sensitivity = config.sensitivity
        self.min_contour_area = config.min_contour_area
        self.learning_rate = config.background_learning_rate
        
        # ROI Setup
        try:
            self.roi = config.get_roi()
        except Exception as exc:
            self.logger.warning(f"ROI-Setup fehlgeschlagen: {exc}")
            # Fallback: ROI deaktiviert
            from types import SimpleNamespace
            self.roi = SimpleNamespace(enabled=False, x=0, y=0, width=0, height=0)
        
        # Learning-Phase für Background-Model
        self.is_learning = True
        self.learning_frame_count = 0
        self.learning_frames_required = 30
        
        # Kernels für Morphological Operations
        self.noise_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.cleanup_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        
        # Tracking für Alert-System
        self.last_motion_time = None
        
        self.logger.info(f"MotionDetector initialisiert - Sensitivität: {self.sensitivity}")
    
    def update_sensitivity(self, new_sensitivity: float) -> bool:
        """
        Aktualisiert die Sensitivität zur Laufzeit.
        
        Args:
            new_sensitivity: Neue Sensitivität (0.1-1.0)
            
        Returns:
            True wenn erfolgreich aktualisiert
        """
        if not 0.1 <= new_sensitivity <= 1.0:
            self.logger.warning(f"Ungültige Sensitivität: {new_sensitivity}")
            return False
        
        self.sensitivity = new_sensitivity
        # Sensitivität beeinflusst minimale Konturgröße
        self.min_contour_area = int(self.config.min_contour_area * (2.0 - new_sensitivity))
        
        self.logger.info(f"Sensitivität auf {new_sensitivity} geändert")
        return True
    
    def reset_background_model(self) -> None:
        """Setzt das Background-Model zurück (z.B. bei Lichtwechsel)."""
        self.background_subtractor.clear()
        self.is_learning = True
        self.learning_frame_count = 0
        self.logger.info("Background-Model zurückgesetzt")
    
    def get_last_motion_time(self) -> Optional[float]:
        """Gibt Zeitstempel der letzten Bewegung zurück (für Alert-System)."""
        return self.last_motion_time
    
    def detect_motion(self, frame: np.ndarray) -> MotionResult:
        """
        Erkennt Bewegung in einem Frame.
        
        Args:
            frame: Eingabe-Frame (BGR oder Graustufen)
            
        Returns:
            MotionResult mit Bewegungsinformationen
        """
        timestamp = time.time()
        
        # Input-Validierung
        if frame is None or frame.size == 0:
            self.logger.warning("Ungültiger Frame")
            return MotionResult(False, 0.0, timestamp, False)
        
        try:
            # Frame zu Graustufen konvertieren
            if len(frame.shape) == 3:
                gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray_frame = frame.copy()
            
            # Gausssche Unschärfe für Rauschreduzierung
            blurred = cv2.GaussianBlur(gray_frame, (5, 5), 0)
            
            # ROI anwenden falls aktiviert
            roi_used = False
            if hasattr(self.roi, 'enabled') and self.roi.enabled:
                h, w = blurred.shape[:2]
                x = max(0, min(self.roi.x, w - 1))
                y = max(0, min(self.roi.y, h - 1))
                x2 = max(x + 1, min(self.roi.x + self.roi.width, w))
                y2 = max(y + 1, min(self.roi.y + self.roi.height, h))
                blurred = blurred[y:y2, x:x2]
                roi_used = True
            
            # Learning-Phase verwalten
            if self.is_learning:
                self.learning_frame_count += 1
                if self.learning_frame_count >= self.learning_frames_required:
                    self.is_learning = False
                    self.logger.info("Background-Learning abgeschlossen")
            
            # Background Subtraction
            learning_rate = self.learning_rate if self.is_learning else (self.learning_rate * 0.1)
            fg_mask = self.background_subtractor.apply(blurred, learningRate=learning_rate)
            
            # Schatten entfernen
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
            
            # Morphological Operations für Rauschunterdrückung
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.noise_kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.cleanup_kernel)
            
            # Konturen finden
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Konturen filtern und Gesamtfläche berechnen
            total_area = 0.0
            for contour in contours:
                area = cv2.contourArea(contour)
                if area >= self.min_contour_area:
                    total_area += area
            
            # Bewegungsentscheidung
            motion_detected = not self.is_learning and total_area > 0
            
            # Zeitstempel der letzten Bewegung aktualisieren
            if motion_detected:
                self.last_motion_time = timestamp
            
            return MotionResult(
                motion_detected=motion_detected,
                contour_area=total_area,
                timestamp=timestamp,
                roi_used=roi_used
            )
            
        except Exception as exc:
            self.logger.error(f"Fehler bei Bewegungserkennung: {exc}")
            return MotionResult(False, 0.0, timestamp, False)


def create_motion_detector_from_config(config_path: Optional[str] = None) -> MotionDetector:
    """
    Erstellt einen MotionDetector aus der Konfiguration.
    
    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        
    Returns:
        Konfigurierter MotionDetector
    """
    from ..config import load_config
    
    path = config_path if config_path is not None else "config/config.yaml"
    config = load_config(path)
    motion_config = config.motion_detection
    
    logger = logging.getLogger("motion_detection")
    
    return MotionDetector(motion_config, logger)


class MotionAlertIntegration:
    """
    Einfache Integration zwischen Bewegungserkennung und Alert-System.
    
    Überwacht Bewegungslosigkeit und triggert Alerts basierend auf Alert-Delay.
    """
    
    def __init__(self, motion_detector: MotionDetector, alert_delay_seconds: float = 300.0):
        """
        Args:
            motion_detector: MotionDetector-Instanz
            alert_delay_seconds: Zeit ohne Bewegung bis Alert ausgelöst wird
        """
        self.motion_detector = motion_detector
        self.alert_delay_seconds = alert_delay_seconds
        self.last_alert_sent = None
        self.alert_cooldown = 3600.0  # 1 Stunde zwischen Alerts
        self.logger = logging.getLogger("motion_alert")
    
    def should_send_alert(self) -> bool:
        """
        Prüft ob ein Alert gesendet werden soll.
        
        Returns:
            True wenn Alert gesendet werden soll
        """
        last_motion = self.motion_detector.get_last_motion_time()
        
        # Kein Alert wenn noch nie Bewegung erkannt wurde
        if last_motion is None:
            return False
        
        # Zeit seit letzter Bewegung berechnen
        time_since_motion = time.time() - last_motion
        
        # Alert-Delay noch nicht erreicht
        if time_since_motion < self.alert_delay_seconds:
            return False
        
        # Cooldown zwischen Alerts prüfen
        if self.last_alert_sent is not None:
            time_since_last_alert = time.time() - self.last_alert_sent
            if time_since_last_alert < self.alert_cooldown:
                return False
        
        return True
    
    def mark_alert_sent(self) -> None:
        """Markiert dass ein Alert gesendet wurde."""
        self.last_alert_sent = time.time()
        self.logger.info("Alert gesendet")
    
    def get_time_since_last_motion(self) -> Optional[float]:
        """Gibt Zeit seit letzter Bewegung in Sekunden zurück."""
        last_motion = self.motion_detector.get_last_motion_time()
        if last_motion is None:
            return None
        return time.time() - last_motion
