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
    from ..config import MotionDetectionConfig, ROI, logger


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

        # Validate configuration
        if not hasattr(config, 'sensitivity') or not 0.1 <= config.sensitivity <= 1.0:
            raise ValueError("Invalid sensitivity in config")
        if not hasattr(config, 'min_contour_area') or config.min_contour_area < 1:
            raise ValueError("Invalid min_contour_area in config")
        if not hasattr(config, 'background_learning_rate') or not 0.001 <= config.background_learning_rate <= 1.0:
            raise ValueError("Invalid background_learning_rate in config")
        
        # OpenCV Background Subtractor
        self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
            detectShadows=False,
            varThreshold=6,
            history=512
        )
        
        # Bewegungsparameter
        self.sensitivity = config.sensitivity
        self.min_contour_area = config.min_contour_area
        self.learning_rate = config.background_learning_rate
        
        # ROI Setup
        try:
            self.roi = config.get_roi()
        except Exception as exc:
            self.logger.warning(f"ROI-Setup failed: {exc}, using fallback ROI")
            # Fallback: ROI deaktiviert
            from types import SimpleNamespace
            self.roi = SimpleNamespace(enabled=False, x=0, y=0, width=0, height=0)
        
        # Learning-Phase für Background-Model
        self.is_learning = True
        self.learning_frame_count = 0
        self.learning_frames_required = 32
        
        # Kernels für Morphological Operations
        self.noise_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.cleanup_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # Memory-Pool für wiederverwendbare Arrays
        self._frame_pool = {}
        self._mask_pool = {}

        # Tracking für Alert-System
        self.last_motion_time = None
        if self.roi.enabled:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI enabled: {self.roi.x}, {self.roi.y}, {self.roi.width}, {self.roi.height}")
        else:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI disabled or using fallback")

    def update_sensitivity(self, new_sensitivity: float) -> bool:
        """
        Aktualisiert die Sensitivität zur Laufzeit.
        
        Args:
            new_sensitivity: Neue Sensitivität (0.1-1.0)
            
        Returns:
            True wenn erfolgreich aktualisiert
        """
        if not 0.1 <= new_sensitivity <= 1.0:
            self.logger.warning(f"Invalid sensitivity: {new_sensitivity}")
            return False
        
        self.sensitivity = new_sensitivity
        # Sensitivität beeinflusst minimale Konturgröße
        scale = 20.0                      # 10-fach Spielraum
        self.min_contour_area = int(
            self.config.min_contour_area * (1 + (scale - 1) * (1 - new_sensitivity))
        )

        self.logger.info(f"Sensitivity changed to {new_sensitivity}")
        return True
    
    def reset_background_model(self) -> None:
        """Setzt das Background-Model zurück (z.B. bei Lichtwechsel)."""
        self.background_subtractor.clear()
        self.is_learning = True
        self.learning_frame_count = 0
        self.logger.info("Background model reset")

    def get_last_motion_time(self) -> Optional[float]:
        """Gibt Zeitstempel der letzten Bewegung zurück (für Alert-System)."""
        return self.last_motion_time
    
    def _get_working_array(self, shape: Tuple[int, int], dtype=np.uint8) -> np.ndarray:
        """Wiederverwendbare Arrays aus Pool."""
        key = (shape, dtype)
        if key not in self._frame_pool:
            self._frame_pool[key] = np.empty(shape, dtype=dtype)
        return self._frame_pool[key]
    
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
            self.logger.warning("Invalid frame")
            return MotionResult(False, 0.0, timestamp, False)
        
        if len(frame.shape) <2 or frame.shape[0] < 10 or frame.shape[1] < 10:
            self.logger.warning("received Frame too small for processing")
            return MotionResult(False, 0.0, timestamp, False)
        
        try:
            # Frame zu Graustufen konvertieren
            h, w = frame.shape[:2]
            if len(frame.shape) == 3:
                gray_frame = self._get_working_array((h, w), np.uint8)
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY, dst=gray_frame)
            else:
                gray_frame = frame.copy()
            
            # ROI direkt nach Graustufen anwenden
            roi_used = False
            roi_frame = self._apply_roi(gray_frame)
            if roi_frame is not gray_frame:
                roi_used = True

            # Validate final ROI frame size
            if roi_frame.shape[0] < 10 or roi_frame.shape[1] < 10:
                self.logger.warning("ROI too small for processing, using full frame")
                roi_frame = gray_frame
                roi_used = False

            # Adaptive Gaussian blur kernel size
            kernel_size = min(5, roi_frame.shape[0]//3, roi_frame.shape[1]//3)
            kernel_size = max(3, kernel_size)  # Minimum size 3
            if kernel_size % 2 == 0:
                kernel_size += 1  # Ensure odd number

            blur_buf = self._get_working_array(roi_frame.shape)
            cv2.GaussianBlur(roi_frame, (kernel_size, kernel_size), 0, dst=blur_buf)

            # Learning-Phase verwalten
            if self.is_learning:
                self.learning_frame_count += 1
                if self.learning_frame_count >= self.learning_frames_required:
                    self.is_learning = False
                    self.logger.info("Background learning completed")

            # Background Subtraction
            learning_rate = self.learning_rate if self.is_learning else (self.learning_rate * 0.1)
            fg_mask = self._get_working_array(roi_frame.shape)
            self.background_subtractor.apply(blur_buf, fg_mask, learningRate=learning_rate)

            # Schatten entfernen
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY, dst=fg_mask)

            # Morphological Operations für Rauschunterdrückung
            cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.noise_kernel, dst=fg_mask)
            cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.cleanup_kernel, dst=fg_mask)
            
            # Verbundene Komponenten finden
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg_mask)

            # Gesamtfläche berechnen basierend auf Komponentenstats
            total_area = 0.0
            if num_labels > 1:
                areas = stats[1:, cv2.CC_STAT_AREA]  # Hintergrund ausschließen
                total_area = float(np.sum(areas[areas >= self.min_contour_area]))
            
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
        
        except cv2.error as exc:
            self.logger.error(f"OpenCV error detecting motion: {exc}")
            return MotionResult(False, 0.0, timestamp, False)
        except Exception as exc:
            self.logger.error(f"Unexpected error detecting motion: {exc}")
            return MotionResult(False, 0.0, timestamp, False)

    def _apply_roi(self, gray_frame: np.ndarray) -> np.ndarray:
        """
        Applies ROI to frame with safe bounds checking.
        
        Args:
            gray_frame: Input grayscale frame
            
        Returns:
            ROI frame or original frame if ROI invalid
        """
        if not (hasattr(self.roi, 'enabled') and self.roi.enabled):
            return gray_frame
        
        h, w = gray_frame.shape[:2]
        
        # Calculate safe ROI bounds
        x1 = max(0, min(self.roi.x, w - 1))
        y1 = max(0, min(self.roi.y, h - 1))
        x2 = max(x1 + 1, min(self.roi.x + self.roi.width, w))
        y2 = max(y1 + 1, min(self.roi.y + self.roi.height, h))
        
        # Validate ROI dimensions
        if x2 <= x1 or y2 <= y1:
            self.logger.warning(f"Invalid ROI bounds: ({x1}, {y1}) to ({x2}, {y2})")
            return gray_frame
        
        roi_width = x2 - x1
        roi_height = y2 - y1
        
        # Check minimum size and if needed, adjust ROI to ensure minimum size
        if roi_width < 20 or roi_height < 20:
            self.logger.warning(f"ROI too small: {roi_width}x{roi_height}, expanding for stability")
            
            # Minimale sinnvolle Größe für OpenCV-Operationen
            min_size = 30
            
            # Zentrum der aktuellen ROI beibehalten
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            
            # Neue ROI um Zentrum berechnen
            new_x1 = max(0, center_x - min_size // 2)
            new_y1 = max(0, center_y - min_size // 2)
            new_x2 = min(w, new_x1 + min_size)
            new_y2 = min(h, new_y1 + min_size)
            
            # Falls an Bildrand: ROI entsprechend verschieben
            if new_x2 - new_x1 < min_size and new_x1 > 0:
                new_x1 = max(0, new_x2 - min_size)
            if new_y2 - new_y1 < min_size and new_y1 > 0:
                new_y1 = max(0, new_y2 - min_size)
            
            x1, y1, x2, y2 = new_x1, new_y1, new_x2, new_y2
            self.logger.info(f"ROI expanded to: ({x1}, {y1}) - ({x2}, {y2})")
            roi_frame = gray_frame[y1:y2, x1:x2]
            return roi_frame
        
        return gray_frame[y1:y2, x1:x2]
    
    def cleanup(self) -> None:
        """Clean up resources when detector is no longer needed."""
        self.background_subtractor.clear()
        self._frame_pool.clear()
        self.logger.info("MotionDetector cleaned up")

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
