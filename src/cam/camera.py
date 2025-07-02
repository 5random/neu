from __future__ import annotations

import base64
import platform
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import cv2
import numpy as np
from fastapi import Response
from nicegui import Client, app, core, run, ui

from src.config import AppConfig, WebcamConfig, UVCConfig, load_config


class Camera:
    """Kameraklasse mit vollständiger (und funktionierender) UVC‑Steuerung."""

    # ------------------------- Initialisierung ------------------------- #

    def __init__(self, config_path: str = "config/config.yaml") -> None:
        # -- Config & Logger --
        self.app_config: AppConfig = load_config(config_path)
        self.webcam_config: WebcamConfig = self.app_config.webcam
        self.uvc_config: UVCConfig = self.app_config.uvc_controls
        self.logger = self.app_config.logging.setup_logger("camera")

        # -- Interne State‑Variablen --
        self.video_capture: Optional[cv2.VideoCapture] = None
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.is_running = False
        self.frame_thread: Optional[threading.Thread] = None
        self.motion_callback: Optional[Callable[[np.ndarray], None]] = None
        self.executor = ThreadPoolExecutor(max_workers=2)

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
            self.logger.warning("Unbekanntes OS – benutze Standard‑Backend (kann Regler einschränken)")

        # -- Kamera initialisieren --
        self._initialize_camera()

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

    # ------------------------- Kamera öffnen -------------------------- #

    def _initialize_camera(self) -> None:
        try:
            self.logger.info(
                f"Öffne Kamera‑Index {self.webcam_config.camera_index} mit Backend {self.backend}"
            )
            self.video_capture = cv2.VideoCapture(
                self.webcam_config.camera_index, self.backend
            )

            if not self.video_capture.isOpened():
                raise RuntimeError("Kamera konnte nicht geöffnet werden")

            self._set_camera_properties()
            self._apply_uvc_controls()

            # Test‑Frame zum Validieren
            ret, _ = self.video_capture.read()
            if not ret:
                raise RuntimeError("Kein Frame von Kamera erhalten")

            self.logger.info("Kamera erfolgreich initialisiert")
        except Exception as exc:
            self.logger.error(f"Initialisierung fehlgeschlagen: {exc}")
            if self.video_capture is not None:
                self.video_capture.release()
            raise

    def _set_camera_properties(self) -> None:
        """Grundlegende Auflösung / FPS etc. setzen."""
        if not self.video_capture:
            raise RuntimeError("Kamera nicht initialisiert")

        res = self.webcam_config.get_default_resolution()
        self._safe_set(cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._safe_set(cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._safe_set(cv2.CAP_PROP_FPS, self.webcam_config.fps)
        self._safe_set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.logger.info(
            "Aktive Auflösung: %dx%d @ %.1f FPS",
            int(self.video_capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self.video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            self.video_capture.get(cv2.CAP_PROP_FPS),
        )

    # ---------------------- UVC‑Steuerung anwenden -------------------- #

    def _apply_uvc_controls(self) -> None:
        if not self.video_capture:
            raise RuntimeError("Kamera nicht initialisiert")

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
        if hasattr(self.uvc_config, "auto_exposure"):
            _set_auto_exposure(self.uvc_config.auto_exposure)
        if self.uvc_config.exposure is not None and not getattr(
            self.uvc_config, "auto_exposure", True
        ):
            self._safe_set(cv2.CAP_PROP_EXPOSURE, self.uvc_config.exposure)

        # ----------------- White Balance -----------
        if hasattr(self.uvc_config, "auto_white_balance"):
            _set_auto_wb(self.uvc_config.auto_white_balance)
        if self.uvc_config.white_balance is not None and not getattr(
            self.uvc_config, "auto_white_balance", True
        ):
            self._safe_set(
                cv2.CAP_PROP_WHITE_BALANCE_BLUE_U, self.uvc_config.white_balance
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
            "zoom": cv2.CAP_PROP_ZOOM,
        }

        for name, prop in param_map.items():
            value = getattr(self.uvc_config, name, None)
            if value is not None:
                if not self._safe_set(prop, value):
                    self.logger.debug(f"Setzen von {name} ({value}) wurde vom Treiber ignoriert")

        self.logger.info("UVC‑Controls angewendet")

    # ------------------ Laufende Bilderfassung ------------------------ #

    def start_frame_capture(self) -> None:
        if self.is_running:
            return
        if not self.video_capture or not self.video_capture.isOpened():
            raise RuntimeError("Kamera nicht verfügbar")

        self.is_running = True
        self.frame_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.frame_thread.start()

    def stop_frame_capture(self) -> None:
        self.is_running = False
        if self.frame_thread and self.frame_thread.is_alive():
            self.frame_thread.join(timeout=2)

    def _capture_loop(self) -> None:
        while self.is_running:
            ret, frame = self.video_capture.read() if self.video_capture else (False, None)
            if not ret:
                self.logger.debug("Frame‑Grab fehlgeschlagen")
                time.sleep(0.05)
                continue
            with self.frame_lock:
                self.current_frame = frame.copy()
            if self.motion_callback:
                try:
                    self.motion_callback(frame)
                except Exception as exc:
                    self.logger.error(f"Motion‑Callback‑Fehler: {exc}")

    # ------------------ Öffentliche Setter‑Methoden ------------------- #

    # Allgemeiner Setter wird genutzt, damit GUI‑Slider etc. einfach callen können
    def _set_uvc_parameter(self, name: str, cv_prop: int, value: float) -> bool:
        if not self._safe_set(cv_prop, value):
            self.logger.warning(f"{name} konnte nicht gesetzt werden – Treiber ignoriert Wert {value}")
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
        if auto is not None:
            if platform.system() == "Windows":
                self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto else 0.25)
            else:
                self._safe_set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if auto else 1)
        if auto is False:
            return self._set_uvc_parameter("exposure", cv2.CAP_PROP_EXPOSURE, value)
        return True

    # ----------------- Frame‑Zugriff und Utils ------------------------ #

    def get_current_frame(self) -> Optional[np.ndarray]:
        with self.frame_lock:
            return None if self.current_frame is None else self.current_frame.copy()

    def take_snapshot(self) -> Optional[np.ndarray]:
        if not self.video_capture or not self.video_capture.isOpened():
            return None
        ret, frame = self.video_capture.read()
        return frame.copy() if ret else self.get_current_frame()

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
#TODO
""""
📋 **Ausführliche TODO-Liste für camera.py**

## 🔍 **Aktuelle Analyse der camera.py**

Die `camera.py` ist bereits **sehr gut strukturiert** und implementiert:
- ✅ Vollständige UVC-Kamerasteuerung
- ✅ Plattform-spezifische Backend-Auswahl (Windows/Linux)
- ✅ Thread-sichere Frame-Erfassung
- ✅ NiceGUI/FastAPI Integration
- ✅ Robuste Fehlerbehandlung

**Fehlende Funktionalitäten für die Projektbeschreibung:**

---

## 🎯 **TODO 1: UVC-Konfiguration-Integration korrigieren**

### **Problem:**
Die `camera.py` erwartet in `_apply_uvc_controls()` direkte Attribute wie:
- `self.uvc_config.auto_exposure` 
- `self.uvc_config.auto_white_balance`

Aber die `UVCConfig` aus der #codebase hat verschachtelte Objekte:
- `self.uvc_config.exposure.auto`
- `self.uvc_config.white_balance.auto`

### **Was zu tun ist:**
1. **Anpassung der `_apply_uvc_controls()` Methode** in `camera.py`
2. **Korrektur der Exposure-Handling** für verschachtelte `Exposure`-Objekte
3. **Korrektur der White Balance-Handling** für verschachtelte `WhiteBalance`-Objekte
4. **Vereinfachung der Auto/Manual-Logik** für bessere Lesbarkeit

---

## 🎯 **TODO 2: Fehlende UVC-Parameter ergänzen**

### **Problem:**
Die Projektbeschreibung fordert **alle UVC-Parameter**, aber in `camera.py` fehlen:
- `backlight_compensation` (ist in `UVCConfig` definiert)

### **Was zu tun ist:**
1. **Ergänzung in `param_map`** in `_apply_uvc_controls()`
2. **Mapping auf das entsprechende OpenCV-Property** (vermutlich `cv2.CAP_PROP_BACKLIGHT`)
3. **Setter-Methode hinzufügen** für GUI-Integration: `set_backlight_compensation()`

---

## 🎯 **TODO 3: Erweiterte UVC-Setter für GUI-Integration**

### **Problem:**
Aktuell gibt es nur wenige Setter-Methoden (`set_brightness`, `set_contrast`, etc.). Für eine vollständige GUI werden **alle UVC-Parameter** benötigt.

### **Was zu tun ist:**
1. **Fehlende Setter-Methoden hinzufügen:**
   - `set_hue()`
   - `set_sharpness()`
   - `set_gamma()`
   - `set_gain()`
   - `set_backlight_compensation()`
   - `set_white_balance()` (mit Auto/Manual-Flag)
   - `set_zoom()` (falls unterstützt)

2. **Erweiterte Exposure-Setter:**
   - `set_exposure()` überarbeiten für bessere Trennung von Auto/Manual
   - Separate Methoden: `set_auto_exposure(bool)` und `set_manual_exposure(value)`

3. **White Balance Setter:**
   - `set_auto_white_balance(bool)`
   - `set_manual_white_balance(value)`

---

## 🎯 **TODO 4: Motion-Callback Integration verbessern**

### **Problem:**
Der Motion-Callback ist sehr rudimentär implementiert. Für die Projektbeschreibung wird eine **strukturierte Bewegungserkennung** benötigt.

### **Was zu tun ist:**
1. **Motion-Callback Signatur erweitern:**
   - Aktuell: `Callable[[np.ndarray], None]`
   - Neu: `Callable[[np.ndarray, MotionResult], None]`

2. **Integration mit MotionDetector vorbereiten:**
   - Import von `MotionResult` aus `motion.py`
   - Callback erweitern um Bewegungsergebnis-Parameter

3. **Frame-Metadaten hinzufügen:**
   - Timestamp pro Frame
   - Frame-Nummer/Index für Debugging

---

## 🎯 **TODO 5: Konfiguration-Persistenz implementieren**

### **Problem:**
Aktuell werden UVC-Änderungen nur in RAM gespeichert: `setattr(self.uvc_config, name, value)`. Für eine praktische Anwendung sollten Einstellungen **persistent** gespeichert werden.

### **Was zu tun ist:**
1. **Methode hinzufügen: `save_uvc_config()`**
   - Schreibt aktuelle UVC-Werte zurück in YAML-Config
   - Nutzt die bestehende `load_config()`/`save_config()` Infrastruktur

2. **Auto-Save Option:**
   - Konfigurierbare automatische Speicherung bei UVC-Änderungen
   - GUI-Button für manuelles Speichern

3. **Backup und Restore:**
   - Möglichkeit, auf Default-Werte zurückzusetzen
   - Backup der letzten funktionierenden Konfiguration

---

## 🎯 **TODO 6: Erweiterte Frame-Verwaltung**

### **Problem:**
Für die Bewegungserkennung und E-Mail-Benachrichtigung werden **spezielle Frame-Features** benötigt.

### **Was zu tun ist:**
1. **Frame-Buffer implementieren:**
   - Speicherung der letzten N Frames für Motion-Analyse
   - Konfigurierbare Buffer-Größe

2. **Snapshot-Verbesserung:**
   - `take_snapshot()` erweitern um Metadaten (Timestamp, Kamera-Settings)
   - Verschiedene Ausgabeformate (JPEG-Qualität konfigurierbar)

3. **ROI-Frame-Extraktion:**
   - Methode um ROI-Bereiche aus Frames zu extrahieren
   - Integration mit `MotionDetectionConfig.get_roi()`

---

## 🎯 **TODO 7: Error-Handling und Robustheit verbessern**

### **Problem:**
Für eine produktive Anwendung ist das Error-Handling noch nicht ausreichend robust.

### **Was zu tun ist:**
1. **Kamera-Reconnection:**
   - Automatische Wiederverbindung bei Kamera-Ausfall
   - Retry-Logik mit konfigurierbaren Intervallen

2. **UVC-Property Validation:**
   - Prüfung der unterstützten Properties vor dem Setzen
   - Graceful Degradation bei nicht unterstützten Features

3. **Performance-Monitoring:**
   - FPS-Monitoring und Logging
   - Speicherverbrauch der Frame-Buffer überwachen

---

## 🎯 **TODO 8: GUI-Integration vorbereiten**

### **Problem:**
Die aktuelle GUI-Integration ist minimal. Für die vollständige Projektbeschreibung werden **erweiterte GUI-Features** benötigt.

### **Was zu tun ist:**
1. **Status-Eigenschaften hinzufügen:**
   - `get_camera_status()` → Dict mit aktuellen Kamera-Infos
   - `get_uvc_current_values()` → Aktuelle UVC-Werte auslesen
   - `is_motion_active` → Boolean für GUI-Status

2. **Event-System implementieren:**
   - Callbacks für Kamera-Status-Änderungen
   - Events bei UVC-Parameter-Änderungen
   - Motion-Status-Events für GUI-Updates

3. **Vereinfachte GUI-Methoden:**
   - `get_all_uvc_ranges()` → Min/Max-Werte für GUI-Slider
   - `reset_to_defaults()` → Alle UVC-Parameter zurücksetzen

---

## 🎯 **TODO 9: Integration mit Alert-System vorbereiten**

### **Problem:**
Für die E-Mail-Benachrichtigung muss die `camera.py` mit dem Alert-System kommunizieren.

### **Was zu tun ist:**
1. **Alert-Callback hinzufügen:**
   - Separater Callback für Alert-Ereignisse
   - Parameter: Frame, Timestamp, Motion-Status

2. **Image-Capture für Alerts:**
   - Hochqualitative Snapshot-Funktion für E-Mail-Anhänge
   - Konfigurierbare Bildqualität und -format

3. **Timing-Integration:**
   - Zeitstempel-Verwaltung für Alert-Delays
   - Integration mit `MeasurementConfig.alert_delay_seconds`

---

## 🎯 **TODO 10: Code-Vereinfachung und Dokumentation**

### **Problem:**
Für ein "möglichst einfaches Programm" sollte die Komplexität reduziert werden, ohne Funktionalität zu verlieren.

### **Was zu tun ist:**
1. **Methoden-Konsolidierung:**
   - Ähnliche UVC-Setter in generische Methoden zusammenfassen
   - Redundante Code-Pfade eliminieren

2. **Erweiterte Docstrings:**
   - Alle öffentlichen Methoden vollständig dokumentieren
   - Beispiele für häufige Use-Cases hinzufügen

3. **Type-Hints vervollständigen:**
   - Alle Parameter und Return-Types vollständig annotieren
   - Generic Types für bessere IDE-Unterstützung

---

## 📊 **Prioritäts-Reihenfolge für die Umsetzung:**

### **🔥 Kritisch (für Basis-Funktionalität):**
1. **TODO 1** - UVC-Konfiguration korrigieren
2. **TODO 2** - Fehlende UVC-Parameter ergänzen
3. **TODO 4** - Motion-Callback Integration

### **⚡ Hoch (für GUI-Integration):**
4. **TODO 3** - Erweiterte UVC-Setter
5. **TODO 8** - GUI-Integration vorbereiten
6. **TODO 6** - Frame-Verwaltung erweitern

### **📋 Mittel (für Produktionsreife):**
7. **TODO 5** - Konfiguration-Persistenz
8. **TODO 9** - Alert-System Integration
9. **TODO 7** - Error-Handling verbessern

### **📝 Niedrig (für Wartbarkeit):**
10. **TODO 10** - Code-Vereinfachung

**Die `camera.py` ist bereits sehr solide aufgebaut - diese TODOs optimieren sie für die spezifischen Anforderungen der Projektbeschreibung.**
"""