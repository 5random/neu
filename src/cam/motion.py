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
from typing import Optional, Tuple, Dict, Any

from ..config import MotionDetectionConfig, ROI, get_logger

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
        self.logger = logger or get_logger('motion')

        # Validate configuration
        if not hasattr(config, 'sensitivity') or not 0.01 <= config.sensitivity <= 1.0:
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
        except (ValueError, AttributeError) as exc:
            self.logger.warning(f"ROI-Setup failed: {exc}, using fallback ROI")
            # Fallback: ROI deaktiviert
            self.roi = ROI(enabled=False, x=0, y=0, width=0, height=0)
        
        # Learning-Phase für Background-Model
        self.is_learning = True
        self.learning_frame_count = 0
        self.learning_frames_required = 32
        
        # Kernels für Morphological Operations
        self.noise_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.cleanup_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # Memory-Pool für wiederverwendbare Arrays (with size limit to prevent leaks)
        self._frame_pool: Dict[Tuple[Tuple[int, int], Any], np.ndarray] = {}
        self._max_pool_entries = 5  # Limit pool size to prevent memory leaks

        # Tracking für Alert-System
        self.last_motion_time: Optional[float] = None
        if self.roi.enabled:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI enabled: {self.roi.x}, {self.roi.y}, {self.roi.width}, {self.roi.height}")
        else:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI disabled or using fallback")

    # -----------------------------
    # Public ROI utility functions
    # -----------------------------
    @staticmethod
    def normalize_roi(x: int, y: int, w: int, h: int, frame_w: int, frame_h: int, *, min_size: int = 1) -> Tuple[int, int, int, int]:
        """
        Clamp and normalize an ROI to valid in-frame bounds.

        Behavior:
        - x,y are clamped into [0, frame_w-1] / [0, frame_h-1]
        - w,h are forced to be at least min_size
        - width/height are reduced where necessary to keep x+w <= frame_w and y+h <= frame_h

        Note: If the original ROI is partially or fully outside the frame, the
        normalized ROI will be adjusted to lie within the frame. If the ROI collapses
        to a very small area, callers may still choose to treat it as invalid based on
        their own minimum sizes.

        Args:
            x, y, w, h: ROI top-left and size in pixels
            frame_w, frame_h: Dimensions of the frame in pixels
            min_size: Minimum width/height to enforce during normalization (default 1)

        Returns:
            Tuple (nx, ny, nw, nh) representing a valid ROI within the frame.
        """
        # Guard against degenerate frame sizes
        frame_w = max(1, int(frame_w))
        frame_h = max(1, int(frame_h))

        x = max(0, min(int(x), frame_w - 1))
        y = max(0, min(int(y), frame_h - 1))

        w = max(int(min_size), int(w))
        h = max(int(min_size), int(h))

        if x + w > frame_w:
            w = frame_w - x
        if y + h > frame_h:
            h = frame_h - y

        # Ensure final sizes are at least min_size if possible (may already be at boundary)
        if w < min_size and x == 0:
            w = min_size if min_size <= frame_w else frame_w
        if h < min_size and y == 0:
            h = min_size if min_size <= frame_h else frame_h

        # Final clamp again (in case min_size expansion exceeded bounds)
        if x + w > frame_w:
            w = frame_w - x
        if y + h > frame_h:
            h = frame_h - y

        # Ensure strictly positive sizes
        w = max(1, w)
        h = max(1, h)
        return x, y, w, h

    @staticmethod
    def is_valid_roi(x: int, y: int, w: int, h: int, frame_w: int, frame_h: int, *, min_size: int = 1) -> bool:
        """
        Validate that an ROI lies within frame bounds and meets minimum size.

        Returns True only if:
        - 0 <= x < frame_w and 0 <= y < frame_h
        - w >= min_size and h >= min_size
        - x + w <= frame_w and y + h <= frame_h
        """
        if frame_w <= 0 or frame_h <= 0:
            return False
        if x < 0 or y < 0 or x >= frame_w or y >= frame_h:
            return False
        if w < min_size or h < min_size:
            return False
        if x + w > frame_w or y + h > frame_h:
            return False
        return True

    def update_sensitivity(self, new_sensitivity: float) -> bool:
        """
        Aktualisiert die Sensitivität zur Laufzeit.
        
        Args:
            new_sensitivity: Neue Sensitivität (0.01-1.0)
            
        Returns:
            True wenn erfolgreich aktualisiert oder geclampt
        """
        if not 0.01 <= new_sensitivity <= 1.0:
            self.logger.warning(f"Invalid sensitivity: {new_sensitivity}")
            # Clamp sensitivity into valid range
            if new_sensitivity < 0.01:
                self.logger.warning(f"Sensitivity {new_sensitivity} below minimum; clamping to 0.01")
                new_sensitivity = 0.01
            elif new_sensitivity > 1.0:
                self.logger.warning(f"Sensitivity {new_sensitivity} above maximum; clamping to 1.0")
                new_sensitivity = 1.0
            # Weiter mit geclamptem Wert - KEIN return False mehr
        
        self.sensitivity = new_sensitivity
        # Sensitivität beeinflusst minimale Konturgröße
        scale = 20.0                      # 20-fach Spielraum
        self.min_contour_area = int(
            self.config.min_contour_area * (1 + (scale - 1) * (1 - new_sensitivity))
        )

        self.logger.info(f"Sensitivity changed to {new_sensitivity}")
        return True  # Immer True wenn die Änderung angewendet wurde
    
    def reset_background_model(self) -> None:
        """Setzt das Background-Model zurück (z.B. bei Lichtwechsel)."""
        self.background_subtractor.clear()
        self.is_learning = True
        self.learning_frame_count = 0
        self.logger.info("Background model reset")

    def get_last_motion_time(self) -> Optional[float]:
        """Gibt Zeitstempel der letzten Bewegung zurück (für Alert-System)."""
        return self.last_motion_time
    
    def _get_working_array(
        self,
        shape: Tuple[int, int],
        dtype: Any = np.uint8,
        *,
        zero_fill: bool = False,
    ) -> np.ndarray:
        """
        Wiederverwendbare Arrays aus Pool.
        
        Returns a reusable array from the pool. Callers may request zero-filled
        storage when stale data would affect the result.
        """
        key = (shape, dtype)
        if key not in self._frame_pool:
            # Evict oldest entries if pool is full (simple FIFO-style eviction)
            if len(self._frame_pool) >= self._max_pool_entries:
                # Remove first (oldest) entry
                try:
                    oldest_key = next(iter(self._frame_pool))
                    del self._frame_pool[oldest_key]
                except (StopIteration, KeyError):
                    pass
            self._frame_pool[key] = np.empty(shape, dtype=dtype)
        
        array = self._frame_pool[key]
        if zero_fill:
            array.fill(0)
        return array
    
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
        
        if len(frame.shape) < 2 or frame.shape[0] < 10 or frame.shape[1] < 10:
            self.logger.warning("received Frame too small for processing")
            return MotionResult(False, 0.0, timestamp, False)
        
        try:
            # Apply ROI on the original frame before expensive grayscale conversion.
            roi_used = False
            roi_frame = self._apply_roi(frame)
            if roi_frame is not frame:
                roi_used = True

            # Validate final ROI frame size
            if roi_frame.shape[0] < 10 or roi_frame.shape[1] < 10:
                self.logger.warning("ROI too small for processing, using full frame")
                roi_frame = frame
                roi_used = False

            if len(roi_frame.shape) == 3:
                gray_frame = self._get_working_array(roi_frame.shape[:2], np.uint8, zero_fill=False)
                cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY, dst=gray_frame)
            else:
                gray_frame = roi_frame.copy()

            # --- Downscaling Optimization ---
            # Process on a smaller frame if the ROI is large (e.g. > 640px width)
            # This significantly reduces CPU usage on Raspberry Pi
            target_width = max(1, int(getattr(self.config, 'processing_max_width', 640) or 640))
            scale_factor = 1.0
            processing_frame = gray_frame
            
            # Guard against division by zero
            roi_width = gray_frame.shape[1]
            if roi_width > target_width and roi_width > 0:
                scale_factor = target_width / roi_width
                # Keep aspect ratio
                new_width = target_width
                new_height = max(1, int(gray_frame.shape[0] * scale_factor))  # Ensure >= 1
                
                # Zusätzliche Sicherheitsprüfung
                if new_width < 1 or new_height < 1:
                    self.logger.warning(f"Invalid scaled dimensions: {new_width}x{new_height}, using original")
                    processing_frame = gray_frame
                    scale_factor = 1.0
                else:
                    processing_frame = cv2.resize(gray_frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
            
            # Adjust min_contour_area for the scaled frame
            # Area scales with square of linear scale
            effective_min_area = self.min_contour_area * (scale_factor * scale_factor)

            # Adaptive Gaussian blur kernel size (based on processing frame)
            kernel_size = min(5, processing_frame.shape[0]//3, processing_frame.shape[1]//3)
            kernel_size = max(3, kernel_size)  # Minimum size 3
            if kernel_size % 2 == 0:
                kernel_size += 1  # Ensure odd number

            blurred = cv2.GaussianBlur(processing_frame, (kernel_size, kernel_size), 0)

            # Learning-Phase verwalten
            if self.is_learning:
                self.learning_frame_count += 1
                if self.learning_frame_count >= self.learning_frames_required:
                    self.is_learning = False
                    self.logger.info("Background learning completed")

            # Background Subtraction
            learning_rate = self.learning_rate if self.is_learning else (self.learning_rate * 0.1)
            fg_mask = self.background_subtractor.apply(blurred, learningRate=learning_rate)
            
            # Schatten entfernen
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
            
            # Morphological Operations für Rauschunterdrückung
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.noise_kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.cleanup_kernel)
            
            # Verbundene Komponenten finden
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg_mask)

            # Gesamtfläche berechnen basierend auf Komponentenstats
            total_area_scaled = 0.0
            if num_labels > 1:
                areas = stats[1:, cv2.CC_STAT_AREA]  # Hintergrund ausschließen
                # Filter with scaled threshold
                valid_areas = areas[areas >= effective_min_area]
                total_area_scaled = float(np.sum(valid_areas))
            
            # Scale area back to original resolution for consistency
            total_area = total_area_scaled / (scale_factor * scale_factor)
            
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

        # --- Polygon ROI Support ---
        if hasattr(self.roi, 'points') and self.roi.points and len(self.roi.points) >= 3:
            try:
                pts = np.array(self.roi.points, dtype=np.int32)
                
                # 1. Compute bounding box for cropping (Performance optimization)
                x, y, w_rect, h_rect = cv2.boundingRect(pts)
                
                # Clamp bounding box to frame
                x = max(0, min(x, w - 1))
                y = max(0, min(y, h - 1))
                w_rect = max(1, min(w_rect, w - x))
                h_rect = max(1, min(h_rect, h - y))
                
                # 2. Create mask on the cropped area
                # We create a mask of the size of the bounding rect to save memory/cpu
                mask = np.zeros((h_rect, w_rect), dtype=np.uint8)
                
                # Offset points to be relative to the bounding rect
                pts_offset = pts - np.array([x, y])
                
                cv2.fillPoly(mask, [pts_offset], 255)
                
                # 3. Crop the frame
                cropped_frame = gray_frame[y:y+h_rect, x:x+w_rect]
                
                # 4. Apply mask
                masked_frame = cv2.bitwise_and(cropped_frame, cropped_frame, mask=mask)
                
                return masked_frame
                
            except Exception as e:
                self.logger.error(f"Error applying Polygon ROI: {e}")
                # Fallback to full frame or rectangle if possible
                return gray_frame

        # --- Rectangle ROI Fallback ---
        # Normalize ROI to frame bounds (min size 1 pixel)
        nx, ny, nw, nh = MotionDetector.normalize_roi(self.roi.x, self.roi.y, self.roi.width, self.roi.height, w, h, min_size=1)

        # If ROI degenerates to extremely small area, fall back handled by caller
        if nw <= 0 or nh <= 0:
            self.logger.warning("ROI normalization produced non-positive size; using full frame")
            return gray_frame

        x2 = nx + nw
        y2 = ny + nh
        # Extra safety (should be guaranteed by normalize)
        if nx < 0 or ny < 0 or x2 > w or y2 > h or nx >= w or ny >= h:
            self.logger.warning(f"Invalid ROI after normalization: ({nx}, {ny}) to ({x2}, {y2})")
            return gray_frame

        return gray_frame[ny:y2, nx:x2]
    
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
    
    logger = get_logger("motion_detection")
    
    return MotionDetector(motion_config, logger)
