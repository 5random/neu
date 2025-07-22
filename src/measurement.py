"""
Messungssteuerung (Measurement Control) für Webcam-Überwachungssystem.

Dieses Modul implementiert die zentrale Steuerungslogik für Überwachungszeiträume
und Alert-System-Integration gemäß Projektbeschreibung:
- Messungen (Überwachungszeiträume) starten und stoppen
- Alert-Delay-System bei anhaltender Bewegungslosigkeit  
- E-Mail-Trigger-Integration
- Session-Management für GUI-Steuerung

Fokus auf einfache, wartbare Implementation.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
import math
from collections import deque
from itertools import islice
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MeasurementConfig
    from .alert import AlertSystem
    from .cam.motion import MotionResult
    from .cam.camera import Camera


class MeasurementController:
    """
    Zentrale Steuerungslogik für Überwachungssitzungen.
    
    Orchestriert Motion-Detection, Alert-System und Session-Management
    für die Webcam-Überwachung mit Alert-Delay-Funktionalität.
    
    Features:
    - Session-Lifecycle-Management (Start/Stop)
    - Alert-Delay-Timer bei Bewegungslosigkeit
    - Integration mit Motion-Detection und Alert-System
    - GUI-Status-Export für Live-Updates
    
    Usage:
        controller = MeasurementController(config, alert_system)
        controller.start_session()
        # Motion-Events über register_motion_callback()
        if controller.should_trigger_alert():
            success = controller.trigger_alert_sync()    
    """
    
    def __init__(
        self,
        config: 'MeasurementConfig',
        alert_system: Optional['AlertSystem'] = None,
        camera: Optional['Camera'] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert den MeasurementController.
        
        Args:
            config: MeasurementConfig mit Alert-Delay und Session-Parametern
            alert_system: Optional AlertSystem für E-Mail-Benachrichtigungen
            camera: Optional Camera-Instanz für Snapshot-Aufnahmen
            logger: Optional Logger für Session-Tracking
        """
        self.config = config
        self.alert_system = alert_system
        self.camera = camera
        self.logger = logger or logging.getLogger(__name__)
        
        # Session-Status-Management
        self.is_session_active: bool = False
        self.session_start_time: Optional[datetime] = None
        self.session_id: Optional[str] = None
        
        # Motion-Tracking für Alert-System
        self.last_motion_time: Optional[datetime] = None
        self.alert_triggered: bool = False
        self.alert_trigger_time: Optional[datetime] = None
        
        # Motion-Callback für Integration
        self._motion_callbacks: list[Callable[['MotionResult'], None]] = []
        
        # Motion-Historie für bessere Alert-Entscheidungen
        self.motion_history_max_size: int = 10  # Anzahl der gespeicherten Motion-States
        self.motion_history: deque[bool] = deque(maxlen=self.motion_history_max_size)  # Letzte Motion-States (True/False)

        
        # Anti-Spam-Mechanismus
        self.alerts_sent_this_session: int = 0
        self.max_alerts_per_session: int = 5  # Maximal 5 Alerts pro Session
        
        # Alert-Timer-Präzision
        self.alert_check_interval: float = 5.0  # Alle 5 Sekunden Alert-Status prüfen
        self.last_alert_check: Optional[datetime] = None
        
        self.logger.info("MeasurementController initialized")

        self.history_lock = Lock()  # Thread-sicherer Zugriff auf Motion-Historie

        self._alert_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alert_executor")  # Executor für Alert-Operationen
        self._camera_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera_executor")  # Executor für Kamera-Operationen

    # === Session-Management ===
    def _ensure_valid_time(self) -> None:
        alert_delay_minutes = math.ceil(self.config.alert_delay_seconds / 60)
        min_minutes = max(5, alert_delay_minutes)
        if 0 < self.config.session_timeout_minutes < min_minutes:
            self.logger.warning(
                f"Session timeout ({self.config.session_timeout_minutes}min) "
                f"is shorter than alert delay {alert_delay_minutes} minutes - "
                f"setting to {min_minutes}min"
            )
            self.config.session_timeout_minutes = min_minutes
    
    def start_session(self, session_id: Optional[str] = None) -> bool:
        """
        Startet eine neue Überwachungssitzung.
        
        Args:
            session_id: Optional eindeutige Session-ID
            
        Returns:
            True wenn Session erfolgreich gestartet
        """
        if self.is_session_active:
            self.logger.warning("Session already active - stopping previous session")
            self.stop_session()
        
        try:
            self._ensure_valid_time()
            self.session_start_time = datetime.now()
            self.session_id = session_id or f"session_{int(time.time())}"
            self.is_session_active = True
            
            # Alert-Status zurücksetzen
            self.last_motion_time = self.session_start_time
            self.alert_triggered = False
            self.alert_trigger_time = None
            
            self.motion_history.clear()
            self.alerts_sent_this_session = 0
            self.last_alert_check = None
            
            self.logger.info(f"Session started: {self.session_id}")
            return True
            
        except Exception as exc:
            self.logger.error(f"Error starting session: {exc}")
            return False
    
    def stop_session(self) -> bool:
        """
        Stoppt die aktuelle Überwachungssitzung.
        
        Returns:
            True wenn Session erfolgreich gestoppt
        """
        if not self.is_session_active:
            self.logger.warning("No active session to stop")
            return False
        
        try:
            session_duration = self._get_session_duration()
            
            self.is_session_active = False
            self.session_start_time = None

            self.logger.info(f"Session stopped: {self.session_id} "
                           f"(Duration: {session_duration})")

            self.session_id = None

            return True
            
        except Exception as exc:
            self.logger.error(f"Error stopping session: {exc}")
            return False
    
    def check_session_timeout(self) -> bool:
        """
        Prüft und behandelt Session-Timeout.
        
        Stoppt automatisch Sessions die zu lange inaktiv sind.
        
        Returns:
            True wenn Session durch Timeout gestoppt wurde
        """
        if not self.is_session_active:
            return False
        
        # Check if session has a maximum duration configured
        max_min = self.config.session_timeout_minutes
        if max_min and max_min > 0:
            session_duration = self._get_session_duration()
            if session_duration and session_duration.total_seconds() > max_min * 60:
                self.logger.info(f"Session timeout reached after {max_min}min - stopping session")
                self.stop_session()
                return True
        
        # Check for inactivity timeout
        inactivity_timeout = getattr(self.config, 'inactivity_timeout_minutes', 60)  # Default 60 min
        if self.last_motion_time:
            time_since_motion = self._get_time_since_motion()
            if time_since_motion and time_since_motion.total_seconds() > inactivity_timeout * 60:
                self.logger.info(f"Inactivity timeout reached after {inactivity_timeout}min - stopping session")
                self.stop_session()
                return True
        
        return False

    # === Motion-Integration ===
    
    def register_motion_callback(self, callback: Callable[['MotionResult'], None]) -> None:
        """Registriert Callback für Motion-Events."""
        self._motion_callbacks.append(callback)
    
    def on_motion_detected(self, motion_result: 'MotionResult') -> None:
        """
        Verarbeitet Motion-Detection-Ergebnisse.
        
        Args:
            motion_result: MotionResult von MotionDetector
        """
        if not self.is_session_active:
            return

        # Motion-Status in Historie speichern
        with self.history_lock:
            self.motion_history.append(motion_result.motion_detected)
        
        if motion_result.motion_detected:
            self.last_motion_time = datetime.now()
            
            # Alert-Status zurücksetzen bei neuer Bewegung
            if self.alert_triggered:
                self.alert_triggered = False
                self.alert_trigger_time = None
                self.logger.info("Alert state reset - new motion detected")

        self.check_session_timeout()  # Prüfe Session-Timeout
        self._check_alert_trigger()   # Prüfe Alert-Trigger
        
        # Motion-Callbacks weiterleiten
        for callback in list(self._motion_callbacks):
            try:
                callback(motion_result)
            except Exception as exc:
                self.logger.error(f"Error in Motion-Callback: {exc}")

    def _check_alert_trigger(self) -> None:
        """
        Prüft periodisch ob Alert ausgelöst werden soll.
        
        Implementiert das Alert-Delay-System mit:
        - Anti-Spam-Mechanismus (max. Alerts pro Session)
        - Motion-Historie-basierte Entscheidungen
        - Automatische Alert-Auslösung bei Erreichen des Delays
        Verwendet jetzt non-blocking Threading-Variante
        """
        if not self.is_session_active or self.alert_triggered:
            return
        
        if self.alerts_sent_this_session >= self.max_alerts_per_session:
            self.logger.warning("Max alerts per session reached - skipping alert check")
            return
        
        now = datetime.now()
        
        # Prüfe ob genug Zeit seit letztem Check vergangen ist
        if (self.last_alert_check and 
            (now - self.last_alert_check).total_seconds() < self.alert_check_interval):
            return
        
        self.last_alert_check = now
        
        # Motion-Historie analysieren: Gab es kürzlich noch Bewegung?
        # THREAD-SICHER mit Lock:
        if len(self.motion_history) >= 3:
            with self.history_lock:
                # Sichere Kopie erstellen INNERHALB des Locks
                history_copy = list(self.motion_history)
                recent_motion = any(history_copy[-3:]) if len(history_copy) >= 3 else False
            if recent_motion:
                return
        
        # Standard Alert-Delay-Check
        if self.should_trigger_alert():
            try:
                self._alert_executor.submit(self._trigger_alert_sync)
            except Exception as exc:
                self.logger.error(f"Error submitting alert trigger task: {exc}")

    def _trigger_alert_sync(self) -> None:
        """Synchrone Hilfsfunktion für automatische Alert-Auslösung"""
        try:
            # Frühzeitige Validierung um unnötige Arbeit zu vermeiden
            if not self.should_trigger_alert():
                return
                
            success = self.trigger_alert_sync()
            if success:
                self.alerts_sent_this_session += 1
                self.logger.info(f"Alert triggered automatically (sync) "
                               f"({self.alerts_sent_this_session}/{self.max_alerts_per_session})")
        except Exception as exc:
            self.logger.error(f"Error in automatic alert trigger: {exc}")

    def trigger_alert_sync(self) -> bool:
        """
        Löst Alert synchron aus für Threading-basierte Verwendung.
        Optimiert für minimale GUI-Blockierung.
        
        Returns:
            True wenn Alert erfolgreich ausgelöst
        """
        # Doppelte Validierung vermeiden - wurde bereits in _trigger_alert_sync geprüft
        if not self.alert_system:
            # Fallback: Alert als "gesendet" markieren
            self.alert_triggered = True
            self.alert_trigger_time = datetime.now()
            self.logger.warning("Alert triggered (no AlertSystem available, sync)")
            return True
        
        try:
            camera_frame = None
            if self.camera:
                try:
                    # Timeout mit concurrent.futures statt signal (thread-sicher)

                    future = self._camera_executor.submit(self.camera.take_snapshot)
                    try:
                        camera_frame = future.result(timeout=2.0)  # 2 Sekunden Timeout
                    except FutureTimeoutError:
                        self.logger.error("Camera snapshot timed out after 2 seconds")

                except Exception as exc:
                    self.logger.error(f"Snapshot failed or timed out: {exc}")
                    # Weitermachen ohne Bild - Alert trotzdem senden

            # E-Mail-Alert synchron senden (läuft im ThreadPoolExecutor)
            success = self.alert_system.send_motion_alert(
                last_motion_time=self.last_motion_time,
                session_id=self.session_id,
                camera_frame=camera_frame,
            )
            
            if success:
                self.alert_triggered = True
                self.alert_trigger_time = datetime.now()
                self.logger.info("Alert triggered successfully (sync)")
                return True
            else:
                self.logger.error("Alert sending failed (sync)")
                return False
                
        except Exception as exc:
            self.logger.error(f"Error triggering alert (sync): {exc}")
            return False

    # === Alert-System ===
    
    def should_trigger_alert(self) -> bool:
        """
        Prüft ob Alert ausgelöst werden soll.
        
        Returns:
            True wenn Alert-Delay erreicht und noch kein Alert gesendet
        """
        if not self.is_session_active or self.alert_triggered:
            return False
        
        if self.last_motion_time is None:
            reference_time = self.session_start_time
        else:
            reference_time = self.last_motion_time
        if reference_time is None:
            return False

        time_since_motion = datetime.now() - reference_time
        alert_delay = timedelta(seconds=self.config.alert_delay_seconds)
        
        return time_since_motion >= alert_delay

    # === Status-Export für GUI ===
    
    def get_session_status(self) -> Dict[str, Any]:
        """
        Exportiert aktuellen Session-Status für GUI.
        
        Returns:
            Dict mit Session-Informationen einschließlich Alert-System-Status
        """
        # Thread-sichere Motion-Historie-Abfrage
        with self.history_lock:
            motion_history_size = len(self.motion_history)
            recent_motion_detected = (
                any(list(self.motion_history)[-3:])
                if motion_history_size >= 3
                else None
            )
            
        return {
            'is_active': self.is_session_active,
            'session_id': self.session_id,
            'start_time': self.session_start_time,
            'duration': self._get_session_duration(),
            'last_motion_time': self.last_motion_time,
            'time_since_motion': self._get_time_since_motion(),
            'alert_triggered': self.alert_triggered,
            'alert_trigger_time': self.alert_trigger_time,
            'alert_countdown': self._get_alert_countdown(),
            'alerts_sent_this_session': self.alerts_sent_this_session,
            'max_alerts_per_session': self.max_alerts_per_session,
            'motion_history_size': motion_history_size,
            'recent_motion_detected': recent_motion_detected,
            'session_timeout_minutes': self.config.session_timeout_minutes,
        }
    
    # === Private Helper-Methoden ===
    
    def _get_session_duration(self) -> Optional[timedelta]:
        """Berechnet Session-Dauer."""
        if self.session_start_time is None:
            return None
        return datetime.now() - self.session_start_time
    
    def _get_time_since_motion(self) -> Optional[timedelta]:
        """Berechnet Zeit seit letzter Bewegung oder seit Sitzungsstart als Fallback."""
        # Fallback auf session_start_time wenn keine Bewegung registriert wurde
        if self.last_motion_time is None:
            reference_time = self.session_start_time
        else:
            reference_time = self.last_motion_time
        if reference_time is None:
            return None
        return datetime.now() - reference_time
    
    def _get_alert_countdown(self) -> Optional[int]:
        """
        Berechnet verbleibende Zeit bis Alert-Trigger.
        
        Returns:
            Verbleibende Sekunden bis Alert, None wenn nicht relevant
        """
        if not self.is_session_active or self.alert_triggered:
            return None
        
        # Fallback auf session_start_time wenn keine Bewegung registriert wurde
        if self.last_motion_time is None:
            reference_time = self.session_start_time
        else:
            reference_time = self.last_motion_time
        if reference_time is None:
            return None

        # Berechne Zeit seit Referenzzeitpunkt
        time_since_motion = datetime.now() - reference_time
        
        alert_delay_seconds = self.config.alert_delay_seconds
        elapsed_seconds = time_since_motion.total_seconds()
        remaining = alert_delay_seconds - elapsed_seconds
        return max(0, int(remaining))
    
    def cleanup(self) -> None:
        """
        Cleanup-Methode für sauberes Shutdown.
        
        Stoppt aktive Sessions und gibt Ressourcen frei.
        """
        try:
            self.logger.info("Starting MeasurementController cleanup...")

            if hasattr(self, '_alert_executor'):
                self._alert_executor.shutdown(wait=True)
            if hasattr(self, '_camera_executor'):
                self._camera_executor.shutdown(wait=True)
            
            # Session stoppen falls aktiv
            if self.is_session_active:
                self.stop_session()
            
            # Callbacks leeren
            self._motion_callbacks.clear()
            
            # State zurücksetzen
            with self.history_lock:
                self.motion_history.clear()
            
            # Referenzen auf None setzen für Garbage Collection
            self.alert_system = None
            self.camera = None
            
            self.logger.info("MeasurementController cleanup completed")
            
        except Exception as exc:
            self.logger.error(f"Error during MeasurementController cleanup: {exc}")

# === Factory-Funktionen ===

def create_measurement_controller_from_config(
    config_path: Optional[str] = None,
    alert_system: Optional['AlertSystem'] = None,
    camera: Optional['Camera'] = None
) -> MeasurementController:
    """
    Erstellt MeasurementController aus Konfiguration.
    
    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        alert_system: Optional AlertSystem für E-Mail-Funktionalität
        camera: Optional Camera-Instanz für Snapshots
        
    Returns:
        Konfigurierter MeasurementController
    """
    from .config import load_config, logger
    
    path = config_path if config_path is not None else "config/config.yaml"
    config = load_config(path)
    measurement_config = config.measurement
    
    return MeasurementController(measurement_config, alert_system, camera, logger)
