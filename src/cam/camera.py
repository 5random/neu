from __future__ import annotations

import base64
import platform
import signal
import threading
import time
from dataclasses import asdict
from turtle import width
from typing import Callable, Optional
import os

import cv2
import numpy as np
from fastapi import Response
from nicegui import Client, app, core, run, ui

from src.config import AppConfig, WebcamConfig, UVCConfig, load_config, save_config
from .motion import MotionResult, MotionDetector


class Camera:
    """Kameraklasse mit vollständiger (und funktionierender) UVC‑Steuerung."""

    # ------------------------- Initialisierung ------------------------- #

    def __init__(self, config_path: str = "config/config.yaml") -> None:
        # -- Config & Logger --
        self.config_path = config_path
        self.app_config: AppConfig = load_config(config_path)
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

        # -- Backend je nach Plattform explizit wählen --
        system = platform.system()
        if system == "Windows":
            self.backend = cv2.CAP_DSHOW  # DirectShow – garantiert alle Regler
        elif system == "Linux":
            self.backend = cv2.CAP_V4L2   # Video4Linux2
        else:
            # macOS oder unbekannt → OpenCV entscheidet selbst
            self.backend = 0
            self.logger.warning("Unknown OS - use standard backend (can restrict controllers)")

        # -- Kamera initialisieren --
        self._initialize_camera()
        ret, frame = self.video_capture.read() if self.video_capture else (False, None)
        if not ret or frame is None:
            self.logger.debug("Frame grab failed")
            time.sleep(0.5)
            self._initialize_camera()

    def _initialize_camera(self) -> None:
        try:
            self.logger.info(
                f"Open camera index {self.webcam_config.camera_index} with backend {self.backend}"
            )
            self.video_capture = cv2.VideoCapture(
                self.webcam_config.camera_index, self.backend
            )

            if not self.video_capture.isOpened():
                raise RuntimeError("Camera could not be opened")

            self._set_camera_properties()
            self._apply_uvc_controls()

            # Test‑Frame zum Validieren
            ret, _ = self.video_capture.read()
            if not ret:
                raise RuntimeError("No frame received from camera")

            self.logger.info("Camera successfully initialized")

        except Exception as exc:
            self.logger.error(f"Initialization failed: {exc}")
            if self.video_capture is not None:
                self.video_capture.release()
            raise

    def _set_camera_properties(self) -> None:
        """Grundlegende Auflösung / FPS etc. setzen."""
        if not self.video_capture:
            raise RuntimeError("Camera not initialized")

        res = self.webcam_config.get_default_resolution()
        self._safe_set(cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._safe_set(cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._safe_set(cv2.CAP_PROP_FPS, self.webcam_config.fps)
        self._safe_set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.logger.info(
            "current camera status: %dx%d @ %.1f FPS",
            int(self.video_capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self.video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            self.video_capture.get(cv2.CAP_PROP_FPS),
        )

    # --------------------- Low‑Level‑Hilfsfunktionen ------------------- #

    def _safe_set(self, prop: int, value: float) -> bool:
        """Setzt ein VideoCapture‑Property und prüft, ob es übernommen wurde."""
        if not self.video_capture or not self.video_capture.isOpened():
            self.logger.error("safe_set: Kamera nicht verfügbar")
            return False

        ok = self.video_capture.set(prop, value)
        actual = self.video_capture.get(prop)
        if not ok or abs(actual - value) > 1e-3:
            self.logger.debug(f"Property {prop} Wunsch={value}, erhalten={actual}")
            return False
        return True
    
    def save_uvc_config(self, path: Optional[str] = None) -> bool:
            """Speichert die aktuellen UVC-Einstellungen zurück in die Config-Datei."""
            cfg_path = path or self.config_path
            try:
                save_config(self.app_config, cfg_path)
                self.logger.info(f"UVC-Configuration saved: {cfg_path}")
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
        if not self._safe_set(cv_prop, value):
            self.logger.warning(f"{name} could not be set - driver ignores value {value}")
            return False
        setattr(self.uvc_config, name, value)  # nur RAM – Persistenz separat
        return True

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
        if hasattr(self.uvc_config, "exposure") and self.uvc_config.exposure:
            self.uvc_config.exposure.auto = auto
        return result

    def set_manual_exposure(self, value: float) -> bool:
        """Setzt manuellen Exposure-Wert und deaktiviert Auto-Exposure"""
        self.set_auto_exposure(False)
        return self.set_exposure(value, auto=False)

    def set_auto_white_balance(self, auto: bool) -> bool:
        """Setzt Auto-White-Balance ohne Wert zu ändern"""
        result = self._safe_set(cv2.CAP_PROP_AUTO_WB, 1 if auto else 0)
        # Update der verschachtelten Konfiguration
        if hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.auto = auto
        return result

    def set_manual_white_balance(self, value: float) -> bool:
        """Setzt manuellen White-Balance-Wert und deaktiviert Auto-White-Balance"""
        self.set_auto_white_balance(False)
        # Manueller WB-Wert
        int_value = int(value)
        success = self._safe_set(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, int_value)
        if success and hasattr(self.uvc_config, "white_balance") and self.uvc_config.white_balance:
            self.uvc_config.white_balance.value = int_value
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
        return success

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
         while self.is_running:
             ret, frame = self.video_capture.read() if self.video_capture else (False, None)

             if not ret or frame is None:
                self.logger.debug("Frame grab failed")
                if self.video_capture and self.video_capture.isOpened(): self.video_capture.release()
                 # Reconnect-Logik
                self._reconnect_attempts += 1
                if self._reconnect_attempts <= self.max_reconnect_attempts:
                    self.logger.warning(
                        f"Camera not reachable, retrying ({self._reconnect_attempts}/{self.max_reconnect_attempts})"
                    )
                    time.sleep(self.reconnect_interval)
                    try:
                        self._initialize_camera()
                    except Exception as exc:
                        self.logger.error(f"Reconnect failed: {exc}")
                    continue
                else:
                    self.logger.error("Max. reconnect attempts reached")
                    self.stop_frame_capture()
                    break

             # erfolgreicher Frame-Grab → Zähler zurücksetzen
             self._reconnect_attempts = 0
                 
             self.frame_count += 1
             with self.frame_lock:
                 self.current_frame = frame.copy()
                 
                 
             # Motion Detection ausführen falls aktiviert
             if self.motion_detector and self.motion_enabled:
                 try:
                     motion_result = self.motion_detector.detect_motion(frame)
                     self.last_motion_result = motion_result
                     if self.motion_callback:
                         self.motion_callback(frame, motion_result)
                     
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
                     self.motion_callback(frame, dummy_result)
                 except Exception as exc:
                     self.logger.error(f"Motion-Callback-Error: {exc}")


    # ---------------- Motion-Detection Steuerung --------------------- #
    def enable_motion_detection(self, callback: Callable[[np.ndarray, MotionResult], None]) -> None:
        """Aktiviert die Bewegungserkennung und setzt den Callback."""
        if not self.motion_detector:
            self.motion_detector = MotionDetector(self.app_config.motion_detection)
        self.motion_callback = callback
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
        if not self.video_capture or not self.video_capture.isOpened():
            return {
                "connected": False,
                "resolution": None,
                "fps": None,
                "backend": self.backend
            }
        
        return {
            "connected": True,
            "resolution": {
                "width": int(self.video_capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(self.video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            },
            "fps": self.video_capture.get(cv2.CAP_PROP_FPS),
            "frame_count": self.frame_count,
            "backend": self.backend,
            "is_running": self.is_running,
            "motion_enabled": getattr(self, 'motion_enabled', False)
        }

    def get_uvc_current_values(self) -> dict:
        """Gibt aktuelle UVC-Werte für GUI-Anzeige zurück"""
        if not self.video_capture or not self.video_capture.isOpened():
            return {}
            
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
        }
        
        for name, prop in param_map.items():
            try:
                current_values[name] = self.video_capture.get(prop)
            except:
                current_values[name] = None
                
        # Spezielle Auto/Manual-Parameter
        current_values["auto_exposure"] = self.video_capture.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        current_values["exposure"] = self.video_capture.get(cv2.CAP_PROP_EXPOSURE)
        current_values["auto_white_balance"] = self.video_capture.get(cv2.CAP_PROP_AUTO_WB)
        current_values["white_balance"] = self.video_capture.get(cv2.CAP_PROP_WHITE_BALANCE_BLUE_U)
        
        return current_values

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
            
            self.logger.info("UVC parameters reset to defaults")
            return success
            
        except Exception as exc:
            self.logger.error(f"Error resetting UVC parameters: {exc}")
            return False

    # ----------------- Frame‑Zugriff und Utils ------------------------ #

    def get_current_frame(self) -> Optional[np.ndarray]:
        with self.frame_lock:
            return None if self.current_frame is None else self.current_frame.copy()

    def take_snapshot(self) -> Optional[np.ndarray]:
        """Gibt einen Snapshot des aktuellen Frames zurück."""
        if not self.video_capture or not self.video_capture.isOpened():
            return None
        ret, frame = self.video_capture.read()
        return frame.copy() if ret else self.get_current_frame()

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
    def convert_frame_to_jpeg(frame: np.ndarray) -> bytes:
        _, enc = cv2.imencode(".jpg", frame)
        return enc.tobytes()

    async def grab_video_frame(self) -> Response:
        if not self.video_capture or not self.video_capture.isOpened():
            return self.placeholder
        _, frame = await run.io_bound(self.video_capture.read)
        if frame is None:
            return self.placeholder
        jpeg = await run.cpu_bound(Camera.convert_frame_to_jpeg, frame)
        return Response(content=jpeg, media_type="image/jpeg")

    # ----------------------- Cleanup & Signals ------------------------ #

    @staticmethod
    async def _disconnect_all() -> None:
        for cid in Client.instances:
            await core.sio.disconnect(cid)

    @staticmethod
    def _sigint_handler(signum, frame):  # noqa: D401  (NiceGUI‑Konvention)
        ui.timer(0.1, Camera._disconnect_all, once=True)
        ui.timer(1, lambda: signal.default_int_handler(signum, frame), once=True)

    async def cleanup(self):  # noqa: D401
        await Camera._disconnect_all()
        if self.video_capture:
            self.video_capture.release()

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
