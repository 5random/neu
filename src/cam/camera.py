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
from typing import Callable, Optional, Iterator, Dict, Any

import cv2
import numpy as np
from fastapi import Response
from nicegui import Client, app, core, run, ui
import logging

from src.config import AppConfig, WebcamConfig, UVCConfig, load_config, save_config, get_logger
from .motion import MotionResult, MotionDetector


class Camera:
    """
    Kameraklasse mit vollständiger (und funktionierender) UVC‑Steuerung.
    
    WARNUNG: Die Initialisierung erfolgt asynchron!
    Nach der Instanziierung MUSS `await camera.wait_for_init()` (oder die synchrone Variante)
    aufgerufen werden, bevor auf `video_capture` oder andere Eigenschaften zugegriffen wird.
    """
    
    # Konstanten für Kamera-Initialisierung (Issue #13)
    WARMUP_FRAMES = 30
    FRAME_WAIT_SECONDS = 0.03
    RECONNECT_INTERVAL_SECONDS = 5
    MAX_RECONNECT_ATTEMPTS = 5

    # ------------------------- Initialisierung ------------------------- #

    def __init__(self, config: AppConfig, logger: Optional[logging.Logger] = None) -> None:
        # -- Config & Logger --
        self.app_config: AppConfig = config
        self.webcam_config: WebcamConfig = self.app_config.webcam
        self.uvc_config: UVCConfig = self.app_config.uvc_controls
        self.logger = logger or get_logger('camera')
        self.logger.info("Initializing Camera")

        # Measurement config for alerts
        self.measurement_config = self.app_config.measurement

        # Ensure image save path exists if alert images should be saved
        if getattr(self.measurement_config, 'save_alert_images', False):
            self.measurement_config.ensure_save_path()

        # -- Interne State‑Variablen --
        self.video_capture: Optional[cv2.VideoCapture] = None
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.capture_lock = threading.RLock()  # Protects video_capture access
        self._init_complete = threading.Event()  # Signals when async init is done
        self.is_running = False
        self.frame_thread: Optional[threading.Thread] = None  # Initialize before use
        self.motion_callback: Optional[Callable[[np.ndarray, MotionResult], None]] = None

        # -- Motion Detection --
        self.motion_detector: Optional[MotionDetector] = None
        self.motion_enabled = False
        self.frame_count = 0

        # Letztes Bewegungsergebnis für Metrics
        self.last_motion_result: Optional[MotionResult] = None

        # Reconnection settings
        self.reconnect_interval = self.RECONNECT_INTERVAL_SECONDS
        self.max_reconnect_attempts = self.MAX_RECONNECT_ATTEMPTS
        self._reconnect_attempts = 0
        
        # Performance optimization
        self.motion_skip_frames = 2  # Run motion detection only every 2nd frame
        
        # -- Platzhalterbild für fehlende Kamera --
        black_1px = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAA"
            "AANJREFUGFdjYGBg+A8AAQQBAHAgZQsAAAAASUVORK5CYII="
        )
        self._uvc_cache_time: float = 0.0

        self._max_pool_size = 3
        self._frame_pool: collections.deque = collections.deque(maxlen=self._max_pool_size)

        self.cleaned = False
        
        # Placeholder object with body attribute
        from types import SimpleNamespace
        self.placeholder = SimpleNamespace(body=base64.b64decode(black_1px))

        self._jpeg_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._config_save_timer: Optional[threading.Timer] = None

        # -- Backend je nach Plattform explizit wählen --
        system = platform.system()
        if system == "Windows":
            self.logger.info("Using DirectShow backend for Windows")
            self.backend = cv2.CAP_DSHOW  # DirectShow - garantiert alle Regler
        elif system == "Linux":
            self.logger.info("Using Video4Linux2 backend for Linux")
            self.backend = cv2.CAP_V4L2   # Video4Linux2
        else:
            # macOS oder unbekannt → OpenCV entscheidet selbst
            self.backend = 0
            self.logger.warning("Unknown OS - use standard backend (can restrict controllers)")

        # -- Kamera initialisieren (Asynchron) --
        self._start_async_init()

    def _start_async_init(self) -> None:
        """Startet die Kamera-Initialisierung im Hintergrund."""
        self.logger.info("Starting async camera initialization...")
        self._init_thread = threading.Thread(target=self._init_worker, daemon=True)
        self._init_thread.start()

    def _init_worker(self) -> None:
        """Worker-Thread für die Initialisierung."""
        try:
            self._initialize_camera()
            self.logger.info("Async camera initialization completed successfully")
            self._init_complete.set()  # Signal erfolgreiche Initialisierung
            # Automatisch Frame-Capture starten, wenn Init erfolgreich
            self.start_frame_capture()
        except Exception as exc:
            self.logger.error(f"Async camera initialization failed: {exc}")
            self.video_capture = None
            self._init_complete.set()  # Signal auch bei Fehler setzen

    def _initialize_camera(self) -> None:
        video_capture: Optional[cv2.VideoCapture] = None
        try:
            self.logger.info(
                f"Open camera index {self.webcam_config.camera_index} with backend {self.backend}"
            )

            with self.capture_lock:
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
                        video_capture = None
                        # Windows: Fallback auf MSMF versuchen
                        if platform.system() == "Windows" and self.backend == cv2.CAP_DSHOW:
                            self.logger.warning("DirectShow failed; trying MSMF backend")
                            vc2 = cv2.VideoCapture(self.webcam_config.camera_index, cv2.CAP_MSMF)
                            if vc2 and vc2.isOpened():
                                video_capture = vc2
                            else:
                                if vc2:
                                    vc2.release()
                                raise RuntimeError(f"Camera {self.webcam_config.camera_index} could not be opened")
                    
                    if video_capture:
                        # Properties setzen
                        self._set_camera_properties(video_capture)
                        # Warmup
                        ok = False
                        for i in range(self.WARMUP_FRAMES):
                            ret, _ = video_capture.read()
                            if ret:
                                ok = True
                                break
                            time.sleep(self.FRAME_WAIT_SECONDS)
                        
                        if not ok:
                            # letzter Fallback: Default-Backend probieren
                            if platform.system() == "Windows" and self.backend != 0:
                                self.logger.warning("No frame after warmup; trying default backend")
                                try:
                                    tmp = cv2.VideoCapture(self.webcam_config.camera_index, 0)
                                    if tmp and tmp.isOpened():
                                        video_capture.release()
                                        video_capture = tmp
                                        self._set_camera_properties(video_capture)
                                        # noch einmal warmup
                                        for _ in range(30):
                                            ret, _ = video_capture.read()
                                            if ret:
                                                ok = True
                                                break
                                            time.sleep(0.03)
                                except Exception:
                                    self.logger.exception("Default backend attempt failed")
                            if not ok:
                                if video_capture:
                                    video_capture.release()
                                raise RuntimeError("No frame received from camera during initialization (all backends)")

                self.video_capture = video_capture

            if self.video_capture:
                self._apply_uvc_controls()
                self.logger.info("Camera successfully initialized")

        except Exception as exc:
            self.logger.error(f"Initialization failed: {exc}")
            if video_capture is not None:
                try:
                    video_capture.release()
                except Exception as e:
                    self.logger.debug(f"Error during video_capture cleanup: {e}")

            with self.capture_lock:
                if self.video_capture is not None:
                    try:
                        self.video_capture.release()
                    except Exception as e:
                        self.logger.debug(f"Error during self.video_capture cleanup: {e}")
                    finally:
                        self.video_capture = None
            raise


    def wait_for_init(self, timeout: float = 10.0) -> bool:
        """
        Wartet bis die Kamera-Initialisierung abgeschlossen ist.
        
        Args:
            timeout: Maximale Wartezeit in Sekunden
            
        Returns:
            True wenn Initialisierung abgeschlossen (erfolgreich oder nicht), False bei Timeout
        """
        result = self._init_complete.wait(timeout=timeout)
        if not result:
            self.logger.error(f"Camera initialization timeout after {timeout}s")
        return result

    def _set_camera_properties(self, capture: cv2.VideoCapture) -> None:
        """Grundlegende Auflösung / FPS etc. setzen."""
        # Note: This is called with a local capture object during init, or needs lock if called on self.video_capture
        # Here we assume it's called on the local object during init, so no lock needed yet.
        # BUT _safe_set_for_capture is used which is generic.
        
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
        with self.capture_lock:
            if not self.video_capture or not self.video_capture.isOpened():
                self.logger.error("safe_set: Camera not available")
                return False
            try:
                ok = self.video_capture.set(prop, value)
                actual = self.video_capture.get(prop)
                if not ok or abs(float(actual) - float(value)) > 1e-3:
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
            if not ok or abs(float(actual) - float(value)) > 1e-3:
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
        # Called inside init (with lock or local object) or needs lock
        # Since this calls _safe_set which uses lock, we need to be careful about reentrancy.
        # RLock handles reentrancy.
        
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
                self._safe_set(cv2.CAP_PROP_EXPOSURE, int(self.uvc_config.exposure.value))

        # ----------------- White Balance -----------
        if hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            _set_auto_wb(self.uvc_config.white_balance.auto)
            if not self.uvc_config.white_balance.auto and self.uvc_config.white_balance.value is not None:
                self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int(self.uvc_config.white_balance.value))

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
                if not self._safe_set(prop, float(value)):
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

            if self._config_save_timer:
                try:
                    self._config_save_timer.cancel()
                except Exception:
                    pass

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
    
    def _auto_save_config(self) -> None:
        """Automatisches Speichern von Config nach Timeout"""
        if getattr(self, '_config_dirty', False):
            self.save_uvc_config()
            self.logger.debug('Config auto-saved after parameter changes')

    # ------------------ Laufende Bilderfassung ------------------------ #

    def start_frame_capture(self) -> None:
        if self.is_running:
            return
        
        # Check if video_capture is ready (or wait/retry logic could be here)
        # For async init, we might be called before init is done.
        # But _init_worker calls us after success.
        # If called manually from outside, we should check.
        
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
        """Vereinfachte Capture-Loop ohne Retry-Logik"""
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        while self.is_running:
            with self.capture_lock:
                video_capture_ref = self.video_capture
                if not video_capture_ref or not video_capture_ref.isOpened():
                    # Camera not ready yet or disconnected
                    time.sleep(0.1)
                    continue

                # Frame lesen
                try:
                    ret, frame = video_capture_ref.read()
                except cv2.error as e:
                    self.logger.error(f"OpenCV error reading frame: {e}")
                    ret, frame = False, None
                except Exception as e:
                    self.logger.error(f"Frame read error: {e}")
                    ret, frame = False, None
                
            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    self.logger.debug(f'Framegrab failed {consecutive_failures} times in a row')
                    rec = self._handle_cam_disconnect()
                    if rec:
                        self.logger.info("Reconnection successful, resuming frame capture")
                        consecutive_failures = 0
                        continue
                    else:
                        self.logger.error("Reconnection failed")
                        break
                continue
                
            # Erfolgreicher Frame
            consecutive_failures = 0
            self._reconnect_attempts = 0

            frame_copy = frame.copy()
            
            with self.frame_lock:
                self.current_frame = frame_copy
                self.frame_count += 1
                
            # Motion Detection
            self._process_motion_detection(frame, frame_copy)
            
        self.logger.info("Frame capture loop stopped")
    
    def _process_motion_detection(self, original_frame: np.ndarray, frame_copy: np.ndarray) -> None:
        # Frame skipping optimization
        if self.frame_count % self.motion_skip_frames != 0:
            return

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
    
    def _handle_cam_disconnect(self) -> bool:
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

        with self.capture_lock:
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
                # Check if init was successful
                with self.capture_lock:
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

    def is_camera_available(self) -> bool:
        """Returns True if the camera is connected and operational."""
        with self.capture_lock:
            return (
                self.video_capture is not None 
                and self.video_capture.isOpened() 
                and self.is_running
            )

    # ----------------- GUI-Integration Methoden ----------------------- #

    def get_camera_status(self) -> dict:
        """Gibt aktuellen Kamera-Status für GUI zurück"""
        current_time = time.time()
        # Cache für 200ms - GUI braucht nicht 60fps Status-Updates
        cache_time = getattr(self, '_status_cache_time', 0)
        if (current_time - cache_time) < 0.2:
            return getattr(self, '_status_cache', {})
        
        with self.capture_lock:
            video_capture_ref = self.video_capture
            is_connected = video_capture_ref is not None and video_capture_ref.isOpened()

            base_status: Dict[str, Any] = {
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
                        "resolution": {"width": int(width or 0), "height": int(height or 0)},
                        "fps": float(fps) if fps and fps > 0 else 0.0,
                        "uptime_frames": self.frame_count if self.is_running else 0,
                    })
                except Exception as e:
                    self.logger.debug(f"Error getting camera status: {e}")
                    base_status.update({"resolution": None, "fps": None, "uptime_frames": 0, "error_status": True})
            else:
                base_status.update({"resolution": None, "fps": None, "uptime_frames": 0})

            self._status_cache = base_status
            self._status_cache_time = current_time
            return base_status

    def get_uvc_current_values(self) -> dict:
        """Gibt aktuelle UVC-Werte für GUI-Anzeige zurück"""
        with self.capture_lock:
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
                    if value is not None and isinstance(value, (int, float)):
                        current_values[name] = value
                except cv2.error as e:
                    self.logger.debug(f"OpenCV get failed for {name}: {e}")
                except Exception as e:
                    self.logger.debug(f"Error reading {name}: {e}")
            
            self._uvc_cache_values = current_values
            self._uvc_cache_time = current_time
            return current_values
    
    
    def _invalidate_uvc_cache(self) -> None:
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
            
            # Standard-Parameter zurücksetzen (außer spezielle)
            for param, values in ranges.items():
                if param in ("exposure", "white_balance"):
                    continue
                setter_name = f"set_{param}"
                if hasattr(self, setter_name):
                    result = getattr(self, setter_name)(values["default"])
                    success = success and result

            # Auto-Exposure und Auto-White-Balance aktivieren
            self.set_auto_exposure(True)
            self.set_auto_white_balance(True)

            self._invalidate_uvc_cache()  # Cache invalidieren
            return success
        except Exception as e:
            self.logger.error(f"Error resetting UVC defaults: {e}")
            return False

    # ----------------- Snapshot & Cleanup ----------------------- #

    def take_snapshot(self) -> Optional[np.ndarray]:
        """Erstellt einen Snapshot (Thread-sicher)."""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
        return None
    
    def get_current_frame(self, copy_frame: bool = True) -> Optional[np.ndarray]:
        """
        Gibt den aktuellen Frame zurück (Thread-sicher).
        
        Args:
            copy_frame: Wenn True (Default), wird eine Kopie zurückgegeben (sicher).
                       Wenn False, wird eine Referenz zurückgegeben (schneller, aber Mutation vermeiden!).
        """
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy() if copy_frame else self.current_frame
            return None

    def initialize_routes(self) -> None:
        """Initialisiert die API-Routen für den Videostream."""
        # Warne wenn Routes vor Kamera-Initialisierung registriert werden
        if not self._init_complete.is_set():
            self.logger.warning("initialize_routes called before camera initialization complete")
        
        @app.get('/video_feed')
        def video_feed() -> Response:
            return Response(content=self._gen_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

        @app.get('/video/frame')
        def video_frame() -> Response:
            """Gibt einen einzelnen Frame zurück (für statische Updates oder MJPEG-Fallback)."""
            frame = self.get_current_frame(copy_frame=False)
            if frame is None:
                # Placeholder zurückgeben
                return Response(content=self.placeholder.body, media_type="image/png")
            
            try:
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if not ret:
                    return Response(content=self.placeholder.body, media_type="image/png")
                return Response(content=buffer.tobytes(), media_type="image/jpeg")
            except Exception as e:
                self.logger.error(f"Error encoding frame: {e}")
                return Response(content=self.placeholder.body, media_type="image/png")
            
    def _gen_frames(self) -> Iterator[bytes]:
        """Generator für den Videostream."""
        while True:
            # Für Streaming keine Kopie nötig, da Encoding nicht mutiert
            frame = self.get_current_frame(copy_frame=False)
            if frame is None:
                # Platzhalter senden wenn kein Frame verfügbar
                yield (b'--frame\r\n'
                       b'Content-Type: image/png\r\n\r\n' + self.placeholder.body + b'\r\n')
                time.sleep(0.1)
                continue
                
            try:
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if not ret:
                    continue
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            except Exception as e:
                self.logger.error(f"Error encoding frame: {e}")
                time.sleep(0.1)
            
            time.sleep(0.03) # Limit FPS slightly for stream

    def cleanup(self) -> None:  # Entfernt 'async' - keine await-Statements
        """Cleanup method."""
        if self.cleaned:
            return
        self.cleaned = True
        self.logger.info("Starting Camera cleanup...")
        self.is_running = False
        
        # Cancel config save timer if running
        if self._config_save_timer is not None:
            try:
                self._config_save_timer.cancel()
            except Exception:
                pass
            self._config_save_timer = None
        
        # Stop init thread if running
        if self._init_thread and self._init_thread.is_alive():
            self.logger.info("Waiting for init thread to complete...")
            self._init_thread.join(timeout=2.0)
            if self._init_thread.is_alive():
                self.logger.warning("Init thread did not complete in time")
        
        # Stop frame capture thread
        if self.frame_thread and self.frame_thread.is_alive():
            self.logger.info("Stopping frame capture...")
            self.stop_frame_capture()
        
        # Cleanup motion detector
        if self.motion_detector is not None:
            try:
                self.motion_detector.cleanup()
            except Exception as e:
                self.logger.debug(f"Error cleaning up motion detector: {e}")
            self.motion_detector = None
        
        # Clear frame pool (Issue #5)
        if hasattr(self, '_frame_pool'):
            self._frame_pool.clear()
        
        # Release camera
        with self.capture_lock:
            if self.video_capture:
                try:
                    self.video_capture.release()
                except Exception as e:
                    self.logger.error(f"Error releasing video capture: {e}")
                finally:
                    self.video_capture = None
        
        self.logger.info("Camera cleanup completed")
