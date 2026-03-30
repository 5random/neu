from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
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

from .alert_history import append_history_entry, get_history_file
from .config import get_logger


def resolve_measurement_stop_event(reason: str | None) -> str:
    """Map a stop reason to the user-facing measurement lifecycle event."""
    normalized_reason = (reason or "").lower()
    return "end" if normalized_reason in {"timeout", "inactivity"} else "stop"


def _sanitize_alert_filename_component(
    value: object,
    *,
    fallback: str = "session",
    max_length: int = 64,
) -> str:
    """Return a safe filename component derived from a session identifier."""
    component = re.sub(r"[^A-Za-z0-9_-]", "_", str(value))
    component = re.sub(r"_+", "_", component).strip("_")
    if not component:
        return fallback

    truncated = component[:max_length].rstrip("_")
    return truncated or fallback


def _encode_history_alert_frame(
    frame: Optional[np.ndarray],
    *,
    image_format: str,
    image_quality: int,
) -> tuple[bool, bytes | None, str | None]:
    """Encode an alert frame for history storage using the configured image format."""
    if frame is None or frame.size == 0:
        return False, None, None

    img_fmt = str(image_format or "jpg").strip().lower()
    is_jpeg = img_fmt in ("jpg", "jpeg")
    params = (
        [cv2.IMWRITE_JPEG_QUALITY, int(image_quality)]
        if is_jpeg else
        [cv2.IMWRITE_PNG_COMPRESSION, 3]
    )

    ok, buf = cv2.imencode(f".{img_fmt}", frame, params)
    if not ok:
        return False, None, None

    return True, buf.tobytes(), img_fmt


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
        self.recent_motion_detected = False
        
        # -- Alert State --
        self.last_motion_time: Optional[datetime] = None
        self.alert_triggered = False
        self.alert_trigger_time: Optional[datetime] = None
        self._alert_generation = 0
        self._alert_dispatch_in_progress = False
        self._alert_dispatch_generation: Optional[int] = None
        self._last_alert_attempt_monotonic: Optional[float] = None
        
        # -- Async Helpers --
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MeasCtrl")
        self._event_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MeasCtrlEvents")
        
        # -- Motion History for Debouncing --
        self._motion_history: deque[tuple[float, bool]] = deque()
        self._history_lock = threading.Lock()
        self._last_motion_summary_log_monotonic: Optional[float] = None
        self._motion_summary_event_count = 0
        self._motion_summary_motion_event_count = 0
        
        # -- GUI Motion Callbacks --
        self._motion_callbacks: list[Callable[[Any], None]] = []
        self._callbacks_lock = threading.Lock()
        self._camera_motion_listener: Optional[Callable[[np.ndarray, MotionResult], None]] = None
        self._timeout_stop_event = threading.Event()
        self._timeout_thread = threading.Thread(
            target=self._timeout_monitor_loop,
            name="MeasCtrl-Timeout",
            daemon=True,
        )
        self._timeout_thread.start()

        # Callbacks registrieren
        if self.camera:
            self._camera_motion_listener = self.on_motion_detected
            self.camera.enable_motion_detection(self._camera_motion_listener)
            self.logger.info("Motion detection callback registered")

        if getattr(self.config, 'auto_start', False):
            try:
                self.start_session()
                self.logger.info("Measurement auto-start activated")
            except Exception as exc:
                self.logger.error(f"Failed to auto-start measurement session: {exc}")

    def update_config(self, new_config: MeasurementConfig) -> None:
        """Update the measurement config used by the running controller."""
        with self.session_lock:
            self.config = new_config
        self.logger.debug("Measurement config updated")

    def _get_config_snapshot(self) -> MeasurementConfig:
        """Return a stable config reference for the duration of an operation."""
        with self.session_lock:
            return self.config

    def register_motion_callback(self, callback: Callable[[Any], None]) -> None:
        """Register a callback to be called when motion events are processed.
        
        This is used by GUI components to refresh their display when motion occurs.
        """
        with self._callbacks_lock:
            if callback not in self._motion_callbacks:
                self._motion_callbacks.append(callback)
                self.logger.debug(f"Registered motion callback: {callback}")

    def unregister_motion_callback(self, callback: Callable[[Any], None]) -> None:
        """Remove a previously registered GUI motion callback."""
        with self._callbacks_lock:
            try:
                self._motion_callbacks.remove(callback)
                self.logger.debug(f"Unregistered motion callback: {callback}")
            except ValueError:
                return

    def _reset_alert_tracking_locked(self) -> None:
        self._alert_generation += 1
        self.alert_triggered = False
        self.alert_trigger_time = None
        self._alert_dispatch_in_progress = False
        self._alert_dispatch_generation = None
        self._last_alert_attempt_monotonic = None

    def _is_alert_task_current_locked(self, session_id: str, alert_generation: int) -> bool:
        return (
            self.is_session_active
            and self.session_id == session_id
            and self._alert_generation == alert_generation
        )

    def _is_abort_requested(self, session_id: str, alert_generation: int) -> bool:
        """Return whether an in-flight alert should abort based on current session state."""
        with self.session_lock:
            return not self._is_alert_task_current_locked(session_id, alert_generation)

    def _get_session_timeout_seconds(self, *, config: MeasurementConfig | None = None) -> int:
        """Return the effective hard session timeout in seconds."""
        if config is None:
            config = self._get_config_snapshot()
        if hasattr(config, "get_session_timeout_seconds"):
            try:
                return max(0, int(config.get_session_timeout_seconds()))
            except Exception:
                self.logger.warning("Invalid session timeout seconds configuration; falling back to minutes")

        raw_minutes = getattr(config, "session_timeout_minutes", 0)
        try:
            return max(0, int(raw_minutes or 0) * 60)
        except (TypeError, ValueError):
            self.logger.warning("Invalid session_timeout_minutes configuration: %r", raw_minutes)
            return 0

    def _sync_email_alert_state(self, *, max_attempts: int = 3) -> None:
        """Synchronize the email alert session state without holding session_lock."""
        if not self.email_system:
            return

        for _ in range(max_attempts):
            with self.session_lock:
                target_session_id = self.session_id if self.is_session_active else None
                target_generation = self._alert_generation

            try:
                self.email_system.reset_alert_state(session_id=target_session_id)
            except Exception as exc:
                self.logger.error(f"Failed to sync email alert state: {exc}")
                return

            with self.session_lock:
                current_session_id = self.session_id if self.is_session_active else None
                current_generation = self._alert_generation

            if (
                current_session_id == target_session_id
                and current_generation == target_generation
            ):
                return

        self.logger.debug(
            "Email alert state changed during sync; controller state moved while reset_alert_state was running"
        )

    def _submit_measurement_event_locked(
        self,
        *,
        event: str,
        session_id: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Queue lifecycle emails while session_lock still defines the event order."""
        if not self.email_system:
            return

        try:
            self._event_executor.submit(
                self.email_system.send_measurement_event,
                event=event,
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                reason=reason,
            )
        except RuntimeError as exc:
            self.logger.error(f"Failed to queue measurement event '{event}': {exc}")

    def start_session(self, session_id: Optional[str] = None) -> bool:
        """Startet eine neue Mess-Session."""
        with self.session_lock:
            if self.is_session_active:
                self.logger.warning("Session already active, cannot start new one")
                return False
            
            self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.is_session_active = True
            self.session_start_time = datetime.now()
            self._last_motion_summary_log_monotonic = None
            self._motion_summary_event_count = 0
            self._motion_summary_motion_event_count = 0
            
            # Reset Alert State
            self.last_motion_time = datetime.now()
            self._reset_alert_tracking_locked()
            
            with self._history_lock:
                self._motion_history.clear()
            
            self.logger.info(f"Session {self.session_id} started")
            current_session_id = self.session_id
            current_start_time = self.session_start_time
            self._submit_measurement_event_locked(
                event="start",
                session_id=current_session_id,
                start_time=current_start_time,
            )

        self._sync_email_alert_state()
         
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
            current_session_id = self.session_id
            current_start_time = self.session_start_time
            stop_event = resolve_measurement_stop_event(reason)
            self._last_motion_summary_log_monotonic = None
            self._motion_summary_event_count = 0
            self._motion_summary_motion_event_count = 0
              
            self._reset_alert_tracking_locked()
            self.is_session_active = False
            self.session_id = None
            self.session_start_time = None
            self._submit_measurement_event_locked(
                event=stop_event,
                session_id=current_session_id,
                start_time=current_start_time,
                end_time=end_time,
                reason=reason,
            )

        self._sync_email_alert_state()
        
        return True

    def on_motion_detected(self, frame: np.ndarray, result: MotionResult) -> None:
        """Callback von der Kamera bei jedem verarbeiteten Frame."""
        
        # 1. Update Motion History (immer, auch ohne aktive Session)
        now_ts = time.time()
        with self._history_lock:
            self._motion_history.append((now_ts, result.motion_detected))
            self._motion_summary_event_count += 1
            if result.motion_detected:
                self._motion_summary_motion_event_count += 1
            # Cleanup old history (> 10s)
            cutoff = now_ts - 10.0
            while self._motion_history and self._motion_history[0][0] < cutoff:
                self._motion_history.popleft()

        # 2. Check Session Logic and Process Motion within a single lock acquisition
        # to prevent race conditions where session is stopped between checks
        session_active = False
        with self.session_lock:
            self.recent_motion_detected = bool(result.motion_detected)
            session_active = self.is_session_active
            current_session_id = self.session_id

            # 3. Process Motion (within lock to ensure consistency)
            if session_active and current_session_id is not None and result.motion_detected:
                self.last_motion_time = datetime.now()
                if self.alert_triggered:
                    self.logger.info("Motion detected - Alert reset")
                self._reset_alert_tracking_locked()
            elif session_active and current_session_id is not None:
                # Check for Alert Condition (pass session state captured under lock)
                self._check_alert_trigger_locked(current_session_id)

        # 4. Notify GUI callbacks (outside locks for safety, using snapshot)
        callbacks_snapshot = []
        with self._callbacks_lock:
            callbacks_snapshot = list(self._motion_callbacks)

        for callback in callbacks_snapshot:
            try:
                callback(result)
            except Exception as exc:
                self.logger.debug(f"Motion callback error: {exc}")

        self._maybe_log_motion_summary()

    def _maybe_log_motion_summary(self) -> None:
        """Emit periodic motion summary logs when enabled in the config."""
        config = self._get_config_snapshot()
        if not bool(getattr(config, 'enable_motion_summary_logs', False)):
            return

        interval_seconds = max(
            5,
            int(getattr(config, 'motion_summary_interval_seconds', 60) or 60),
        )
        now_monotonic = time.monotonic()
        last_log = self._last_motion_summary_log_monotonic
        if last_log is not None and (now_monotonic - last_log) < interval_seconds:
            return

        with self.session_lock:
            session_active = self.is_session_active
            session_id = self.session_id
            recent_motion = self.recent_motion_detected
            last_motion_time = self.last_motion_time

        if not session_active:
            return

        with self._history_lock:
            total_events = self._motion_summary_event_count
            motion_events = self._motion_summary_motion_event_count
            self._motion_summary_event_count = 0
            self._motion_summary_motion_event_count = 0

        time_since_motion = None
        if last_motion_time is not None:
            time_since_motion = (datetime.now() - last_motion_time).total_seconds()

        self.logger.info(
            "Motion summary | active=%s session_id=%s recent_motion=%s time_since_motion=%.1fs events=%s motion_events=%s",
            session_active,
            session_id or "-",
            recent_motion,
            time_since_motion if time_since_motion is not None else -1.0,
            total_events,
            motion_events,
        )
        self._last_motion_summary_log_monotonic = now_monotonic

    def _check_alert_trigger_locked(self, session_id: str) -> None:
        """Check whether the alert condition is active and dispatch if needed."""
        config = self._get_config_snapshot()
        # With no email system configured, a raised alert only affects local state/history.
        # Returning early avoids re-processing the same no-motion condition on every frame.
        if self.alert_triggered and self.email_system is None:
            return

        if self.last_motion_time is None:
            self.last_motion_time = datetime.now()
            return

        time_since_motion = (datetime.now() - self.last_motion_time).total_seconds()

        if time_since_motion >= config.alert_delay_seconds:
            if self._confirm_no_motion(duration=2.0):
                if not self.is_session_active or self.session_id != session_id:
                    self.logger.debug("Session ended before alert could be triggered")
                    return

                if not self.alert_triggered:
                    self.logger.warning(f"ALERT: No motion for {time_since_motion:.1f}s")
                    self.alert_triggered = True
                    self.alert_trigger_time = datetime.now()

                current_generation = self._alert_generation
                if self._alert_dispatch_in_progress:
                    if self._alert_dispatch_generation == current_generation:
                        return
                    self.logger.debug(
                        "Clearing stale alert dispatch flag for generation %s (current %s)",
                        self._alert_dispatch_generation,
                        current_generation,
                    )
                    self._alert_dispatch_in_progress = False
                    self._alert_dispatch_generation = None

                if self.email_system is not None and not self.email_system.can_send_alert(session_id=session_id):
                    return

                if self.email_system is None and self._last_alert_attempt_monotonic is not None:
                    return

                check_interval = max(0.1, float(getattr(config, 'alert_check_interval', 5.0)))
                now_monotonic = time.monotonic()
                if self._last_alert_attempt_monotonic is not None:
                    if (now_monotonic - self._last_alert_attempt_monotonic) < check_interval:
                        return

                self._alert_dispatch_in_progress = True
                self._alert_dispatch_generation = current_generation
                self._last_alert_attempt_monotonic = now_monotonic
                self._executor.submit(self.trigger_alert_sync, session_id, current_generation)

    def _confirm_no_motion(self, duration: float) -> bool:
        """Bestätigt Bewegungslosigkeit anhand der Historie."""
        now_ts = time.time()
        cutoff = now_ts - duration
        
        with self._history_lock:
            if not self._motion_history:
                return False
            
            # Check if ANY motion occurred in the last 'duration' seconds
            for ts, motion in reversed(self._motion_history):
                if ts < cutoff:
                    break
                if motion:
                    return False
        return True

    def trigger_alert_sync(self, session_id: str, alert_generation: int) -> bool:
        """Execute alert side effects if the scheduled alert is still current."""
        with self.session_lock:
            if not self._is_alert_task_current_locked(session_id, alert_generation):
                self.logger.info("Skipping stale alert task for session %s", session_id)
                return False
            last_motion_time = self.last_motion_time

        frame = None
        if self.camera:
            try:
                if hasattr(self.camera, 'take_snapshot'):
                    frame = self.camera.take_snapshot()
                    if frame is not None and not isinstance(frame, np.ndarray):
                        self.logger.error(f"take_snapshot returned invalid type: {type(frame)}")
                        frame = None
                else:
                    self.logger.warning("Camera object has no take_snapshot method")
            except Exception as e:
                self.logger.error(f"Error taking snapshot: {e}")
                frame = None

        email_sent = False
        try:
            with self.session_lock:
                if not self._is_alert_task_current_locked(session_id, alert_generation):
                    self.logger.info(
                        "Skipping stale alert task for session %s after session state changed",
                        session_id,
                    )
                    return False
                last_motion_time = self.last_motion_time

            if not self.email_system:
                self.logger.warning("No email system configured; alert will only be written to history")
            else:
                try:
                    email_sent = bool(self.email_system.send_motion_alert(
                        last_motion_time=last_motion_time,
                        session_id=session_id,
                        camera_frame=frame,
                        abort_checker=lambda: self._is_abort_requested(session_id, alert_generation),
                    ))

                    if email_sent:
                        self.logger.info("Alert email sent successfully")
                    else:
                        self.logger.error("Failed to send alert email")
                except Exception as e:
                    self.logger.error(f"Error while sending alert email: {e}")

            self._save_alert_to_history(session_id, frame, email_sent=email_sent)
        except Exception as e:
            self.logger.error(f"Failed to save alert history: {e}")
        finally:
            with self.session_lock:
                if self._alert_dispatch_generation == alert_generation:
                    self._alert_dispatch_in_progress = False
                    self._alert_dispatch_generation = None

        return email_sent

    def _save_alert_to_history(
        self,
        session_id: str,
        frame: Optional[np.ndarray],
        *,
        email_sent: bool,
    ) -> None:
        """Saves the alert event to a JSON history file and saves the image."""
        config = self._get_config_snapshot()
        history_file = get_history_file(config)
        
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        filename_ts = timestamp.strftime("%Y%m%d_%H%M%S")

        pending_image_filename: str | None = None
        pending_image_bytes: bytes | None = None
        if frame is not None:
            safe_session_id = _sanitize_alert_filename_component(session_id)
            ok, encoded_bytes, image_extension = _encode_history_alert_frame(
                frame,
                image_format=getattr(config, "image_format", "jpg"),
                image_quality=getattr(config, "image_quality", 85),
            )
            if not ok or encoded_bytes is None or image_extension is None:
                self.logger.error("Error encoding alert history image for session %s", session_id)
            else:
                pending_image_filename = f"alert_{filename_ts}_{safe_session_id}.{image_extension}"
                pending_image_bytes = encoded_bytes

        event_data = {
            "timestamp": ts_str,
            "session_id": session_id,
            "type": "alert",
            "image_path": "",
            "details": "No motion detected",
            "email_sent": bool(email_sent),
        }

        append_history_entry(
            event_data,
            history_file=history_file,
            pending_image_filename=pending_image_filename,
            pending_image_bytes=pending_image_bytes,
        )

    def check_session_timeout(self) -> None:
        """Prüft ob die maximale Session-Dauer erreicht ist."""
        stop_reason: str | None = None
        with self.session_lock:
            if not self.is_session_active or not self.session_start_time:
                return

            config = self.config
            now = datetime.now()

            inactivity_timeout_minutes = max(
                0,
                int(getattr(config, 'inactivity_timeout_minutes', 0) or 0),
            )
            if (
                inactivity_timeout_minutes > 0
                and self.last_motion_time is not None
            ):
                inactivity_duration = now - self.last_motion_time
                if inactivity_duration.total_seconds() >= inactivity_timeout_minutes * 60:
                    self.logger.info("Session inactivity timeout reached")
                    stop_reason = "inactivity"

            session_timeout_seconds = self._get_session_timeout_seconds(config=config)
            if stop_reason is None and session_timeout_seconds > 0:
                duration = now - self.session_start_time
                if duration.total_seconds() >= session_timeout_seconds:
                    self.logger.info("Session timeout reached")
                    stop_reason = "timeout"

        if stop_reason is not None:
            self.stop_session(reason=stop_reason)

    def _timeout_monitor_loop(self) -> None:
        """Background loop that checks session timeout once per second."""
        while not self._timeout_stop_event.wait(1.0):
            try:
                self.check_session_timeout()
            except Exception:
                self.logger.exception("Timeout monitoring check failed")

    def get_session_status(self) -> dict:
        """Gibt den aktuellen Status für die GUI zurück."""
        with self.session_lock:
            active = self.is_session_active
            start_time = self.session_start_time
            sid = self.session_id
            last_motion_time = self.last_motion_time
            alert_triggered = self.alert_triggered
            recent_motion_detected = self.recent_motion_detected
            config = self.config

        duration: timedelta | None = None
        if active and start_time:
            duration = datetime.now() - start_time

        session_timeout_seconds = self._get_session_timeout_seconds(config=config)
        session_timeout_minutes = max(
            0,
            int(getattr(config, 'session_timeout_minutes', 0) or 0),
        )
        if session_timeout_seconds > 0 and session_timeout_minutes <= 0:
            session_timeout_minutes = (session_timeout_seconds + 59) // 60

        time_since_motion = 0.0
        if last_motion_time:
            time_since_motion = (datetime.now() - last_motion_time).total_seconds()

        alert_countdown: float | None = None
        if active:
            alert_countdown = max(
                0.0,
                float(getattr(config, 'alert_delay_seconds', 0) or 0) - time_since_motion,
            )

        max_alerts_per_session = max(
            1,
            int(getattr(config, 'max_alerts_per_session', 1) or 1),
        )
        alert_runtime: dict[str, Any] = {
            "alerts_sent_count": 0,
            "max_alerts_per_session": max_alerts_per_session,
            "cooldown_remaining": None,
            "can_send_alert": False,
        }
        if self.email_system is not None and hasattr(self.email_system, "get_alert_status"):
            try:
                runtime_status = self.email_system.get_alert_status()
                alert_runtime.update(
                    {
                        "alerts_sent_count": int(runtime_status.get("alerts_sent_count", 0) or 0),
                        "max_alerts_per_session": int(
                            runtime_status.get("max_alerts_per_session", max_alerts_per_session) or max_alerts_per_session
                        ),
                        "cooldown_remaining": runtime_status.get("cooldown_remaining"),
                        "can_send_alert": bool(runtime_status.get("can_send_alert", False)),
                    }
                )
            except Exception:
                self.logger.debug("Failed to read email alert runtime state", exc_info=True)

        return {
            "is_active": active,
            "session_id": sid,
            "session_start_time": start_time,
            "duration": duration,
            "alert_triggered": alert_triggered,
            "session_timeout_seconds": session_timeout_seconds,
            "session_timeout_minutes": session_timeout_minutes,
            "recent_motion_detected": recent_motion_detected,
            "time_since_motion": time_since_motion,
            "alert_countdown": alert_countdown,
            **alert_runtime,
        }

    def decrement_alert_count(self, *, amount: int = 1) -> bool:
        """Decrease the active session alert counter without resetting cooldown."""
        with self.session_lock:
            session_id = self.session_id if self.is_session_active else None
        if not session_id or self.email_system is None or not hasattr(self.email_system, "decrement_alert_count"):
            return False
        try:
            return bool(self.email_system.decrement_alert_count(session_id=session_id, amount=amount))
        except Exception:
            self.logger.error("Failed to decrement alert count", exc_info=True)
            return False

    def reset_alert_count(self) -> bool:
        """Reset the active session alert counter without resetting cooldown."""
        with self.session_lock:
            session_id = self.session_id if self.is_session_active else None
        if not session_id or self.email_system is None or not hasattr(self.email_system, "reset_alert_count"):
            return False
        try:
            return bool(self.email_system.reset_alert_count(session_id=session_id))
        except Exception:
            self.logger.error("Failed to reset alert count", exc_info=True)
            return False

    def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info("Cleaning up MeasurementController")
        self._timeout_stop_event.set()
        if self.camera and self._camera_motion_listener is not None:
            try:
                self.camera.disable_motion_detection(self._camera_motion_listener)
            except Exception as exc:
                self.logger.debug(f"Failed to unregister camera motion callback: {exc}")
        self.stop_session(reason="shutdown")
        self._timeout_thread.join(timeout=3.0)
        self._event_executor.shutdown(wait=True, cancel_futures=False)
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
