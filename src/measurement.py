from __future__ import annotations

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable, TYPE_CHECKING, Any
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

# Type checking imports to avoid circular dependencies at runtime
if TYPE_CHECKING:
    from .config import AppConfig, MeasurementConfig
    from .notify import EMailSystem
    from .cam.camera import Camera
    from .cam.motion import MotionResult

from .alert_history import append_history_entry, get_history_dir, to_history_image_storage_path
from .config import get_logger

class MeasurementController:
    """
    Steuert den Messablauf, überwacht Bewegung und löst Alerts aus.
    
    Features:
    - Session-Management (Start/Stop)
    - Alert-Verzögerung (alert_delay)
    - Anti-Spam (cooldown)
    - Thread-sichere Status-Verwaltung
    """

    def __init__(
        self,
        config: MeasurementConfig,
        email_system: Optional[EMailSystem],
        camera: Optional[Camera],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.email_system = email_system
        self.camera = camera
        self.logger = logger or get_logger('measurement')
        
        # -- Session State (Thread-Safe) --
        self.session_lock = threading.RLock()
        self.is_session_active = False
        self.session_id: Optional[str] = None
        self.session_start_time: Optional[datetime] = None
        
        # -- Alert State --
        self.last_motion_time: Optional[datetime] = None
        self.alert_triggered = False
        self.alert_trigger_time: Optional[datetime] = None
        
        # -- Async Helpers --
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MeasCtrl")
        
        # -- Motion History for Debouncing --
        self._motion_history: list[tuple[float, bool]] = []
        self._history_lock = threading.Lock()
        
        # -- GUI Motion Callbacks --
        self._motion_callbacks: list[Callable[[Any], None]] = []
        self._callbacks_lock = threading.Lock()

        # Callbacks registrieren
        if self.camera:
            self.camera.enable_motion_detection(self.on_motion_detected)
            self.logger.info("Motion detection callback registered")

    def register_motion_callback(self, callback: Callable[[Any], None]) -> None:
        """Register a callback to be called when motion events are processed.
        
        This is used by GUI components to refresh their display when motion occurs.
        """
        with self._callbacks_lock:
            if callback not in self._motion_callbacks:
                self._motion_callbacks.append(callback)
                self.logger.debug(f"Registered motion callback: {callback}")

    def start_session(self, session_id: Optional[str] = None) -> bool:
        """Startet eine neue Mess-Session."""
        with self.session_lock:
            if self.is_session_active:
                self.logger.warning("Session already active, cannot start new one")
                return False
            
            self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.is_session_active = True
            self.session_start_time = datetime.now()
            
            # Reset Alert State
            self.last_motion_time = datetime.now()
            self.alert_triggered = False
            self.alert_trigger_time = None
            
            with self._history_lock:
                self._motion_history.clear()
            
            self.logger.info(f"Session {self.session_id} started")
            
            # Notify Email System
            if self.email_system:
                self._executor.submit(
                    self.email_system.send_measurement_event,
                    event="start",
                    session_id=self.session_id,
                    start_time=self.session_start_time
                )
            
            return True

    def stop_session(self, *, reason: str | None = None) -> bool:
        """Stoppt die aktuelle Session."""
        with self.session_lock:
            if not self.is_session_active:
                return False
            
            end_time = datetime.now()
            if self.session_start_time:
                duration = end_time - self.session_start_time
            else:
                duration = timedelta(0)
            
            self.logger.info(f"Session {self.session_id} stopped. Duration: {duration}")
            
            # Notify Email System
            if self.email_system:
                self._executor.submit(
                    self.email_system.send_measurement_event,
                    event="stop",
                    session_id=self.session_id,
                    start_time=self.session_start_time,
                    end_time=end_time,
                    reason=reason
                )
            
            self.is_session_active = False
            self.session_id = None
            self.session_start_time = None
            
            return True

    def on_motion_detected(self, frame: np.ndarray, result: MotionResult) -> None:
        """Callback von der Kamera bei jedem verarbeiteten Frame."""
        
        # 1. Update Motion History (immer, auch ohne aktive Session)
        now_ts = time.time()
        with self._history_lock:
            self._motion_history.append((now_ts, result.motion_detected))
            # Cleanup old history (> 10s)
            cutoff = now_ts - 10.0
            while self._motion_history and self._motion_history[0][0] < cutoff:
                self._motion_history.pop(0)

        # 2. Check Session Logic and Process Motion within a single lock acquisition
        # to prevent race conditions where session is stopped between checks
        with self.session_lock:
            if not self.is_session_active:
                return
            
            current_session_id = self.session_id
            
            if current_session_id is None:
                return

            # 3. Process Motion (within lock to ensure consistency)
            if result.motion_detected:
                self.last_motion_time = datetime.now()
                if self.alert_triggered:
                    self.logger.info("Motion detected - Alert reset")
                    self.alert_triggered = False
                    self.alert_trigger_time = None
            else:
                # Check for Alert Condition (pass session state captured under lock)
                self._check_alert_trigger_locked(current_session_id)

        # 4. Check Session Timeout (outside main lock to avoid deadlock)
        self.check_session_timeout()
        
        # 5. Notify GUI callbacks (outside locks for safety, using snapshot)
        callbacks_snapshot = []
        with self._callbacks_lock:
            callbacks_snapshot = list(self._motion_callbacks)

        for callback in callbacks_snapshot:
            try:
                callback(result)
            except Exception as exc:
                self.logger.debug(f"Motion callback error: {exc}")

    def _check_alert_trigger_locked(self, session_id: str) -> None:
        """Prüft ob ein Alert ausgelöst werden muss. MUST be called while holding session_lock."""
        if self.alert_triggered:
            return
            
        if self.last_motion_time is None:
            self.last_motion_time = datetime.now()
            return

        time_since_motion = (datetime.now() - self.last_motion_time).total_seconds()
        
        if time_since_motion >= self.config.alert_delay_seconds:
            # Double check with history to avoid noise triggers
            if self._confirm_no_motion(duration=2.0):
                # Re-verify session is still active before triggering (race condition prevention)
                if not self.is_session_active or self.session_id != session_id:
                    self.logger.debug("Session ended before alert could be triggered")
                    return
                    
                self.logger.warning(f"ALERT: No motion for {time_since_motion:.1f}s")
                self.alert_triggered = True
                self.alert_trigger_time = datetime.now()
                
                # Trigger Alert Action
                self._executor.submit(self.trigger_alert_sync, session_id)

    def _confirm_no_motion(self, duration: float) -> bool:
        """Bestätigt Bewegungslosigkeit anhand der Historie."""
        now_ts = time.time()
        cutoff = now_ts - duration
        
        with self._history_lock:
            if not self._motion_history:
                return True # No data -> assume no motion? Or wait? Assume yes for safety.
            
            # Check if ANY motion occurred in the last 'duration' seconds
            for ts, motion in reversed(self._motion_history):
                if ts < cutoff:
                    break
                if motion:
                    return False
            
            # If history is empty, we can't confirm "no motion". 
            # Safe default: assume motion might be happening or system just started.
            if not self._motion_history:
                # no history -> cannot rule out motion, treat as motion possible
                return False
                
        return True

    def trigger_alert_sync(self, session_id: str) -> bool:
        """Führt den eigentlichen Alert-Versand aus (läuft im Executor)."""
        if not self.email_system:
            self.logger.warning("No email system configured, cannot send alert")
            return False
            
        # Snapshot holen mit Validation
        frame = None
        if self.camera:
            try:
                if hasattr(self.camera, 'take_snapshot'):
                    frame = self.camera.take_snapshot()
                    # Validate frame type
                    if frame is not None and not isinstance(frame, np.ndarray):
                        self.logger.error(f"take_snapshot returned invalid type: {type(frame)}")
                        frame = None
                else:
                    self.logger.warning("Camera object has no take_snapshot method")
            except Exception as e:
                self.logger.error(f"Error taking snapshot: {e}")
                frame = None
            
        success = self.email_system.send_motion_alert(
            last_motion_time=self.last_motion_time,
            session_id=session_id,
            camera_frame=frame
        )
        
        if success:
            self.logger.info("Alert email sent successfully")
        else:
            self.logger.error("Failed to send alert email")
            
        # Save to History (JSON)
        try:
            self._save_alert_to_history(session_id, frame, email_sent=bool(success))
        except Exception as e:
            self.logger.error(f"Failed to save alert history: {e}")

        return bool(success)

    def _save_alert_to_history(
        self,
        session_id: str,
        frame: Optional[np.ndarray],
        *,
        email_sent: bool,
    ) -> None:
        """Saves the alert event to a JSON history file and saves the image."""
        history_dir = get_history_dir(self.config)
        history_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        filename_ts = timestamp.strftime("%Y%m%d_%H%M%S")
        
        image_path = ""
        if frame is not None:
            image_filename = f"alert_{filename_ts}_{session_id}.jpg"
            image_full_path = history_dir / image_filename
            try:
                # Convert RGB to BGR if needed? Camera usually returns BGR for OpenCV
                # Assuming frame is BGR from cv2.VideoCapture
                if cv2.imwrite(str(image_full_path), frame):
                    image_path = to_history_image_storage_path(image_full_path, history_dir)
                else:
                    self.logger.error(
                        f"Error saving alert image: cv2.imwrite returned False for {image_full_path}. "
                        "Possible causes include disk full, invalid path, insufficient permissions, or invalid image data."
                    )
            except Exception as e:
                self.logger.error(f"Error saving alert image: {e}")

        event_data = {
            "timestamp": ts_str,
            "session_id": session_id,
            "type": "alert",
            "image_path": image_path,
            "details": "No motion detected",
            "email_sent": bool(email_sent),
        }

        append_history_entry(
            event_data,
            history_file=history_dir / 'history.json',
            max_entries=100,
        )

    def check_session_timeout(self) -> None:
        """Prüft ob die maximale Session-Dauer erreicht ist."""
        with self.session_lock:
            if not self.is_session_active or not self.session_start_time:
                return

            # Max duration check
            if self.config.session_timeout_minutes > 0:
                duration = datetime.now() - self.session_start_time
                if duration.total_seconds() >= self.config.session_timeout_minutes * 60:
                    self.logger.info("Session timeout reached")
                    self.stop_session(reason="timeout")

    def get_session_status(self) -> dict:
        """Gibt den aktuellen Status für die GUI zurück."""
        with self.session_lock:
            active = self.is_session_active
            start_time = self.session_start_time
            sid = self.session_id
        
        duration_str = ""
        if active and start_time:
            duration = datetime.now() - start_time
            # Format HH:MM:SS
            total_seconds = int(duration.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_str = f"{hours:02}:{minutes:02}:{seconds:02}"
            
        time_since_motion = 0.0
        if self.last_motion_time:
            time_since_motion = (datetime.now() - self.last_motion_time).total_seconds()

        return {
            "is_active": active,
            "session_id": sid,
            "duration": duration_str,
            "alert_triggered": self.alert_triggered,
            "time_since_motion": time_since_motion,
            "alert_countdown": max(0, self.config.alert_delay_seconds - time_since_motion)
        }

    def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info("Cleaning up MeasurementController")
        self.stop_session(reason="shutdown")
        self._executor.shutdown(wait=False)


def create_measurement_controller_from_config(
    config: AppConfig,
    email_system: Optional[EMailSystem],
    camera: Optional[Camera],
    logger: Optional[logging.Logger] = None
) -> MeasurementController:
    """Factory function."""
    return MeasurementController(
        config=config.measurement,
        email_system=email_system,
        camera=camera,
        logger=logger
    )
