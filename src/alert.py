"""
Alert-System für Webcam-Überwachung mit E-Mail-Benachrichtigung.

Dieses Modul implementiert die E-Mail-Benachrichtigung bei anhaltender 
Bewegungslosigkeit gemäß Projektbeschreibung:
- Einfache SMTP-Integration ohne Sicherheitsfeatures
- E-Mail-Versand an mehrere Empfänger
- Webcam-Bild als Anhang
- Template-System für dynamische Inhalte

Fokus auf einfache, robuste Implementation.
"""

from __future__ import annotations
import smtplib
import logging
import time
import re
import cv2
import numpy as np
import requests
import math
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from .config import EmailConfig, MeasurementConfig, AppConfig, get_logger


class AlertSystem:
    """
    Einfaches Alert-System für E-Mail-Benachrichtigungen.
    
    Versendet E-Mails bei anhaltender Bewegungslosigkeit mit:
    - SMTP-Integration ohne SSL/TLS (wie in Projektbeschreibung)
    - Multi-Empfänger-Support
    - Webcam-Bild als Anhang
    - Template-basierte Nachrichten
    - Anti-Spam-Mechanismus
    
    Usage:
        alert_system = AlertSystem(email_config, measurement_config)
        success = await alert_system.send_motion_alert_async(last_motion_time, session_id)
    """
    
    def __init__(
        self,
        email_config: 'EmailConfig',
        measurement_config: 'MeasurementConfig',
        app_cfg: 'AppConfig',
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert das AlertSystem.
        """

        self.logger = logger or get_logger('alert')

        if app_cfg is None:
            raise ValueError("AppConfig is needed")
        
        self.app_cfg = app_cfg
        self.webcam_cfg = app_cfg.webcam
        self.motion_cfg = app_cfg.motion_detection

        if not email_config:
            raise ValueError("E-Mail-Config is needed")
    
        email_errors = email_config.validate()
        if email_errors:
            raise ValueError(f"invalid E-Mail-Config: {', '.join(email_errors)}")
        
        if not hasattr(email_config, 'recipients') or not email_config.recipients:
            raise ValueError("At least one recipient must be configured")

        if not measurement_config:
            raise ValueError("MeasurementConfig is needed")
        
        measurement_errors = measurement_config.validate()
        if measurement_errors:
            self.logger.warning(f"Invalid measurement config: {', '.join(measurement_errors)}")

        self.email_config = email_config
        self.measurement_config = measurement_config

        # Alert-State-Management
        self.last_alert_time: Optional[datetime] = None
        self.alerts_sent_count: int = 0
        self.cooldown_minutes: int = max(
            5,
            math.ceil(self.measurement_config.alert_delay_seconds / 60),
        )  # Minimum 5 Minuten zwischen E-Mails

        self._state_lock = threading.RLock()
        self._smtp_lock = threading.Lock()
        self._smtp_connection: Optional[smtplib.SMTP] = None
        self._connection_timeout: int = 30
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._alert_system_cleanup = False

        self.logger.info("AlertSystem initialized")

    # ------------------------------------------------------------------
    # SMTP connection helpers
    # ------------------------------------------------------------------
    def _ensure_smtp_connection(self) -> smtplib.SMTP:
        """Create SMTP connection if not already open."""
        current_email_config = self._get_current_email_config()
        if self._smtp_connection is None:
            self._smtp_connection = smtplib.SMTP(
                current_email_config.smtp_server,
                current_email_config.smtp_port,
                timeout=self._connection_timeout,
            )
        return self._smtp_connection

    def _close_smtp_connection(self) -> None:
        """Close current SMTP connection if open."""
        if self._smtp_connection is not None:
            try:
                self._smtp_connection.quit()
            except Exception as exc:
                self.logger.warning(f"Error closing SMTP connection: {exc}")
            finally:
                self._smtp_connection = None

    def close(self) -> None:
        """Public method to close resources."""
        with self._smtp_lock:
            self._close_smtp_connection()

    def __del__(self) -> None:
        self.close()
    
    def refresh_config(self) -> None:
        """
        Aktualisiert die Konfigurationsreferenzen.
        Sollte nach Konfigurationsänderungen aufgerufen werden.
        """
        with self._state_lock:
            # Referenzen aktualisieren
            self.email_config = self.app_cfg.email
            self.measurement_config = self.app_cfg.measurement
            self.webcam_cfg = self.app_cfg.webcam
            self.motion_cfg = self.app_cfg.motion_detection
            
            # Cooldown neu berechnen
            self.cooldown_minutes = max(
                5,
                math.ceil(self.measurement_config.alert_delay_seconds / 60),
            )
            
            self.logger.info("Alert-Configuration refreshed")
    
    def _get_current_email_config(self) -> 'EmailConfig':
        """
        Gibt die aktuelle E-Mail-Konfiguration zurück.
        
        Returns:
            Aktuelle EmailConfig-Instanz
        """
        with self._state_lock:
            return self.app_cfg.email
    
    def send_motion_alert(
        self,
        last_motion_time: Optional[datetime] = None,
        session_id: Optional[str] = None,
        camera_frame: Optional[np.ndarray] = None
    ) -> bool:
        """
        Sendet E-Mail-Alert bei Bewegungslosigkeit.
        
        Args:
            last_motion_time: Zeitpunkt der letzten Bewegung
            session_id: ID der aktuellen Session
            camera_frame: Optional aktuelles Kamera-Bild
            
        Returns:
            True wenn E-Mail erfolgreich gesendet
        """
        if self._alert_system_cleanup:
            self.logger.error("AlertSystem has been cleaned up, cannot send alert")
            raise RuntimeError("AlertSystem has been cleaned up")
    
        current_time = datetime.now()
        current_email_config = self._get_current_email_config()

        with self._state_lock:
            if not self._should_send_alert_unsafe():
                return False
            
            previous_alert_time = self.last_alert_time
            previous_count = self.alerts_sent_count
            
            self.last_alert_time = current_time
            temp_count = self.alerts_sent_count + 1

            # E-Mail-Template rendern
            try:
                template = current_email_config.alert_template()
                timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")

                template_params = {
                    'timestamp': timestamp,
                    'session_id': session_id or "unknown",
                    'last_motion_time': last_motion_time.strftime("%H:%M:%S") if last_motion_time else "unknown",
                    'website_url': current_email_config.website_url or "unknown",
                    'camera_index': self.webcam_cfg.camera_index if self.webcam_cfg else "unknown",
                    'sensitivity': self.motion_cfg.sensitivity if self.motion_cfg else "unknown",
                    'roi_enabled': self.motion_cfg.get_roi().enabled if self.motion_cfg else "unknown"
                }
                
                subject = template.subject.format(**template_params)

                body = template.body.format(**template_params)
                
            except (KeyError, ValueError, AttributeError) as e:
                self.logger.error(f"Error when rendering the email template: {e}, use fallback template")
                timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
                subject = f"CVD-Alert: No motion detected - {timestamp}"
                body = (
                    f"Motion has not been detected since {timestamp}!\n"
                    f"Please check the website at: {current_email_config.website_url}\n\n"
                    f"Details:\n"
                    f"Session-ID: {session_id or 'unknown'}\n"
                    f"Camera: Index currently not available\n"
                    f"Sensitivity: currently not available\n"
                    f"ROI enabled: currently not available\n"
                    f"Last motion at {last_motion_time.strftime('%H:%M:%S') if last_motion_time else 'unknown'}.\n"
                    f"Attached is the current webcam image."
                )
            
            # E-Mail-Nachricht erstellen
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                img_buffer = None
                filename: str | None = None
                saved_frame_path: Optional[Path] = None
                ok = False
                
                if camera_frame is not None:
                    ok, img_buffer, filename = self._encode_frame(camera_frame, ts=timestamp)

                    if ok and img_buffer is not None and filename is not None and self.measurement_config.save_alert_images:
                        self._save_alert_image(img_buffer, filename)

                msg = self._create_email_message(subject, body, current_email_config.recipients)

                # Bild-Anhang hinzufügen wenn verfügbar
                if img_buffer is not None and filename is not None:
                    img_attach = MIMEImage(img_buffer.tobytes())
                    img_attach.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                    msg.attach(img_attach)

                # E-Mail versenden
                success_count = self._send_emails_batch(msg, current_email_config.recipients)

                # Erfolg wenn mindestens eine E-Mail gesendet wurde
                if success_count > 0:
                    with self._state_lock:
                        self.alerts_sent_count = temp_count
                    self.logger.info(
                        f"Alert #{temp_count} sent ({success_count}/{len(current_email_config.recipients)} successful)"
                    )
                    if saved_frame_path:
                        try:
                            saved_frame_path.unlink()
                            self.logger.info(f"Alert image file {saved_frame_path} deleted")
                        except Exception as e:
                            self.logger.error(f"Error deleting alert image file {saved_frame_path}: {e}")
                    return True
                else:
                    # Rollback bei Fehlschlag
                    if saved_frame_path:
                        self.logger.warning(f"Alert image file {saved_frame_path} not sent, keeping it")

                    with self._state_lock:
                        self.last_alert_time = previous_alert_time
                        self.alerts_sent_count = previous_count
                        self.logger.error("All email sending attempts failed, state reset")
                    return False
                
            except Exception as exc:
                with self._state_lock:
                        self.last_alert_time = previous_alert_time
                        self.alerts_sent_count = previous_count
                self.logger.error(f"Critical error when sending alert: {exc}; state reset")
                return False

    async def send_motion_alert_async(
        self,
        last_motion_time: Optional[datetime] = None,
        session_id: Optional[str] = None,
        camera_frame: Optional[np.ndarray] = None
    ) -> bool:
        """ Async Wrapper für send_motion_alert """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self.send_motion_alert, last_motion_time, session_id, camera_frame)

    def _send_emails_batch(self, message: MIMEMultipart, recipients: List[str], max_retries: int = 3) -> int:
        """Send a single message to multiple recipients"""

        success_count = 0
        current_email_config = self._get_current_email_config()

        self.logger.info("=" * 50)
        self.logger.info("📧 STARTING EMAIL BATCH SEND!")
        self.logger.info("=" * 50)
        self.logger.info(f"📊 EMAIL CONFIGURATION:")
        self.logger.info(f"   SMTP Server: {current_email_config.smtp_server}")
        self.logger.info(f"   SMTP Port: {current_email_config.smtp_port}")
        self.logger.info(f"   Sender Email: {current_email_config.sender_email}")
        self.logger.info(f"   Recipients: {recipients} ({len(recipients)} total)")
        self.logger.info(f"   Max Retries: {max_retries}")
        self.logger.info(f"   Connection Timeout: {self._connection_timeout}s")
        self.logger.info("=" * 50)

        for attempt in range(max_retries):
            try:
                with self._smtp_lock:
                    with smtplib.SMTP(
                        current_email_config.smtp_server,
                        current_email_config.smtp_port,
                        timeout=self._connection_timeout,
                    ) as smtp:
                        failed = smtp.sendmail(
                            current_email_config.sender_email,
                            recipients,
                            message.as_string(),
                        )
                        success_count = len(recipients) - len(failed)

                        if failed:
                            self.logger.warning(f"Failed to send email to: {failed}")

                if success_count > 0:
                    break

            except (smtplib.SMTPException, ConnectionError, OSError) as exc:
                self.logger.error(f"❌ SMTP ATTEMPT {attempt + 1} FAILED: {type(exc).__name__}")
                self.logger.error(f"   Error: {exc}")
                self.logger.error(f"   📡 CONNECTION DETAILS:")
                self.logger.error(f"      Server: {current_email_config.smtp_server}")
                self.logger.error(f"      Port: {current_email_config.smtp_port}")
                self.logger.error(f"      Sender: {current_email_config.sender_email}")
                self.logger.error(f"      Timeout: {self._connection_timeout}s")
                self.logger.error(f"      Recipients: {recipients}")

                with self._smtp_lock:
                    self._close_smtp_connection()
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(2 ** attempt)
                    continue
                else:
                    self.logger.error(
                        f"SMTP-connection failed after {max_retries} attempts: {exc}"
                    )
            except Exception as exc:
                self.logger.error(f"❌ CRITICAL ERROR: {type(exc).__name__}")
                self.logger.error(f"   Error: {exc}")
                self.logger.error(f"   📡 CONNECTION DETAILS:")
                self.logger.error(f"      Server: {current_email_config.smtp_server}")
                self.logger.error(f"      Port: {current_email_config.smtp_port}")
                self.logger.error(f"      Sender: {current_email_config.sender_email}")
                self.logger.error(f"      Timeout: {self._connection_timeout}s")
                self.logger.error(f"      Recipients: {recipients}")

                with self._smtp_lock:
                    self._close_smtp_connection()
                break
        
        self.logger.info("=" * 50)
        if success_count > 0:
            self.logger.info(f"✅ EMAIL BATCH COMPLETED SUCCESSFULLY")
            self.logger.info(f"   📊 Results: {success_count}/{len(recipients)} emails sent")
            self.logger.info(f"   📡 Used config: Server: {current_email_config.smtp_server}; Port: {current_email_config.smtp_port}")
            self.logger.info(f"   📤 Sender: {current_email_config.sender_email}")
        else:
            self.logger.error(f"❌ EMAIL BATCH FAILED COMPLETELY")
            self.logger.error(f"   📊 Results: 0/{len(recipients)} emails sent")
            self.logger.error(f"   📡 Failed config: Server: {current_email_config.smtp_server}; Port: {current_email_config.smtp_port}")
            self.logger.error(f"   📤 Failed sender: {current_email_config.sender_email}")
            self.logger.error(f"   🎯 Target recipients: {recipients}")
        
        self.logger.info("=" * 50)
        return success_count

    def _should_send_alert_unsafe(self) -> bool:
        """
        Prüft Anti-Spam-Mechanismus.
        
        Returns:
            True wenn Alert gesendet werden kann
        """
        if self.last_alert_time is None:
            return True
        
        time_since_last = datetime.now() - self.last_alert_time
        cooldown_reached = time_since_last.total_seconds() >= self.cooldown_minutes * 60
        
        if not cooldown_reached:
            remaining = self.cooldown_minutes * 60 - time_since_last.total_seconds()
            self.logger.debug(f"Alert-cooldown active, remaining: {remaining:.0f}s")

        return cooldown_reached
    
    def _should_send_alert(self) -> bool:
        with self._state_lock:
            return self._should_send_alert_unsafe()

    def _create_email_message(self, subject: str, body: str, recipients: list[str]) -> MIMEMultipart:
        """
        Erstellt MIME-Multipart-E-Mail-Nachricht.
        
        Args:
            subject: E-Mail-Betreff
            body: E-Mail-Text
            recipient: Empfänger-Adresse
            
        Returns:
            MIME-Multipart-Nachricht
        """
        current_email_config = self._get_current_email_config()

        msg = MIMEMultipart()
        msg['From'] = current_email_config.sender_email
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        
        # Text-Inhalt hinzufügen
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        return msg
    
    def _encode_frame(
        self,
        frame: np.ndarray,
        ts: Optional[str] = None
    ) -> tuple[bool, Optional[np.ndarray], Optional[str]]:
        """
        Kodiert ein BGR‑Frame in JPEG/PNG gemäß MeasurementConfig.

        Args:
            frame: OpenCV‑Frame (BGR‑ndarray)
            ts:   Optional Zeitstempel‑String; wenn None ⇒ jetzt erzeugen

        Returns:
            (ok, buffer, filename)
            ok        - True wenn Encoding erfolgreich
            buffer    - kodiertes Bild als np.ndarray oder None
            filename  - empfohlener Dateiname (str) oder None
        """
        if frame is None or frame.size == 0:
            return False, None, None

        img_fmt = self.measurement_config.image_format.lower()
        # „jpg“ und „jpeg“ behandeln wir gleich
        is_jpeg = img_fmt in ("jpg", "jpeg")

        params = (
            [cv2.IMWRITE_JPEG_QUALITY, self.measurement_config.image_quality]
            if is_jpeg else
            [cv2.IMWRITE_PNG_COMPRESSION, 3]
        )

        ok, buf = cv2.imencode(f".{img_fmt}", frame, params)
        if not ok:
            return False, None, None

        if ts is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_ts = re.sub(r"[^\w\s-]", "", ts)[:50]
        filename = f"alert_{safe_ts}.{img_fmt}"

        return True, buf, filename
    
    def _attach_camera_image(
        self,
        msg: MIMEMultipart,
        frame: np.ndarray,
        ts: Optional[str] = None
    ) -> None:
        """
        Hängt das übergebene BGR‑Frame als Anhang an die MIME‑Nachricht an.

        Args:
            msg:   Bereits erzeugte MIME‑Nachricht
            frame: OpenCV‑Frame (BGR)
            ts:    Zeitstempel‑String (wird für Dateinamen genutzt);
                None ⇒ jetzt erzeugen
        """
        # --- Bild kodieren ---------------------------------------------------
        ok, buf, filename = self._encode_frame(frame, ts=ts)
        if not ok or buf is None or filename is None:
            self.logger.warning("Image encoding failed - no attachment added")
            return
        # --------------------------------------------------------------------

        # --- Attachment erzeugen & anhängen ---------------------------------
        img_attach = MIMEImage(buf.tobytes())           # buf ist ndarray mit Bytes
        img_attach.add_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"'
        )
        msg.attach(img_attach)

        self.logger.debug(
            "Image attachment added: %s (%d bytes)", filename, buf.size
        )

    def _save_alert_image(self, image_buffer: np.ndarray, filename: str) -> Optional[Path]:
        """
        Saves alert image locally.

        Args:
            image_buffer: Image data
            filename: Filename
        """
        try:
            if self.measurement_config:
                save_path = Path(self.measurement_config.image_save_path)
                save_path.mkdir(parents=True, exist_ok=True)
                
                file_path = save_path / filename
                if image_buffer is None or image_buffer.size == 0:
                    self.logger.warning(f'No valid image buffer to save, skipping save: {filename}')
                    return
                
                with open(file_path, 'wb') as f:
                    f.write(image_buffer.tobytes())
                
                self._cleanup_image(save_path)
                self.logger.info(f"Alert image saved: {file_path}")
                return file_path
        except Exception as exc:
            self.logger.error(f"Error saving alert image: {exc}")
            
        return None

    def _cleanup_image(self, save_path: Path, max_files: int = 100) -> None:
        """
        Cleans up old alert images in the save path.

        Args:
            save_path: Path to the image save location
        """
        try:
            image_files = (
                list(save_path.glob("alert_*.jpg")) +
                list(save_path.glob("alert_*.jpeg")) +
                list(save_path.glob("alert_*.png"))
            )
            if len(image_files) > max_files:
                # Sort by modification time, delete oldest
                image_files.sort(key=lambda x: x.stat().st_mtime)
                for old_file in image_files[:-max_files]:
                    old_file.unlink()
                    self.logger.debug(f"Old alert image deleted: {old_file}")
        except Exception as exc:
            self.logger.error(f"Error cleaning up old images: {exc}")

    # === Status export for GUI ===

    def get_alert_status(self) -> Dict[str, Any]:
        """
        Exports alert status for GUI.

        Returns:
            Dict with alert information
        """
        current_email_config = self._get_current_email_config()
        with self._state_lock:
            return {
                'last_alert_time': self.last_alert_time,
                'alerts_sent_count': self.alerts_sent_count,
                'cooldown_remaining': self._get_cooldown_remaining_unsafe(),
                'can_send_alert': self._should_send_alert_unsafe(),
                'configured_recipients': len(current_email_config.recipients),
                'smtp_server': current_email_config.smtp_server
            }

    def _get_cooldown_remaining_unsafe(self) -> Optional[float]:
        """
        Berechnet verbleibende Cooldown-Zeit.
        
        Returns:
            Verbleibende Sekunden, None wenn kein Cooldown aktiv
        """
        if self.last_alert_time is None:
            return None
        
        time_since_last = datetime.now() - self.last_alert_time
        cooldown_seconds = self.cooldown_minutes * 60
        elapsed = time_since_last.total_seconds()
        
        if elapsed >= cooldown_seconds:
            return None

        return cooldown_seconds - elapsed
        
    def _get_cooldown_remaining(self) -> Optional[float]:
        with self._state_lock:
            return self._get_cooldown_remaining_unsafe()

    def test_connection(self) -> bool:
        """
        Testet SMTP-Verbindung ohne E-Mail zu senden.
        
        Returns:
            True wenn Verbindung erfolgreich
        """
        current_email_config = self._get_current_email_config()

        self.logger.info(f"Server: {current_email_config.smtp_server}")
        self.logger.info(f"Port: {current_email_config.smtp_port}")
        self.logger.info(f"Timeout: {self._connection_timeout}s")

        with self._smtp_lock:
            try:
                with smtplib.SMTP(current_email_config.smtp_server, current_email_config.smtp_port, timeout=self._connection_timeout) as smtp:
                    smtp.noop()  # Simple test command
                    self.logger.info("SMTP connection test successful")
                    return True
            except Exception as exc:
                self.logger.error(f"SMTP connection test failed: {exc}")
                self.logger.error(f"   Server: {current_email_config.smtp_server}:{current_email_config.smtp_port}")
                return False
    
    def send_test_email(self) -> bool:
        """
        Sendet Test-E-Mail an alle konfigurierten Empfänger.

        Returns:
            True wenn mindestens eine E-Mail erfolgreich gesendet
        """
        try:
            current_email_config = self._get_current_email_config()

            self.logger.info("=" * 50)
            self.logger.info("🧪 SENDING TEST EMAIL")
            self.logger.info("=" * 50)
            self.logger.info(f"📡 SMTP CONFIGURATION:")
            self.logger.info(f"   Server: {current_email_config.smtp_server}")
            self.logger.info(f"   Port: {current_email_config.smtp_port}")
            self.logger.info(f"   Sender: {current_email_config.sender_email}")
            self.logger.info(f"   Recipients: {current_email_config.recipients}")
            self.logger.info(f"   Website URL: {current_email_config.website_url}")
            self.logger.info("=" * 50)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            subject = f"Test email - {timestamp}"
            # Beispielinhalt der Test-E-Mail erzeugen
            test_message =(
                    f"Motion has not been detected since {timestamp}!\n"
                    f"Please check the website at: {current_email_config.website_url}\n\n"
                    f"Details:\n"
                    f"Session-ID: Test\n"
                    f"Camera: Test\n"
                    f"Sensitivity: Test\n"
                    f"ROI active: currently not available\n"
                    f"Last motion at: not available, as this is a test.\n"
                    f"Attached is the current webcam image."
                )
            
            IMG_SRC = 'https://picsum.photos/id/325/720/405'
            try:
                response = requests.get(IMG_SRC, timeout=10)
                response.raise_for_status()  # Raise an error for bad responses
                img_array = np.frombuffer(response.content, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                if frame is None or frame.size == 0:
                    self.logger.warning("No valid test image received")
                    frame = None
            except Exception as exc:
                self.logger.warning(f"Error retrieving test image: {exc}")
                frame = None

            msg = self._create_email_message(subject, test_message, current_email_config.recipients)
            if frame is not None:
                self._attach_camera_image(msg, frame, timestamp)

            success_count = self._send_emails_batch(msg, current_email_config.recipients)
            return success_count > 0
            
        except Exception as exc:
            current_email_config = self._get_current_email_config()
            self.logger.error("=" * 50)
            self.logger.error(f"💥 TEST EMAIL FAILED: {type(exc).__name__}")
            self.logger.error("0" * 50)
            self.logger.error(f"   Error: {exc}")
            self.logger.error(f"   📡 CONFIG USED:")
            self.logger.error(f"      Server: {current_email_config.smtp_server}")
            self.logger.error(f"      Port: {current_email_config.smtp_port}")
            self.logger.error(f"      Sender: {current_email_config.sender_email}")
            self.logger.error(f"      Recipients: {current_email_config.recipients}")
            self.logger.error("=" * 50)
            return False
    
    async def send_test_email_async(self) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self.send_test_email)
    
    def cleanup(self) -> None:
        """
        Cleanup-Methode für sauberes Shutdown.
        
        Schließt SMTP-Verbindungen und gibt Ressourcen frei.
        """
        try:
            self.logger.info("Starting AlertSystem cleanup...")
            
            # ThreadPoolExecutor shutdown
            if hasattr(self, '_executor'):
                self._executor.shutdown(wait=True)
            
            # SMTP-Verbindung schließen falls vorhanden
            with self._smtp_lock:
                if self._smtp_connection:
                    try:
                        self._smtp_connection.quit()
                    except Exception as e:
                        self.logger.debug(f"Error closing SMTP connection: {e}")
                    finally:
                        self._smtp_connection = None
            
            # State zurücksetzen
            with self._state_lock:
                self.last_alert_time = None
                self.alerts_sent_count = 0
            
            self._alert_system_cleanup = True  # Set cleanup flag
            self.logger.info("AlertSystem cleanup completed")
            
        except Exception as exc:
            self.logger.error(f"Error during AlertSystem cleanup: {exc}")
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of the AlertSystem"""
        health = {
            'status': 'healthy',
            'checks': {},
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # SMTP-Verbindungstest
            smtp_ok = self.test_connection()
            health['checks']['smtp_connection'] = {
                'status': 'ok' if smtp_ok else 'error',
                'message': 'SMTP connection successful' if smtp_ok else 'SMTP connection failed'
            }

            # Configuration validation
            config_ok = True
            config_message = "Configuration valid"
            try:
                if not self.email_config.recipients:
                    config_ok = False
                    config_message = "No email recipients configured"
            except Exception as exc:
                config_ok = False
                config_message = f"Configuration error: {exc}"

            health['checks']['configuration'] = {
                'status': 'ok' if config_ok else 'error',
                'message': config_message
            }
            
            # Alert-State-Status
            with self._state_lock:
                health['checks']['alert_state'] = {
                    'status': 'ok',
                    'last_alert_time': self.last_alert_time.isoformat() if self.last_alert_time else None,
                    'alerts_sent_count': self.alerts_sent_count,
                    'cooldown_remaining': self._get_cooldown_remaining_unsafe()
                }
            
            # Gesamtstatus bestimmen
            if not all(check['status'] == 'ok' for check in health['checks'].values() if isinstance(check, dict)):
                health['status'] = 'degraded'
            
        except Exception as exc:
            health['status'] = 'error'
            health['error'] = str(exc)
            self.logger.error(f"Health check failed: {exc}")

        return health
    
    def get_metrics(self) -> Dict[str, Any]:
        """Exports metrics for monitoring"""
        current_email_config = self._get_current_email_config()
        with self._state_lock:
            return {
                'alerts_sent_total': self.alerts_sent_count,
                'last_alert_timestamp': self.last_alert_time.timestamp() if self.last_alert_time else None,
                'cooldown_remaining_seconds': self._get_cooldown_remaining_unsafe(),
                'recipients_configured': len(current_email_config.recipients),
                'cooldown_minutes_configured': self.cooldown_minutes
            }


# === Factory-Funktionen ===

def create_alert_system_from_config(
    config: Optional[AppConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> AlertSystem:
    """
    Erstellt AlertSystem aus Konfiguration.
    
    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        
    Returns:
        Konfiguriertes AlertSystem
    """
    from .config import load_config
    
    if config is None:
        config = load_config("config/config.yaml")
    
    logger = get_logger("alert")
    
    return AlertSystem(config.email, config.measurement, config, logger)

# Test-Code zur Verifikation

# Test-Code zur Verifikation
def test_template_rendering():
    """Testet ob Template-Rendering funktioniert"""
    import sys
    from pathlib import Path
    # Projekt-Root zum Python-Pfad hinzufügen
    project_root = Path(__file__).parents[1]
    sys.path.insert(0, str(project_root))
    from src.config import load_config
    logger = logging.getLogger(__name__)
    try:
        """Testet ob Template-Rendering funktioniert"""
        template = load_config("config/config.yaml").email.alert_template()

        test_params = {
            'timestamp': '2025-01-14 10:30:00',
            'session_id': 'TEST-123',
            'last_motion_time': '10:25:00',
            'website_url': 'http://localhost:8080',
            'camera_index': 0,
            'sensitivity': 0.1,
            'roi_enabled': True
        }
        
        try:
            subject = template.subject.format(**test_params)
            body = template.body.format(**test_params)
            logger.info("✅ Template-Rendering erfolgreich")
            logger.info("Subject: %s", subject)
            logger.info("Body: %s", body)
            return True
        except KeyError as e:
            logger.error("❌ Template-Parameter fehlt: %s", e)
            return False
    except Exception as e:
        logger.error("❌ Template-Fehler: %s", e)
        return False

# Testen
if __name__ == "__main__":
    test_template_rendering()
