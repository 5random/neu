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
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MeasurementConfig
    from .alert import AlertSystem
    from .cam.motion import MotionResult


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
            controller.trigger_alert()
    """
    
    def __init__(
        self, 
        config: 'MeasurementConfig',
        alert_system: Optional['AlertSystem'] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert den MeasurementController.
        
        Args:
            config: MeasurementConfig mit Alert-Delay und Session-Parametern
            alert_system: Optional AlertSystem für E-Mail-Benachrichtigungen
            logger: Optional Logger für Session-Tracking
        """
        self.config = config
        self.alert_system = alert_system
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
        self.motion_history: list[bool] = []  # Letzte Motion-States (True/False)
        self.motion_history_max_size: int = 10  # Anzahl der gespeicherten Motion-States
        
        # Anti-Spam-Mechanismus
        self.alerts_sent_this_session: int = 0
        self.max_alerts_per_session: int = 5  # Maximal 5 Alerts pro Session
        
        # Alert-Timer-Präzision
        self.alert_check_interval: float = 3.0  # Alle 3 Sekunden Alert-Status prüfen
        self.last_alert_check: Optional[datetime] = None
        
        self.logger.info("MeasurementController initialisiert")
    
    # === Session-Management ===
    
    def start_session(self, session_id: Optional[str] = None) -> bool:
        """
        Startet eine neue Überwachungssitzung.
        
        Args:
            session_id: Optional eindeutige Session-ID
            
        Returns:
            True wenn Session erfolgreich gestartet
        """
        if self.is_session_active:
            self.logger.warning("Session bereits aktiv - stoppe vorherige Session")
            self.stop_session()
        
        try:
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
            
            self.logger.info(f"Session gestartet: {self.session_id}")
            return True
            
        except Exception as exc:
            self.logger.error(f"Fehler beim Session-Start: {exc}")
            return False
    
    def stop_session(self) -> bool:
        """
        Stoppt die aktuelle Überwachungssitzung.
        
        Returns:
            True wenn Session erfolgreich gestoppt
        """
        if not self.is_session_active:
            self.logger.warning("Keine aktive Session zum Stoppen")
            return False
        
        try:
            session_duration = self._get_session_duration()
            
            self.is_session_active = False
            self.session_start_time = None
            
            self.logger.info(f"Session gestoppt: {self.session_id} "
                           f"(Dauer: {session_duration})")
            
            self.session_id = None
            return True
            
        except Exception as exc:
            self.logger.error(f"Fehler beim Session-Stop: {exc}")
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
                self.logger.info(f"Session-Timeout erreicht nach {max_min}min - stoppe Session")
                self.stop_session()
                return True
        
        # Check for inactivity timeout
        inactivity_timeout = getattr(self.config, 'inactivity_timeout_minutes', 60)  # Default 60 min
        if self.last_motion_time:
            time_since_motion = self._get_time_since_motion()
            if time_since_motion and time_since_motion.total_seconds() > inactivity_timeout * 60:
                self.logger.info(f"Inaktivitäts-Timeout erreicht nach {inactivity_timeout}min - stoppe Session")
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
        self.motion_history.append(motion_result.motion_detected)
        if len(self.motion_history) > self.motion_history_max_size:
            self.motion_history.pop(0)  # Älteste Einträge entfernen
        
        if motion_result.motion_detected:
            self.last_motion_time = datetime.now()
            
            # Alert-Status zurücksetzen bei neuer Bewegung
            if self.alert_triggered:
                self.alert_triggered = False
                self.alert_trigger_time = None
                self.logger.info("Alert zurückgesetzt - neue Bewegung erkannt")

        self.check_session_timeout()  # Prüfe Session-Timeout
        self._check_alert_trigger()   # Prüfe Alert-Trigger
        
        # Motion-Callbacks weiterleiten
        for callback in self._motion_callbacks:
            try:
                callback(motion_result)
            except Exception as exc:
                self.logger.error(f"Fehler in Motion-Callback: {exc}")
    
    def _check_alert_trigger(self) -> None:
        """
        Prüft periodisch ob Alert ausgelöst werden soll.
        
        Implementiert das Alert-Delay-System mit:
        - Anti-Spam-Mechanismus (max. Alerts pro Session)
        - Motion-Historie-basierte Entscheidungen
        - Automatische Alert-Auslösung bei Erreichen des Delays
        """
        if not self.is_session_active or self.alert_triggered:
            return
        
        now = datetime.now()
        
        # Prüfe ob genug Zeit seit letztem Check vergangen ist
        if (self.last_alert_check and 
            (now - self.last_alert_check).total_seconds() < self.alert_check_interval):
            return
        
        self.last_alert_check = now
        
        # Anti-Spam-Check: Maximale Alerts pro Session erreicht?
        if self.alerts_sent_this_session >= self.max_alerts_per_session:
            return
        
        # Motion-Historie analysieren: Gab es kürzlich noch Bewegung?
        if len(self.motion_history) >= 3:
            # Wenn in den letzten 3 Motion-Checks noch Bewegung war, warten
            recent_motion = any(self.motion_history[-3:])
            if recent_motion:
                return
        
        # Standard Alert-Delay-Check
        if self.should_trigger_alert():
            # Automatisch Alert auslösen
            if self.trigger_alert():
                self.alerts_sent_this_session += 1
                self.logger.info(f"Alert automatisch ausgelöst "
                               f"({self.alerts_sent_this_session}/{self.max_alerts_per_session})")

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
    
    def trigger_alert(self) -> bool:
        """
        Löst Alert aus (E-Mail-Benachrichtigung).
        
        Returns:
            True wenn Alert erfolgreich ausgelöst
        """
        if not self.should_trigger_alert():
            return False
        
        try:
            if self.alert_system:
                # E-Mail-Alert senden
                success = self.alert_system.send_motion_alert(
                    last_motion_time=self.last_motion_time,
                    session_id=self.session_id
                )
                
                if success:
                    self.alert_triggered = True
                    self.alert_trigger_time = datetime.now()
                    self.logger.info("Alert erfolgreich ausgelöst")
                    return True
                else:
                    self.logger.error("Alert-Versendung fehlgeschlagen")
                    return False
            else:
                # Fallback: Alert als "gesendet" markieren auch ohne AlertSystem
                self.alert_triggered = True
                self.alert_trigger_time = datetime.now()
                self.logger.warning("Alert ausgelöst (kein AlertSystem verfügbar)")
                return True
                
        except Exception as exc:
            self.logger.error(f"Fehler beim Alert-Trigger: {exc}")
            return False
    
    # === Status-Export für GUI ===
    
    def get_session_status(self) -> Dict[str, Any]:
        """
        Exportiert aktuellen Session-Status für GUI.
        
        Returns:
            Dict mit Session-Informationen einschließlich Alert-System-Status
        """
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
            'motion_history_size': len(self.motion_history),
            'recent_motion_detected': any(self.motion_history[-3:]) if len(self.motion_history) >= 3 else None,
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


# === Factory-Funktionen ===

def create_measurement_controller_from_config(
    config_path: Optional[str] = None,
    alert_system: Optional['AlertSystem'] = None
) -> MeasurementController:
    """
    Erstellt MeasurementController aus Konfiguration.
    
    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        alert_system: Optional AlertSystem für E-Mail-Funktionalität
        
    Returns:
        Konfigurierter MeasurementController
    """
    from .config import load_config
    
    path = config_path if config_path is not None else "config/config.yaml"
    config = load_config(path)
    measurement_config = config.measurement
    
    logger = logging.getLogger("measurement")
    
    return MeasurementController(measurement_config, alert_system, logger)