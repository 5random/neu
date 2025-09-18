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
from typing import Optional, Tuple

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
    upward_score: float = 0.0         # Anteil der Pixel mit Aufwärtsgeschwindigkeit
    avg_vy: float = 0.0               # mittlere vertikale Geschwindigkeit (Pixel/Frame, negativ = oben)
    bubble_count: int = 0             # Anzahl aufwärtsgerichteter Blobs im Größenfenster


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

        # Reusable working buffers (allocated on demand)
        self._buffers_shape: Optional[Tuple[int, int]] = None
        self._blur_buf: Optional[np.ndarray] = None
        self._fg_mask: Optional[np.ndarray] = None

        # Optional downscaling for faster processing (0.25 .. 1.0)
        try:
            scale = float(getattr(config, 'processing_scale', 1.0))
        except Exception:
            scale = 1.0
        self.scale = float(max(0.25, min(1.0, scale)))

        # Tracking für Alert-System
        self.last_motion_time = None
        if self.roi.enabled:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI enabled: {self.roi.x}, {self.roi.y}, {self.roi.width}, {self.roi.height}")
        else:
            self.logger.info(f"MotionDetector initialized - Sensitivity: {self.sensitivity}, ROI disabled or using fallback")

        # Neu: Richtungs-/Bläschen-Parameter und temporale Logik
        self.min_vertical_speed: float = float(getattr(config, 'min_vertical_speed', 0.6))  # px/frame
        self.required_consecutive_frames: int = int(getattr(config, 'required_consecutive_frames', 3))
        self.brightness_threshold: Optional[int] = getattr(config, 'brightness_threshold', None)

        # Bläschen-Größenbereich (in Pixel, vor Downscaling)
        self.bubble_min_area: int = int(getattr(config, 'bubble_min_area', max(25, int(self.min_contour_area * 0.8))))
        self.bubble_max_area: int = int(getattr(config, 'bubble_max_area', max(self.bubble_min_area * 6, self.min_contour_area * 10)))

        # Form-/Drift-Filter
        self.bubble_circularity_min: float = float(getattr(config, 'bubble_circularity_min', 0.55))
        self.bubble_aspect_ratio_max: float = float(getattr(config, 'bubble_aspect_ratio_max', 1.8))
        self.max_h_to_v_ratio: float = float(getattr(config, 'max_h_to_v_ratio', 0.7))  # |vx| / |vy|

        # Cooldown nach Trigger
        self.trigger_cooldown_s: float = float(getattr(config, 'trigger_cooldown_s', 1.0))
        self._last_trigger_time: float = 0.0

        # Global-Brightness-Gate
        self.global_brightness_skip_delta: float = float(getattr(config, 'global_brightness_skip_delta', 8.0))
        self._prev_mean_intensity: Optional[float] = None

        # Randmaske (Prozentualer Rand wird ignoriert)
        self.border_ignore_pct: float = float(getattr(config, 'border_ignore_pct', 0.04))
        self._ignore_mask_shape: Optional[Tuple[int, int]] = None
        self._ignore_mask: Optional[np.ndarray] = None

        # Minimaler Pi‑Modus (vereinfacht): nur Kern-Pipeline
        self.simple_mode = bool(getattr(config, 'simple_mode', True))

        # Zusätzliche Pi‑freundliche Optionen und Zustände
        # Lightweight Lucas–Kanade Flow: Anzahl Punkte begrenzen
        self.flow_max_points = int(getattr(config, 'flow_max_points', 48))
        # Sättigungs-/Reflexions-Unterdrückung
        self.saturated_value = int(getattr(config, 'saturated_value', 245))
        self.saturated_persistence_frames = int(getattr(config, 'saturated_persistence_frames', 6))
        self._sat_accum = None  # type: Optional[np.ndarray]
        # Optional: nur eine horizontale Band nahe Oberfläche auswerten
        self.surface_band_only = bool(getattr(config, 'surface_band_only', False))
        self.surface_band_top_frac = float(getattr(config, 'surface_band_top_frac', 0.10))
        self.surface_band_height_frac = float(getattr(config, 'surface_band_height_frac', 0.20))
        # Temporale Zustände
        self._prev_fg_mask = None  # type: Optional[np.ndarray]
        self._consecutive_up_frames = 0
        self._prev_work_frame = None  # type: Optional[np.ndarray]
        

    def update_sensitivity(self, new_sensitivity: float) -> bool:
        """
        Aktualisiert die Sensitivität zur Laufzeit.
        
        Args:
            new_sensitivity: Neue Sensitivität (0.1-1.0)
            
        Returns:
            True wenn erfolgreich aktualisiert
        """
        if not 0.0 <= new_sensitivity <= 1.0:
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
        """
        Wiederverwendbare Arrays aus Pool.
        
        Returns a zeroed array from the pool to prevent stale data usage.
        """
        key = (shape, dtype)
        if key not in self._frame_pool:
            self._frame_pool[key] = np.empty(shape, dtype=dtype)
        
        # Zero out the array to prevent stale data usage
        array = self._frame_pool[key]
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

            # Optional downscaling for faster processing
            work_frame = roi_frame
            if self.scale < 1.0:
                th, tw = roi_frame.shape[:2]
                dh, dw = max(1, int(th * self.scale)), max(1, int(tw * self.scale))
                work_frame = cv2.resize(roi_frame, (dw, dh), interpolation=cv2.INTER_AREA)

            # Optional: nur eine horizontale Band nahe der Oberfläche betrachten
            if self.surface_band_only:
                hh_full, ww_full = work_frame.shape[:2]
                top = int(hh_full * self.surface_band_top_frac)
                band_h = max(2, int(hh_full * self.surface_band_height_frac))
                bottom = min(hh_full, top + band_h)
                # Sicherstellen, dass die Band gültig ist
                if bottom - top >= 2:
                    work_frame = work_frame[top:bottom, :]

            # Ensure buffers
            hh, ww = work_frame.shape[:2]
            if self._buffers_shape != (hh, ww):
                self._blur_buf = np.empty((hh, ww), dtype=np.uint8)
                self._fg_mask = np.empty((hh, ww), dtype=np.uint8)
                self._buffers_shape = (hh, ww)
                # Rebuild dependent masks/states on size change
                self._ignore_mask = None
                self._sat_accum = np.zeros((hh, ww), dtype=np.uint8)
                self._prev_fg_mask = None

            blur_buf = self._blur_buf if self._blur_buf is not None else np.empty((hh, ww), dtype=np.uint8)
            fg_mask = self._fg_mask if self._fg_mask is not None else np.empty((hh, ww), dtype=np.uint8)

            # Adaptive Gaussian blur kernel size (odd)
            kernel_size = min(5, hh//3, ww//3)
            kernel_size = max(3, kernel_size)
            if kernel_size % 2 == 0:
                kernel_size += 1

            # In-place blur into buffer
            cv2.GaussianBlur(work_frame, (kernel_size, kernel_size), 0, dst=blur_buf)

            # Global-Brightness-Gate
            mean_int = float(blur_buf.mean())
            if self._prev_mean_intensity is None:
                self._prev_mean_intensity = mean_int
            if abs(mean_int - self._prev_mean_intensity) > self.global_brightness_skip_delta:
                # großer globaler Sprung -> Frame überspringen
                self._prev_mean_intensity = mean_int
                self._prev_work_frame = blur_buf.copy()
                self._prev_fg_mask = None
                self._consecutive_up_frames = 0
                return MotionResult(False, 0.0, timestamp, roi_used)

            # Learning-Phase verwalten
            if self.is_learning:
                self.learning_frame_count += 1
                if self.learning_frame_count >= self.learning_frames_required:
                    self.is_learning = False
                    self.logger.info("Background learning completed")

            # Background Subtraction into preallocated fg mask
            # Bei viel Bewegung Learning-Rate auf 0 setzen (kein Weglernen)
            provisional_fg = self.background_subtractor.apply(blur_buf, fgmask=fg_mask, learningRate=self.learning_rate if self.is_learning else (self.learning_rate * 0.1))
            fg_mask[:] = provisional_fg

            # Threshold to 0/255; overwrite same buffer
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

            # Optional: Helligkeitsfilter (nur helle Bereiche als Bewegung)
            if isinstance(self.brightness_threshold, (int, float)) and 0 <= self.brightness_threshold <= 255:
                # Threshold auf geblurrtem Frame (robuster gegen Rauschen)
                _, bright_mask = cv2.threshold(blur_buf, int(self.brightness_threshold), 255, cv2.THRESH_BINARY)
                cv2.bitwise_and(fg_mask, bright_mask, dst=fg_mask)

            # Überbelichtete Bereiche (Reflexionen) mit Persistenz maskieren
            if self._sat_accum is None or self._sat_accum.shape != fg_mask.shape:
                self._sat_accum = np.zeros_like(fg_mask)
            sat_now = (work_frame >= self.saturated_value).astype(np.uint8)
            # Persistenz aufbauen und leicht abbauen
            self._sat_accum = np.clip(self._sat_accum + sat_now * 2, 0, 255)
            self._sat_accum = (self._sat_accum - 1).clip(0, 255).astype(np.uint8)
            sat_persist = (self._sat_accum >= (self.saturated_persistence_frames * 2)).astype(np.uint8) * 255
            inv_sat = cv2.bitwise_not(sat_persist)
            cv2.bitwise_and(fg_mask, inv_sat, dst=fg_mask)

            # Morphological operations in-place for noise cleanup
            cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.noise_kernel, dst=fg_mask)
            cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.cleanup_kernel, dst=fg_mask)

            # Randmaske anwenden
            ignore_mask = self._build_ignore_mask(hh, ww)
            cv2.bitwise_and(fg_mask, ignore_mask, dst=fg_mask)

            # Fast-path: too few active pixels -> no motion
            min_area_ds = max(1, int(self.min_contour_area * (self.scale * self.scale)))
            nz = int(cv2.countNonZero(fg_mask))
            if self.is_learning or nz < min_area_ds:
                # Prev-Frame aktualisieren, um Flow im nächsten Frame zu ermöglichen
                self._prev_work_frame = blur_buf.copy()
                self._prev_fg_mask = fg_mask.copy()
                self._prev_mean_intensity = mean_int
                self._consecutive_up_frames = 0
                return MotionResult(False, 0.0, timestamp, roi_used)

            # Einfacher Minimalmodus: keine Komponenten/Fluss/Blob-Formtests
            if self.simple_mode:
                # Jede ausreichende Aktivität zählt als "Up"-Frame
                self._consecutive_up_frames += 1
                confirmed = (self._consecutive_up_frames >= self.required_consecutive_frames)
                motion_detected = (not self.is_learning) and confirmed

                # Cooldown gegen Mehrfach-Trigger
                if motion_detected and (timestamp - self._last_trigger_time) < self.trigger_cooldown_s:
                    motion_detected = False
                    self._consecutive_up_frames = 0
                if motion_detected:
                    self._last_trigger_time = timestamp

                # Save prevs
                self._fg_mask = fg_mask
                self._prev_fg_mask = fg_mask.copy()
                self._prev_work_frame = blur_buf.copy()
                self._prev_mean_intensity = mean_int

                if motion_detected:
                    self.last_motion_time = timestamp

                return MotionResult(
                    motion_detected=motion_detected,
                    contour_area=float(nz),
                    timestamp=timestamp,
                    roi_used=roi_used,
                    upward_score=0.0,
                    avg_vy=0.0,
                    bubble_count=0
                )

            # Connected components only if potential motion
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg_mask)

            # Gesamtfläche (alle Komponenten >= min_area_ds)
            total_area = 0.0
            if num_labels > 1:
                areas = stats[1:, cv2.CC_STAT_AREA]
                total_area = float(np.sum(areas[areas >= min_area_ds]))

            # Lightweight Optical Flow (Lucas–Kanade) für Richtungsabschätzung
            avg_vy = 0.0
            upward_score = 0.0
            bubble_count = 0

            if self._prev_work_frame is not None and self._prev_fg_mask is not None:
                try:
                    p0 = cv2.goodFeaturesToTrack(
                        self._prev_work_frame,
                        maxCorners=self.flow_max_points,
                        qualityLevel=0.01,
                        minDistance=5,
                        mask=self._prev_fg_mask
                    )
                    if p0 is not None and len(p0) > 0:
                        # Ensure correct dtype and provide initial guess for nextPts
                        if p0.dtype != np.float32:
                            p0 = p0.astype(np.float32)
                        p1, st, err = cv2.calcOpticalFlowPyrLK(
                            self._prev_work_frame, blur_buf, p0, p0,
                            winSize=(15, 15), maxLevel=2,
                            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
                        )
                        good_new = p1[st == 1] if p1 is not None else np.empty((0, 2))
                        good_old = p0[st == 1] if p0 is not None else np.empty((0, 2))
                        if good_new.shape[0] > 0:
                            dy = (good_new[:, 1] - good_old[:, 1]).astype(np.float32)  # + nach unten, - nach oben
                            avg_vy = float(dy.mean()) if dy.size > 0 else 0.0
                            up_mask = dy < -self.min_vertical_speed
                            upward_score = float(np.count_nonzero(up_mask)) / float(dy.size)

                    # Bubble-Kandidaten anhand Größe und Form zählen
                    min_area_b_ds = max(1, int(self.bubble_min_area * (self.scale * self.scale)))
                    max_area_b_ds = max(min_area_b_ds, int(self.bubble_max_area * (self.scale * self.scale)))
                    for lbl in range(1, num_labels):
                        area = int(stats[lbl, cv2.CC_STAT_AREA])
                        if area < min_area_b_ds or area > max_area_b_ds:
                            continue
                        x = stats[lbl, cv2.CC_STAT_LEFT]
                        y0 = stats[lbl, cv2.CC_STAT_TOP]
                        w_ = stats[lbl, cv2.CC_STAT_WIDTH]
                        h_ = stats[lbl, cv2.CC_STAT_HEIGHT]
                        roi_lbl = (labels[y0:y0+h_, x:x+w_] == lbl).astype(np.uint8) * 255
                        contours, _h = cv2.findContours(roi_lbl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        circ_ok, ar_ok = True, True
                        if contours:
                            cnt = max(contours, key=cv2.contourArea)
                            per = float(cv2.arcLength(cnt, True))
                            a = float(cv2.contourArea(cnt))
                            circularity = (4.0 * np.pi * a / (per * per)) if per > 1e-3 else 0.0
                            circ_ok = (circularity >= self.bubble_circularity_min)
                            ar = max(w_, h_) / max(1.0, min(w_, h_))
                            ar_ok = (ar <= self.bubble_aspect_ratio_max)
                        if not (circ_ok and ar_ok):
                            continue
                        bubble_count += 1

                except cv2.error:
                    avg_vy = 0.0
                    upward_score = 0.0
                    bubble_count = 0

            # Trigger-Bedingung
            up_condition = (bubble_count >= 1 and upward_score > 0.5) or (avg_vy < -self.min_vertical_speed and total_area >= min_area_ds)

            if up_condition:
                self._consecutive_up_frames += 1
            else:
                self._consecutive_up_frames = 0

            confirmed = (self._consecutive_up_frames >= self.required_consecutive_frames)
            motion_detected = not self.is_learning and confirmed

            # Cooldown gegen Mehrfach-Trigger
            if motion_detected and (timestamp - self._last_trigger_time) < self.trigger_cooldown_s:
                motion_detected = False
                self._consecutive_up_frames = 0
            if motion_detected:
                self._last_trigger_time = timestamp

            # Save mask and prev frame
            self._fg_mask = fg_mask
            self._prev_fg_mask = fg_mask.copy()
            self._prev_work_frame = blur_buf.copy()
            self._prev_mean_intensity = mean_int

            if motion_detected:
                self.last_motion_time = timestamp

            return MotionResult(
                motion_detected=motion_detected,
                contour_area=total_area,
                timestamp=timestamp,
                roi_used=roi_used,
                upward_score=upward_score,
                avg_vy=avg_vy,
                bubble_count=bubble_count
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
        if x2 <= x1 or y2 <= y1 or x1 < 0 or y1 < 0 or x2 > w or y2 > h or x1 >= w or y1 >= h:
            self.logger.warning(f"Invalid ROI bounds: ({x1}, {y1}) to ({x2}, {y2})")
            return gray_frame
        
        return gray_frame[y1:y2, x1:x2]
    
    def cleanup(self) -> None:
        """Clean up resources when detector is no longer needed."""
        self.background_subtractor.clear()
        self._frame_pool.clear()
        self.logger.info("MotionDetector cleaned up")

    def _build_ignore_mask(self, hh: int, ww: int) -> np.ndarray:
        """Erzeugt eine 0/255-Maske, die Randbereiche ignoriert."""
        if self._ignore_mask is not None and self._ignore_mask_shape == (hh, ww):
            return self._ignore_mask
        mask = np.full((hh, ww), 255, dtype=np.uint8)
        b = int(max(1, self.border_ignore_pct * min(hh, ww)))
        mask[:b, :] = 0
        mask[-b:, :] = 0
        mask[:, :b] = 0
        mask[:, -b:] = 0
        self._ignore_mask = mask
        self._ignore_mask_shape = (hh, ww)
        return mask

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
