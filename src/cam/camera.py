from __future__ import annotations

import base64
import platform
import signal
import threading
import asyncio
import concurrent.futures
import collections
from contextlib import contextmanager
from functools import lru_cache
import time
from typing import Callable, Optional

import cv2
import numpy as np
from fastapi import Response
from nicegui import Client, app, core, run, ui

from src.config import AppConfig, WebcamConfig, UVCConfig, load_config, save_config
from .motion import MotionResult, MotionDetector


class Camera:
    """Kameraklasse mit vollständiger (und funktionierender) UVC‑Steuerung."""

    # ------------------------- Initialisierung ------------------------- #

    def __init__(self, config: AppConfig) -> None:
        # -- Config & Logger --
        self.app_config: AppConfig = config
        self.webcam_config: WebcamConfig = self.app_config.webcam
        self.uvc_config: UVCConfig = self.app_config.uvc_controls
        self.logger = self.app_config.logging.setup_logger("camera")

        # Measurement config for alerts
        self.measurement_config = self.app_config.measurement

        # Ensure image save path exists if alert images should be saved
        if getattr(self.measurement_config, 'save_alert_images', False):
            self.measurement_config.ensure_save_path()

        # -- Interne State‑Variablen --
        self.video_capture: Optional[cv2.VideoCapture] = None
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.is_running = False
        self.motion_callback: Optional[Callable[[np.ndarray, MotionResult], None]] = None

        # -- Motion Detection --
        self.motion_detector: Optional[MotionDetector] = None
        self.motion_enabled = False
        self.frame_count = 0

        # Letztes Bewegungsergebnis für Metrics
        self.last_motion_result: Optional[MotionResult] = None

        # Reconnection settings
        self.reconnect_interval = 5
        self.max_reconnect_attempts = 5
        self._reconnect_attempts = 0
        
        # -- Platzhalterbild für fehlende Kamera --
        black_1px = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAA"
            "AANJREFUGFdjYGBg+A8AAQQBAHAgZQsAAAAASUVORK5CYII="
        )
        self.placeholder = Response(
            content=base64.b64decode(black_1px.encode("ascii")),
            media_type="image/png",
        )

        self.frame_thread: Optional[threading.Thread] = None

        self._status_cache: dict = {}
        self._status_cache_time: float = 0.0
        self._uvc_cache_values: dict = {}
        self._uvc_cache_time: float = 0.0

        self._max_pool_size = 3
        self._frame_pool: collections.deque = collections.deque(maxlen=self._max_pool_size)

        self._jpeg_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

        # -- Backend je nach Plattform explizit wählen --
        system = platform.system()
        if system == "Windows":
            self.backend = cv2.CAP_DSHOW  # DirectShow - garantiert alle Regler
        elif system == "Linux":
            self.backend = cv2.CAP_V4L2   # Video4Linux2
        else:
            # macOS oder unbekannt → OpenCV entscheidet selbst
            self.backend = 0
            self.logger.warning("Unknown OS - use standard backend (can restrict controllers)")

        # -- Kamera initialisieren --
        try:
            self._initialize_camera()
            self.logger.info("Camera initialized successfully")
        except Exception as exc:
            self.logger.error(f"Camera initialization failed: {exc}")
            self.video_capture = None

    def _initialize_camera(self) -> None:
        video_capture: Optional[cv2.VideoCapture] = None
        try:
            self.logger.info(
                f"Open camera index {self.webcam_config.camera_index} with backend {self.backend}"
            )

            with self.frame_lock:
                if self.video_capture:
                    try:
                        self.video_capture.release()
                    except Exception as e:
                        pass
                    finally:
                        self.video_capture = None
                    
            video_capture = cv2.VideoCapture(
                self.webcam_config.camera_index, self.backend
            )
            if video_capture:
                if not video_capture.isOpened():
                    video_capture.release()
                    self.video_capture = None
                    raise RuntimeError(f"Camera {self.webcam_config.camera_index} could not be opened")

                self._set_camera_properties(video_capture)

                # Test‑Frame zum Validieren
                ret, _ = video_capture.read()
                if not ret:
                    raise RuntimeError("No frame received from camera during initialization")

            with self.frame_lock:
                self.video_capture = video_capture

            self._apply_uvc_controls()
            self.logger.info("Camera successfully initialized")

        except Exception as exc:
            self.logger.error(f"Initialization failed: {exc}")
            if video_capture is not None:
                try:
                    video_capture.release()
                except Exception as e:
                    self.logger.debug(f"Error during video_capture cleanup: {e}")

            with self.frame_lock:
                if self.video_capture is not None:
                    try:
                        self.video_capture.release()
                    except Exception as e:
                        self.logger.debug(f"Error during self.video_capture cleanup: {e}")
                    finally:
                        self.video_capture = None
            raise

    def _set_camera_properties(self, capture: cv2.VideoCapture) -> None:
        """Grundlegende Auflösung / FPS etc. setzen."""

        res = self.webcam_config.get_default_resolution()
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_FPS, self.webcam_config.fps)
        self._safe_set_for_capture(capture, cv2.CAP_PROP_BUFFERSIZE, 1)

        self.logger.info(
            "current camera status: %dx%d @ %.1f FPS",
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            capture.get(cv2.CAP_PROP_FPS),
        )

    # --------------------- Low‑Level‑Hilfsfunktionen ------------------- #

    def _safe_set(self, prop: int, value: float) -> bool:
        """Setzt ein VideoCapture‑Property und prüft, ob es übernommen wurde."""
        if not self.video_capture or not self.video_capture.isOpened():
            self.logger.error("safe_set: Camera not available")
            return False
        try:
            ok = self.video_capture.set(prop, value)
            actual = self.video_capture.get(prop)
            if not ok or abs(actual - value) > 1e-3:
                self.logger.debug(f"Property {prop} to set to={value}, received={actual}; tolerance=1e-3")
                return False
            return True
        except cv2.error as e:
            self.logger.error(f"OpenCV error setting property {prop}: {e}")
            return False        
        except Exception as e:
            self.logger.error(f"Unexpected error setting property {prop}: {e}")
            return False
    
    def _safe_set_for_capture(self, capture: cv2.VideoCapture, prop: int, value: float) -> bool:
        """Thread-sichere Property-Setter für spezifische VideoCapture"""
        try:
            ok = capture.set(prop, value)
            actual = capture.get(prop)
            if not ok or abs(actual - value) > 1e-3:
                self.logger.debug(f"Property {prop} to set to={value}, received={actual}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error setting property {prop}: {e}")
            return False
    
    def save_uvc_config(self, path: Optional[str] = None) -> bool:
            """Speichert die aktuellen UVC-Einstellungen zurück in die Config-Datei."""

            if not getattr(self, '_config_dirty', True):
                self.logger.debug("UVC configuration not changed, skipping save")
                return True

            try:
                save_config(self.app_config)
                self._config_dirty = False
                self.logger.info(f"UVC-Configuration saved!")
                return True
            except Exception as exc:
                self.logger.error(f"Error saving UVC configuration: {exc}")
                return False

    def _apply_uvc_controls(self) -> None:
        if not self.video_capture:
            raise RuntimeError("Camera not initialized")

        # Hilfsfunktionen für Auto‑/Manuell‑Flags
        def _set_auto_exposure(auto: bool) -> None:
            if platform.system() == "Windows":
                value = 0.75 if auto else 0.25  # DirectShow‑Konvention
            else:  # Linux V4L2
                value = 3 if auto else 1       # V4L2_EXPOSURE_AUTO / MANUAL
            self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, value)

        def _set_auto_wb(auto: bool) -> None:
            self._safe_set(cv2.CAP_PROP_AUTO_WB, 1 if auto else 0)

        # ----------------- Exposure -----------------
        if hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            _set_auto_exposure(self.uvc_config.exposure.auto)
            if not self.uvc_config.exposure.auto and self.uvc_config.exposure.value is not None:
                self._safe_set(cv2.CAP_PROP_EXPOSURE, self.uvc_config.exposure.value)

        # ----------------- White Balance -----------
        if hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            _set_auto_wb(self.uvc_config.white_balance.auto)
            if not self.uvc_config.white_balance.auto and self.uvc_config.white_balance.value is not None:
                self._safe_set(
                    cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, self.uvc_config.white_balance.value
                )

        # --------- Weitere Standardregler ----------
        param_map = {
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
            "hue": cv2.CAP_PROP_HUE,
            "gain": cv2.CAP_PROP_GAIN,
            "sharpness": cv2.CAP_PROP_SHARPNESS,
            "gamma": cv2.CAP_PROP_GAMMA,
            "backlight_compensation": cv2.CAP_PROP_BACKLIGHT,
        }

        for name, prop in param_map.items():
            value = getattr(self.uvc_config, name, None)
            if value is not None:
                if not self._safe_set(prop, value):
                    self.logger.debug(f"Setting of {name} ({value}) was ignored by the driver")

        self.logger.info("UVC controls applied")

    # ------------------ Öffentliche Setter‑Methoden ------------------- #

    # Allgemeiner Setter wird genutzt, damit GUI‑Slider etc. einfach callen können
    def _set_uvc_parameter(self, name: str, cv_prop: int, value: float) -> bool:
        """Setzt einen UVC-Parameter und aktualisiert die Konfiguration."""
        if not isinstance(value, (int, float)):
            self.logger.error(f"Invalid value type for {name}: {type(value)}")
            return False
        
        if not self._safe_set(cv_prop, value):
            self.logger.warning(f"{name} could not be set - driver ignores value {value}")
            return False
        
        try:
            setattr(self.uvc_config, name, value)  # nur RAM - Persistenz separat
            self._config_dirty = True
            self._invalidate_uvc_cache()

            if hasattr(self, '_config_save_timer'):
                self._config_save_timer.cancel()

            self._config_save_timer = threading.Timer(5.0, self._auto_save_config)
            self._config_save_timer.start()

            return True
        except Exception as e:
            self.logger.error(f"Error setting config for {name}: {e}")
            return False

    # Convenience‑Funktionen (können bei Bedarf erweitert werden)
    def set_brightness(self, value: float) -> bool:
        return self._set_uvc_parameter("brightness", cv2.CAP_PROP_BRIGHTNESS, value)

    def set_contrast(self, value: float) -> bool:
        return self._set_uvc_parameter("contrast", cv2.CAP_PROP_CONTRAST, value)

    def set_saturation(self, value: float) -> bool:
        return self._set_uvc_parameter("saturation", cv2.CAP_PROP_SATURATION, value)

    def set_exposure(self, value: float, auto: Optional[bool] = None) -> bool:
        # Auto-Exposure falls angegeben
        if auto is not None:
            if not self.set_auto_exposure(auto):
                return False
        # Manueller Exposure-Wert (auto=False oder auto=None)
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_EXPOSURE, int_value)
        if success and hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            self.uvc_config.exposure.value = int_value
            self.uvc_config.exposure.auto = False
            self._config_dirty = True
            self._invalidate_uvc_cache()
        return success

    def set_hue(self, value: float) -> bool:
        return self._set_uvc_parameter("hue", cv2.CAP_PROP_HUE, value)

    def set_sharpness(self, value: float) -> bool:
        return self._set_uvc_parameter("sharpness", cv2.CAP_PROP_SHARPNESS, value)

    def set_gamma(self, value: float) -> bool:
        return self._set_uvc_parameter("gamma", cv2.CAP_PROP_GAMMA, value)

    def set_gain(self, value: float) -> bool:
        return self._set_uvc_parameter("gain", cv2.CAP_PROP_GAIN, value)

    def set_backlight_compensation(self, value: float) -> bool:
        return self._set_uvc_parameter("backlight_compensation", cv2.CAP_PROP_BACKLIGHT, value)

    def set_auto_exposure(self, auto: bool) -> bool:
        """Setzt Auto-Exposure ohne Wert zu ändern"""
        if platform.system() == "Windows":
            result = self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto else 0.25)
        else:
            result = self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if auto else 1)
        # Update der verschachtelten Konfiguration
        if result and hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            self.uvc_config.exposure.auto = auto
            self._config_dirty = True
            self._invalidate_uvc_cache()
        return result

    def set_manual_exposure(self, value: float) -> bool:
        """Setzt manuellen Exposure-Wert und deaktiviert Auto-Exposure"""
        self.set_auto_exposure(False)
        return self.set_exposure(value, auto=False)

    def set_auto_white_balance(self, auto: bool) -> bool:
        """Setzt Auto-White-Balance ohne Wert zu ändern"""
        result = self._safe_set(cv2.CAP_PROP_AUTO_WB, 1 if auto else 0)
        # Update der verschachtelten Konfiguration
        if result and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.auto = auto
            self._config_dirty = True
            self._invalidate_uvc_cache()
        return result

    def set_manual_white_balance(self, value: float) -> bool:
        """Setzt manuellen White-Balance-Wert und deaktiviert Auto-White-Balance"""
        self.set_auto_white_balance(False)
        # Manueller WB-Wert
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int_value)
        if success and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.value = int_value
            self._config_dirty = True
            self._invalidate_uvc_cache()
        return success

    def set_white_balance(self, value: float, auto: Optional[bool] = None) -> bool:
        """Setzt den White-Balance-Wert und/oder den Auto-Modus."""
        # Auto-White-Balance falls angegeben
        if auto is not None:
            if not self.set_auto_white_balance(auto):
                return False
        # Manueller WB-Wert (auto=False oder auto=None)
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int_value)
        if success and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.value = int_value
            self.uvc_config.white_balance.auto = False
            self._config_dirty = True
            self._invalidate_uvc_cache()      
        return success
    
    def _auto_save_config(self):
        """Automatisches Speichern von Config nach Timeout"""
        if getattr(self, '_config_dirty', False):
            self.save_uvc_config()
            self.logger.debug('Config auto-saved after parameter changes')

    # ------------------ Laufende Bilderfassung ------------------------ #

    def start_frame_capture(self) -> None:
        if self.is_running:
            return
        if not self.video_capture or not self.video_capture.isOpened():
            raise RuntimeError("camera not available")

        self.is_running = True
        self.frame_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.frame_thread.start()

    def stop_frame_capture(self) -> None:
        """Stop the frame grabbing thread."""
        self.is_running = False
        if (
            self.frame_thread
            and self.frame_thread.is_alive()
            and threading.current_thread() is not self.frame_thread
        ):
            self.frame_thread.join(timeout=2)

    def _capture_loop(self) -> None:
        consecutive_failures = 0
        max_consecutive_failures = 5

        while self.is_running:
            with self.frame_lock:
                video_capture_ref = self.video_capture
            if not video_capture_ref:
                time.sleep(0.04)
                continue

            try:
                ret, frame = video_capture_ref.read() if video_capture_ref else (False, None)
            except cv2.error as e:
                self.logger.error(f"OpenCV error reading frame: {e}")
                ret, frame = False, None
            except Exception as e:
                self.logger.error(f"Unexpected error reading frame: {e}")
                ret, frame = False, None

            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.logger.debug(f'Framegrab failed {consecutive_failures} times, trying to reconnect...')
                    rec = self._handle_cam_disconnect()
                    if rec:
                        consecutive_failures = 0
                        self.logger.info("Camera reconnected successfully")
                        continue
                    else:
                        self.logger.error("Camera reconnection failed")
                        break
                continue

            # erfolgreicher Frame-Grab → Zähler zurücksetzen
            consecutive_failures = 0
            self._reconnect_attempts = 0

            frame_copy = frame.copy()
            with self.frame_lock:
                self.current_frame = frame_copy
                self.frame_count += 1

            # Motion Detection ausführen falls aktiviert
            self._process_motion_detection(frame, frame_copy)

        # Loop beendet → Loggen
        self.logger.info("Frame capture stopped")
    
    def _process_motion_detection(self, original_frame: np.ndarray, frame_copy: np.ndarray) -> None:
        if self.motion_detector and self.motion_enabled:
                try:
                    motion_result = self.motion_detector.detect_motion(original_frame)
                    self.last_motion_result = motion_result
                    if self.motion_callback:
                        self.motion_callback(frame_copy, motion_result)
                except Exception as exc:
                    self.logger.error(f"Motion-Detection-Error: {exc}")

        elif self.motion_callback:
            # Fallback: Alten Callback-Stil unterstützen für Rückwärtskompatibilität
            try:
                # Erstelle Dummy MotionResult für Kompatibilität und speichere
                dummy_result = MotionResult(
                    motion_detected=False,
                    contour_area=0.0,
                    timestamp=time.time(),
                    roi_used=False,
                )
                self.last_motion_result = dummy_result
                self.motion_callback(original_frame, dummy_result)
                self.logger.debug("Motion callback called with dummy result")
            except Exception as exc:
                self.logger.error(f"Dummy-Motion-Callback-Error: {exc}")
    
    def _handle_cam_disconnect(self):
        """Handle camera disconnection gracefully."""
        self.logger.warning("Camera disconnected, trying to reconnect...")

        if self.motion_callback:
            try:
                # Dummy-Frame für Callback
                dummy_frame = self.get_current_frame()
                if dummy_frame is None:
                    res = self.webcam_config.get_default_resolution()
                    dummy_frame = np.zeros((res.height, res.width, 3), dtype=np.uint8)
                
                # Disconnect-MotionResult erstellen
                disconnect_result = MotionResult(
                    motion_detected=False,
                    contour_area=0.0,
                    timestamp=time.time(),
                    roi_used=False,
                )
                
                # Callback aufrufen um GUI/MeasurementController zu benachrichtigen
                self.motion_callback(dummy_frame, disconnect_result)
                self.logger.debug("Motion callback notified about disconnect")
            except Exception as exc:
                self.logger.error(f"Error notifying motion callback about disconnect: {exc}")

        with self.frame_lock:
            if self.video_capture and self.video_capture.isOpened():
                try:
                    self.video_capture.release()
                except cv2.error as e:
                    self.logger.error(f"OpenCV error releasing video capture: {e}")
                except Exception as e:
                    self.logger.error(f"Error releasing video capture: {e}")
                finally:
                    self.video_capture = None

        base_interval = self.reconnect_interval
        while self._reconnect_attempts < self.max_reconnect_attempts:
            self._reconnect_attempts += 1

            wait_time = base_interval * (2 ** (self._reconnect_attempts - 1))
            wait_time = min(wait_time, 60)
            self.logger.warning(f"Camera not reachable, retrying ({self._reconnect_attempts}/{self.max_reconnect_attempts})")

            time.sleep(wait_time)

            try:
                self._initialize_camera()
                if self.video_capture and self.video_capture.isOpened():
                    self.logger.info("Camera reconnected successfully")
                    self._reconnect_attempts = 0
                    return True
            except Exception as exc:
                self.logger.error(f"Reconnect attempt {self._reconnect_attempts} failed: {exc}")

        self.logger.error("Max. reconnect attempts reached")
        self.stop_frame_capture()
        return False


    # ---------------- Motion-Detection Steuerung --------------------- #
    def enable_motion_detection(self, callback: Callable[[np.ndarray, MotionResult], None]) -> None:
        """Aktiviert die Bewegungserkennung und setzt den Callback."""
        if not self.motion_detector:
            try:
                self.motion_detector = MotionDetector(self.app_config.motion_detection)
                self.logger.info("Motion detector initialized")
            except Exception as e:
                self.logger.error(f"Error initializing motion detector: {e}")
                return

        def safe_callback(frame: np.ndarray, motion_result: MotionResult) -> None:
            """Sicherer Callback, der sicherstellt, dass der Frame gültig ist."""
            if not isinstance(frame, np.ndarray):
                self.logger.error(f"Motion callback called with non-ndarray frame: {type(frame)}")
                return
            
            if frame.size == 0:
                self.logger.warning("Motion callback called with empty frame")
                return
            
            if not isinstance(motion_result, MotionResult):
                self.logger.error(f"Motion callback called with non-MotionResult: {type(motion_result)}")
                return
            
            try:
                callback(frame, motion_result)
            except Exception as exc:
                self.logger.error(f"Motion-Callback-Error: {exc}")

        self.motion_callback = safe_callback
        self.motion_enabled = True

    def disable_motion_detection(self) -> None:
        """Deaktiviert die Bewegungserkennung."""
        self.motion_enabled = False
        self.motion_callback = None

    def is_motion_active(self) -> bool:
        """Gibt zurück, ob die Bewegungserkennung aktiv ist."""
        return self.motion_enabled

    def get_last_motion_result(self) -> Optional[MotionResult]:
        """Gibt das letzte Bewegungsergebnis zurück."""
        return self.last_motion_result

    def get_motion_metrics(self) -> dict:
        """Gibt Metriken zur Bewegungserkennung zurück."""
        return {
            "frame_count": self.frame_count,
            "last_timestamp": self.last_motion_result.timestamp if self.last_motion_result else None,
            "last_contour_area": self.last_motion_result.contour_area if self.last_motion_result else None,
            "roi_used": self.last_motion_result.roi_used if self.last_motion_result else None,
            "motion_enabled": self.motion_enabled
        }

    # ----------------- GUI-Integration Methoden ----------------------- #

    def get_camera_status(self) -> dict:
        """Gibt aktuellen Kamera-Status für GUI zurück"""
        current_time = time.time()
        # Cache für 200ms - GUI braucht nicht 60fps Status-Updates
        cache_time = getattr(self, '_status_cache_time', 0)
        if (current_time - cache_time) < 0.2:
            return getattr(self, '_status_cache', {})
        
        with self.frame_lock:
            video_capture_ref = self.video_capture
            is_connected = video_capture_ref is not None and video_capture_ref.isOpened()

            base_status = {
                "connected": is_connected,
                "resolution": None,
                "fps": None,
                "backend": self.backend,
                "frame_count": self.frame_count,
                "is_running": self.is_running,
                "motion_enabled": self.motion_enabled,
                "reconnect_attempts": self._reconnect_attempts,
                "error_status": self._reconnect_attempts >= self.max_reconnect_attempts
            }
            if is_connected and video_capture_ref is not None:
                try:
                    width = video_capture_ref.get(cv2.CAP_PROP_FRAME_WIDTH)
                    height = video_capture_ref.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    fps = video_capture_ref.get(cv2.CAP_PROP_FPS)

                    base_status.update({
                        "resolution": {
                            "width": int(width) if width is not None else 0,
                            "height": int(height) if height is not None else 0
                        },
                        "fps": float(fps) if fps is not None and fps > 0 else 0.0,
                        "uptime_frames": self.frame_count if self.is_running else 0,
                        "error_status": False if self._reconnect_attempts < self.max_reconnect_attempts else True
                    })
                except Exception as e:
                    self.logger.debug(f"Error getting camera status: {e}")
                    base_status.update({
                        "resolution": None,
                        "fps": None,
                        "uptime_frames": 0,
                        "error_status": True
                    })
            else:
                base_status.update({
                    "resolution": None,
                    "fps": None,
                    "uptime_frames": 0
                })
                

            self._status_cache = base_status
            self._status_cache_time = current_time
            return base_status

    def get_uvc_current_values(self) -> dict:
        """Gibt aktuelle UVC-Werte für GUI-Anzeige zurück"""
        with self.frame_lock:
            if not self.video_capture or not self.video_capture.isOpened():
                return {}
            video_capture_ref = self.video_capture
        
        current_time = time.time()
        if hasattr(self, '_uvc_cache_time') and (current_time - self._uvc_cache_time) < 0.1:
            return getattr(self, '_uvc_cache_values', {})
            
        current_values = {}
        
        # Standard UVC-Parameter auslesen
        param_map = {
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
            "hue": cv2.CAP_PROP_HUE,
            "gain": cv2.CAP_PROP_GAIN,
            "sharpness": cv2.CAP_PROP_SHARPNESS,
            "gamma": cv2.CAP_PROP_GAMMA,
            "backlight_compensation": cv2.CAP_PROP_BACKLIGHT,
            "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
            "white_balance": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U
        }
        
        for name, prop in param_map.items():
            try:
                value = video_capture_ref.get(prop)
                if value is not None and isinstance(value, (int,float)):
                    current_values[name] = value
                else:
                    self.logger.warning(f"Property {name} returned invalid value: {value}")
                    current_values[name] = None
            except cv2.error as e:
                self.logger.warning(f"OpenCV error reading {name}: {e}")
                current_values[name] = None
            except Exception as e:
                self.logger.warning(f"Error reading {name}: {e}")
                current_values[name] = None
        
        self._uvc_cache_values = current_values
        self._uvc_cache_time = current_time
                
        return current_values
    
    
    def _invalidate_uvc_cache(self):
        """Invalidate the cached UVC values."""
        if getattr(self, '_batch_update_mode', False):
            return
        
        try:
            delattr(self, '_uvc_cache_time')
        except AttributeError:
            pass
        
        try:
            delattr(self, '_uvc_cache_values')
        except AttributeError:
            pass

    def get_uvc_ranges(self) -> dict:
        """Gibt Min/Max-Werte für GUI-Slider zurück"""

        return {
            "brightness": {"min": -64, "max": 64, "default": 0},
            "contrast": {"min": 0, "max": 64, "default": 16},
            "saturation": {"min": 0, "max": 128, "default": 64},
            "hue": {"min": -40, "max": 40, "default": 0},
            "gain": {"min": 0, "max": 100, "default": 10},
            "sharpness": {"min": 0, "max": 14, "default": 2},
            "gamma": {"min": 72, "max": 500, "default": 164},
            "backlight_compensation": {"min": 0, "max": 160, "default": 42},
            "exposure": {"min": -13, "max": -1, "default": -6},
            "white_balance": {"min": 2800, "max": 6500, "default": 4600},
        }
    

    def reset_uvc_to_defaults(self) -> bool:
        """Setzt alle UVC-Parameter auf Default-Werte zurück"""
        try:
            ranges = self.get_uvc_ranges()
            success = True
            
            # Standard-Parameter zurücksetzen
            for param, values in ranges.items():
                if param in ["exposure", "white_balance"]:
                    continue  # Diese haben spezielle Behandlung
                    
                setter_name = f"set_{param}"
                if hasattr(self, setter_name):
                    result = getattr(self, setter_name)(values["default"])
                    success = success and result
            
            # Auto-Exposure und Auto-White-Balance aktivieren
            self.set_auto_exposure(True)
            self.set_auto_white_balance(True)

            self._invalidate_uvc_cache()  # Cache invalidieren
            
            self.logger.info("UVC parameters reset to defaults")
            return success
            
        except Exception as exc:
            self.logger.error(f"Error resetting UVC parameters: {exc}")
            return False
    
    def _validate_uvc_value(self, param_name: str, value: float) -> bool:
        """Validiert UVC-Werte gegen bekannte Ranges."""
        ranges = self.get_uvc_ranges()
        if param_name in ranges:
            param_range = ranges[param_name]
            return param_range["min"] <= value <= param_range["max"]
        return True
    
    def is_camera_available(self) -> bool:
        """
        Prüft ob Kamera verfügbar und aktiv ist.
        
        Returns:
            True wenn Kamera verfügbar und läuft
        """
        
        try:
            video_capture_ref = self.video_capture
            is_connected = video_capture_ref is not None and video_capture_ref.isOpened()
            # Prüfe die relevanten Status-Flags
            is_running = self.is_running
            error_status = self._reconnect_attempts >= self.max_reconnect_attempts
            
            # Kamera ist verfügbar wenn sie verbunden, läuft und kein Error
            return is_connected and is_running and not error_status
            
        except Exception as exc:
            # Bei Fehlern Kamera als nicht verfügbar betrachten
            return False

    # ----------------- Frame‑Zugriff und Utils ------------------------ #

    def get_current_frame(self, copy_frame: bool = True) -> Optional[np.ndarray]:
        with self.frame_lock:
            if self.current_frame is None:
                self.logger.debug("No current frame available")
                return None
            return self.current_frame.copy() if copy_frame else self.current_frame
    
    def _get_pooled_frame(self, shape) -> np.ndarray:
        """Holt einen Frame aus dem Pool oder erstellt einen neuen."""
        for _ in range(len(self._frame_pool)):
            try: 
                pooled_frame = self._frame_pool.popleft()
                if pooled_frame.shape == shape:
                    return pooled_frame
                else: 
                    self._frame_pool.append(pooled_frame)
            except IndexError:
                self.logger.debug("Frame pool is empty, creating new frame")
                break

        return np.empty(shape, dtype=np.uint8)
    
    @contextmanager
    def get_pooled_frame_context(self, shape):
        frame = self._get_pooled_frame(shape)
        try:
            yield frame
        finally:
            self._return_to_pool(frame)

    def _return_to_pool(self, frame: np.ndarray) -> None:
        """Gibt einen Frame an den Pool zurück."""
        if len(self._frame_pool) < self._max_pool_size:
            try:
                self._frame_pool.append(frame)
            except Exception as e:
                self.logger.error(f"Error returning frame to pool: {e}")
        else:
            self.logger.debug("Frame pool is full, not returning frame")
    
    def return_snapshot_frame(self, frame: np.ndarray) -> None:
        """Gibt Snapshot-Frame an Pool zurück"""
        if frame is not None:
            self._return_to_pool(frame)

    def take_snapshot(self) -> Optional[np.ndarray]:
        """Gibt einen Snapshot des aktuellen Frames zurück."""
        curr_frame = self.get_current_frame()
        if curr_frame is not None:
            pooled_frame = self._get_pooled_frame(curr_frame.shape)
            np.copyto(pooled_frame, curr_frame)
            return pooled_frame

        with self.frame_lock:
            if not self.video_capture or not self.video_capture.isOpened():
                self.logger.warning("No video capture available for snapshot")
                return None
            video_capture_ref = self.video_capture
        
        try:
            ret, frame = video_capture_ref.read()
            if ret and frame is not None:
                pooled_frame = self._get_pooled_frame(frame.shape)
                np.copyto(pooled_frame, frame)
                self.logger.debug("direct Snapshot taken")
                return pooled_frame
            return None
        except Exception as exc:
            self.logger.debug(f"Error taking snapshot: {exc}")
            return self.get_current_frame()


    def take_hq_snapshot(self, jpeg_quality: int = 95) -> Optional[bytes]:
        """Snapshot als JPEG mit hoher Qualität."""
        frame_array = self.take_snapshot()
        if frame_array is None:
            return None
        # HQ JPEG-Kodierung
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        success, encoded = cv2.imencode('.jpg', frame_array, encode_params)
        if not success:
            self.logger.error("JPEG encoding failed")
            return None
        return encoded.tobytes()

    # ------------------- FastAPI / NiceGUI Integration --------------- #

    @staticmethod
    @lru_cache(maxsize=1)  # Cache für JPEG-Parameter
    def _get_jpeg_params(quality: int = 85) -> list:
        return [cv2.IMWRITE_JPEG_QUALITY, quality]
    
    def _get_jpeg_executor(self):
        """Gibt den ThreadPoolExecutor für JPEG-Kodierung zurück."""
        if self._jpeg_executor is None:
            self._jpeg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="jpeg_encoder")
        return self._jpeg_executor

    @staticmethod
    def convert_frame_to_jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
        params = Camera._get_jpeg_params(quality)
        success, enc = cv2.imencode(".jpg", frame, params)
        if not success:
            _, enc = cv2.imencode(".jpg", frame)
        return enc.tobytes()

    async def grab_video_frame(self) -> Response:
        current_frame = self.get_current_frame()
        if current_frame is None:
            return self.placeholder

        try:
            loop = asyncio.get_event_loop()
            jpeg = await loop.run_in_executor(self._get_jpeg_executor(), Camera.convert_frame_to_jpeg, current_frame)
            return Response(content=jpeg, media_type="image/jpeg")
        except Exception as exc:
            self.logger.error(f"Error grabbing video frame: {exc}")
            return self.placeholder

    # ----------------------- Cleanup & Signals ------------------------ #

    @staticmethod
    async def _disconnect_all() -> None:
        for cid in Client.instances:
            await core.sio.disconnect(cid)

    @staticmethod
    def _sigint_handler(signum, frame_param):
        try:
            loop = asyncio.get_event_loop()

            if loop.is_running():
                loop.create_task(Camera._disconnect_all())
            else:
                asyncio.run(Camera._disconnect_all())

        except RuntimeError:
            try:
                ui.timer(0.1, Camera._disconnect_all, once=True)
                ui.timer(1, lambda: signal.default_int_handler(signum, frame_param), once=True)
            except Exception:
                signal.default_int_handler(signum, frame_param)

        except Exception:
            signal.default_int_handler(signum, frame_param)

    async def cleanup(self):
        """Cleanup method to release resources and disconnect clients."""
        self.logger.info("Starting camera cleanup...")

        self.is_running = False
        self.motion_enabled = False

        self.stop_frame_capture()

        # Cancel the auto-save timer if it exists and is active
        if hasattr(self, '_config_save_timer'):
            try:
                self._config_save_timer.cancel()
                self.logger.debug("Auto-save timer cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling auto-save timer: {e}")

        # Wait for thread to finish
        if hasattr(self, 'frame_thread') and self.frame_thread and self.frame_thread.is_alive():
            self.frame_thread.join(timeout=5)
            if self.frame_thread.is_alive():
                self.logger.warning("Frame capture thread did not stop cleanly")

        # Clean up motion detector
        if self.motion_detector:
            try:
                if hasattr(self.motion_detector, 'cleanup'):
                    self.motion_detector.cleanup()
            except Exception as e:
                self.logger.error(f"Error cleaning up motion detector: {e}")
            finally:
                self.motion_detector = None

        with self.frame_lock:   
            if self.video_capture:
                try:
                    if self.video_capture.isOpened():
                        self.video_capture.release()
                        self.logger.info("Video capture released")
                except Exception as e:
                    self.logger.error(f"Error releasing video capture: {e}")
                finally:
                    self.video_capture = None
            self.current_frame = None
        
        if self._jpeg_executor:
            try:
                self._jpeg_executor.shutdown(wait=True)
                self.logger.info("JPEG executor shut down")
            except Exception as e:
                self.logger.error(f"Error shutting down JPEG executor: {e}")
            finally:
                self._jpeg_executor = None

        try:
            await asyncio.wait_for(Camera._disconnect_all(), timeout=5)
        except asyncio.TimeoutError:
            self.logger.warning("Disconnecting clients timed out")
        except Exception as e:
            self.logger.error(f"Error disconnecting clients: {e}")

        self.logger.info("Camera cleanup completed")

    # ----------------------- GUI / Routing ---------------------------- #

    def initialize_routes(self):
        """Initialize FastAPI routes for video streaming. Call this before starting the web interface."""
        self._setup_routes()

    def _setup_routes(self):
        
        @app.get("/video/frame")
        async def _video_route() -> Response:  # noqa: D401
            return await self.grab_video_frame()

        app.on_shutdown(self.cleanup)
        signal.signal(signal.SIGINT, Camera._sigint_handler)

    def setup(self):  # noqa: D401
        self._setup_routes()
        img = ui.interactive_image().classes("w-full h-full")
        ui.timer(0.1, lambda: img.set_source(f"/video/frame?{time.time()}"))

    # ---------------- GUI Convenience Methods ----------------------- #
    def get_all_uvc_ranges(self) -> dict:
        """Alias for UVC slider ranges."""
        return self.get_uvc_ranges()

    def reset_to_defaults(self) -> bool:
        """Alias to reset all UVC parameters to defaults."""
        return self.reset_uvc_to_defaults()
