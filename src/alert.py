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
from datetime import datetime
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import EmailConfig, MeasurementConfig, AppConfig


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
        success = alert_system.send_motion_alert(last_motion_time, session_id)
    """
    
    def __init__(
        self,
        email_config: 'EmailConfig',
        measurement_config: Optional['MeasurementConfig'] = None,
        app_cfg: Optional['AppConfig'] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert das AlertSystem.
        
        Args:
            email_config: E-Mail-Konfiguration mit SMTP-Einstellungen
            measurement_config: Optional für Bild-Speicherung
            logger: Optional Logger für Alert-Tracking
        """
        if app_cfg is None:
            raise ValueError("AppConfig ist erforderlich")
        # app_cfg.webcam ist eine WebcamConfig-Instanz
        self.webcam_cfg = app_cfg.webcam
        # app_cfg.motion_detection ist eine MotionDetectionConfig-Instanz
        self.motion_cfg = app_cfg.motion_detection
        self.email_cfg = app_cfg.email

        if not email_config:
            raise ValueError("E-Mail-Konfiguration ist erforderlich")
    
        if email_config.validate():
            raise ValueError("Ungültige E-Mail-Konfiguration")
        
        if not hasattr(email_config, 'smtp_server') or not email_config.smtp_server:
            raise ValueError("SMTP-Server darf nicht leer sein")
        
        if not hasattr(email_config, 'smtp_port') or not email_config.smtp_port or not (1 <= email_config.smtp_port <= 65535):
            raise ValueError("SMTP-Port muss zwischen 1 und 65535 liegen")
        
        email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

        if not hasattr(email_config, 'recipients') or not email_config.recipients:
            raise ValueError("Mindestens ein Empfänger muss konfiguriert sein")

        for recipient in email_config.recipients:
            if not isinstance(recipient, str) or not email_pattern.match(recipient):
                raise ValueError(f"Ungültige E-Mail-Adresse: {recipient}")

        if not hasattr(email_config, 'sender_email') or not email_pattern.match(email_config.sender_email):
            raise ValueError("Ungültige Absender-E-Mail-Adresse")

        self.email_config = email_config
        self.measurement_config = measurement_config
        self.logger = logger or logging.getLogger(__name__)
        
        # Alert-State-Management
        self.last_alert_time: Optional[datetime] = None
        self.alerts_sent_count: int = 0
        self.cooldown_minutes: int = max(1, getattr(email_config, 'cooldown_minutes', 5))  # Minimum 1 Minute zwischen E-Mails

        self._state_lock = threading.RLock()
        self._smtp_lock = threading.Lock()
        
        # SMTP-Verbindung Cache
        self._smtp_connection: Optional[smtplib.SMTP] = None
        self._connection_timeout: int = 30  # Sekunden
        
        self.logger.info("AlertSystem initialisiert")
    
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
        current_time = datetime.now()

        with self._state_lock:
            if not self._should_send_alert_unsafe():
                return False
            
            previous_alert_time = self.last_alert_time
            previous_count = self.alerts_sent_count
            
            self.last_alert_time = current_time
            temp_count = self.alerts_sent_count + 1

            # E-Mail-Template rendern
            try:
                template = self.email_config.alert_template()
                timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")

                template_params = {
                    'timestamp': timestamp,
                    'session_id': session_id or "Unbekannt",
                    'last_motion_time': last_motion_time.strftime("%H:%M:%S") if last_motion_time else "Unbekannt",
                    'website_url': self.email_cfg.website_url or "Unbekannt",
                    'camera_index': self.webcam_cfg.camera_index if self.webcam_cfg else "Unbekannt",
                    'sensitivity': self.motion_cfg.sensitivity if self.motion_cfg else "Unbekannt",
                    'roi_enabled': self.motion_cfg.get_roi().enabled if self.motion_cfg else "Unbekannt"
                }
                
                subject = template.subject.format(**template_params)

                body = template.body.format(**template_params)
                
            except (KeyError, ValueError, AttributeError) as e:
                self.logger.error(f"Fehler beim Rendern des E-Mail-Templates: {e}, verwende Fallback-Template")
                timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
                subject = f"Bewegungslosigkeit erkannt - {timestamp}"
                body = (
                    f"Bewegung wird seit {timestamp} nicht erkannt!\n"
                    f"Bitte überprüfen Sie die Website unter: {self.email_config.website_url}\n\n"
                    f"Details:\n"
                    f"Session-ID: {session_id or 'Unbekannt'}\n"
                    f"Kamera: Index aktuell nicht verfügbar\n"
                    f"Sensitivität: aktuell nicht verfügbar\n"
                    f"ROI aktiv: aktuell nicht verfügbar\n"
                    f"Letzte Bewegung um {last_motion_time.strftime('%H:%M:%S') if last_motion_time else 'Unbekannt'}.\n"
                    f"Im Anhang finden Sie das aktuelle Webcam-Bild."
                )
            
            # E-Mail-Nachrichten für alle Empfänger erstellen
            try:
                messages = []
                for recipient in self.email_config.recipients:
                    msg = self._create_email_message(subject, body, recipient)
                    
                    # Bild-Anhang hinzufügen wenn verfügbar
                    if camera_frame is not None:
                        camframe = camera_frame.copy()
                        self._attach_camera_image(msg, camframe, timestamp)

                    messages.append((recipient, msg))
                
                # E-Mails versenden
                success_count = self._send_emails_batch(messages)
                
                # Erfolg wenn mindestens eine E-Mail gesendet wurde
                if success_count > 0:
                    with self._state_lock:
                        self.alerts_sent_count = temp_count
                    self.logger.info(f"Alert #{temp_count} gesendet ({success_count}/{len(messages)} erfolgreich)")
                    return True
                else:
                    # Rollback bei Fehlschlag
                    with self._state_lock:
                        self.last_alert_time = previous_alert_time
                        self.alerts_sent_count = previous_count
                        self.logger.error("Alle E-Mail-Versendungen fehlgeschlagen, Zustand zurückgesetzt")
                    return False
                
            except Exception as exc:
                with self._state_lock:
                        self.last_alert_time = previous_alert_time
                        self.alerts_sent_count = previous_count
                self.logger.error(f"kritischer Fehler beim Alert-Versand: {exc}; Zustand zurückgesetzt")
                return False

    def _send_emails_batch(self, messages: List[tuple], max_retries: int = 3) -> int:
        """Optimierter Batch-E-Mail-Versand"""
        success_count = 0
        
        # Einmalige SMTP-Verbindung für alle E-Mails
        for attempt in range(max_retries):
            try:
                with self._smtp_lock:
                    
                        with smtplib.SMTP(
                            self.email_config.smtp_server, 
                            self.email_config.smtp_port, 
                            timeout=self._connection_timeout
                        ) as smtp:
                            
                            for recipient, message in messages:
                                try:
                                    smtp.sendmail(self.email_config.sender_email, recipient, message.as_string())
                                    success_count += 1
                                    self.logger.info(f"E-Mail erfolgreich gesendet an {recipient}")
                                except smtplib.SMTPException as exc:
                                    self.logger.error(f"SMTP-Fehler beim Senden an {recipient}: {exc}")
                                except Exception as exc:
                                    self.logger.error(f"Allgemeiner Fehler beim Senden an {recipient}: {exc}")
                        
                        break # Erfolgreich gesendet, Schleife verlassen

            except (smtplib.SMTPException, ConnectionError, OSError) as exc:
                if attempt == max_retries - 1:
                    self.logger.error(f"SMTP-Verbindung nach {max_retries} Versuchen fehlgeschlagen: {exc}")
                else:
                    self.logger.warning(f"SMTP-Verbindungsversuch {attempt + 1} fehlgeschlagen, retry in {2 ** attempt}s: {exc}")
                    time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
            except Exception as exc:
                self.logger.error(f"Kritischer SMTP-Fehler (kein Retry): {exc}")
                break
        
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
            self.logger.debug(f"Alert-Cooldown aktiv, verbleibend: {remaining:.0f}s")
        
        return cooldown_reached
    
    def _should_send_alert(self) -> bool:
        with self._state_lock:
            return self._should_send_alert_unsafe()

    def _create_email_message(self, subject: str, body: str, recipient: str) -> MIMEMultipart:
        """
        Erstellt MIME-Multipart-E-Mail-Nachricht.
        
        Args:
            subject: E-Mail-Betreff
            body: E-Mail-Text
            recipient: Empfänger-Adresse
            
        Returns:
            MIME-Multipart-Nachricht
        """
        msg = MIMEMultipart()
        msg['From'] = self.email_config.sender_email
        msg['To'] = recipient
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        
        # Text-Inhalt hinzufügen
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        return msg
    
    def _attach_camera_image(self, msg: MIMEMultipart, frame: np.ndarray, timestamp: str) -> None:
        """
        Fügt Kamera-Bild als E-Mail-Anhang hinzu.
        
        Args:
            msg: MIME-Nachricht
            frame: OpenCV-Frame (BGR)
            timestamp: Zeitstempel für Dateinamen
        """
        try:
            if frame is None or frame.size == 0:
                self.logger.warning("Kein gültiges Kamera-Bild für Anhang")
                return
            
            image_format = 'jpg'
            image_quality = 85

            # Bild-Format und Qualität aus Config
            
            if self.measurement_config:
                image_format = self.measurement_config.image_format.lower()
                image_quality = self.measurement_config.image_quality
            
            # OpenCV-Frame zu JPEG konvertieren
            encode_params = []
            if image_format == 'jpg':
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, image_quality]
            elif image_format == 'png':
                encode_params = [cv2.IMWRITE_PNG_COMPRESSION, 3]
            
            success, buffer = cv2.imencode(f'.{image_format}', frame, encode_params)
            
            if success:
                # MIME-Image-Attachment erstellen
                safe_timestamp = re.sub(r'[^\w\s-]', '', timestamp)  # Nur sichere Zeichen für Dateinamen
                safe_timestamp = safe_timestamp[:50]  # Länge begrenzen
                filename = f"alert_{safe_timestamp}.{image_format}"

                img_attachment = MIMEImage(buffer.tobytes())
                img_attachment.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                msg.attach(img_attachment)
                
                self.logger.debug(f"Bild-Anhang hinzugefügt: {filename} ({len(buffer)} bytes)")
                
                # Optional: Bild auch lokal speichern
                if self.measurement_config and self.measurement_config.save_alert_images:
                    self._save_alert_image(buffer, filename)
            else:
                self.logger.warning("Bild-Encoding fehlgeschlagen")
                
        except Exception as exc:
            self.logger.error(f"Fehler beim Bild-Anhang: {exc}")
    
    def _save_alert_image(self, image_buffer: np.ndarray, filename: str) -> None:
        """
        Speichert Alert-Bild lokal.
        
        Args:
            image_buffer: Bild-Daten
            filename: Dateiname
        """
        try:
            if self.measurement_config:
                save_path = Path(self.measurement_config.image_save_path)
                save_path.mkdir(parents=True, exist_ok=True)
                
                file_path = save_path / filename
                with open(file_path, 'wb') as f:
                    f.write(image_buffer.tobytes())
                
                self._cleanup_image(save_path)
                self.logger.info(f"Alert-Bild gespeichert: {file_path}")
        except Exception as exc:
            self.logger.error(f"Fehler beim Speichern des Alert-Bilds: {exc}")

    def _cleanup_image(self, save_path: Path, max_files: int = 100) -> None:
        """
        Bereinigt alte Alert-Bilder im Speicherpfad.
        
        Args:
            save_path: Pfad zum Speicherort der Bilder
        """
        try:
            image_files = list(save_path.glob("alert_*.jpg")) + list(save_path.glob("alert_*.png"))
            if len(image_files) > max_files:
                # Sortiere nach Änderungszeit, lösche älteste
                image_files.sort(key=lambda x: x.stat().st_mtime)
                for old_file in image_files[:-max_files]:
                    old_file.unlink()
                    self.logger.debug(f"Altes Alert-Bild gelöscht: {old_file}")
        except Exception as exc:
            self.logger.error(f"Fehler beim Aufräumen alter Bilder: {exc}")
    
    # === Status-Export für GUI ===
    
    def get_alert_status(self) -> Dict[str, Any]:
        """
        Exportiert Alert-Status für GUI.
        
        Returns:
            Dict mit Alert-Informationen
        """
        with self._state_lock:
            return {
                'last_alert_time': self.last_alert_time,
                'alerts_sent_count': self.alerts_sent_count,
                'cooldown_remaining': self._get_cooldown_remaining_unsafe(),
                'can_send_alert': self._should_send_alert_unsafe(),
                'configured_recipients': len(self.email_config.recipients),
                'smtp_server': self.email_config.smtp_server
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
        with self._smtp_lock:
            try:
                with smtplib.SMTP(self.email_config.smtp_server, self.email_config.smtp_port, timeout=self._connection_timeout) as smtp:
                    smtp.noop()  # Einfacher Test-Befehl
                    self.logger.info("SMTP-Verbindungstest erfolgreich")
                    return True
            except Exception as exc:
                self.logger.error(f"SMTP-Verbindungstest fehlgeschlagen: {exc}")
                return False
    
    def send_test_email(self, test_message: str = "Test-E-Mail vom Webcam-Überwachungssystem") -> bool:
        """
        Sendet Test-E-Mail an alle konfigurierten Empfänger.
        
        Args:
            test_message: Test-Nachricht
            
        Returns:
            True wenn mindestens eine E-Mail erfolgreich gesendet
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            subject = f"Test-E-Mail - {timestamp}"
            
            messages = []
            for recipient in self.email_config.recipients:
                msg = self._create_email_message(subject, test_message, recipient)
                messages.append((recipient, msg))

            success_count = self._send_emails_batch(messages)
            return success_count > 0
            
        except Exception as exc:
            self.logger.error(f"Fehler beim Test-E-Mail-Versand: {exc}")
            return False
    
    def health_check(self) -> Dict[str, Any]:
        """Umfassender Gesundheitscheck des AlertSystems"""
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
                'message': 'SMTP-Verbindung erfolgreich' if smtp_ok else 'SMTP-Verbindung fehlgeschlagen'
            }
            
            # Konfigurationsvalidierung
            config_ok = True
            config_message = "Konfiguration gültig"
            try:
                if not self.email_config.recipients:
                    config_ok = False
                    config_message = "Keine E-Mail-Empfänger konfiguriert"
            except Exception as exc:
                config_ok = False
                config_message = f"Konfigurationsfehler: {exc}"
            
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
            self.logger.error(f"Health-Check fehlgeschlagen: {exc}")
        
        return health
    
    def get_metrics(self) -> Dict[str, Any]:
        """Exportiert Metriken für Monitoring"""
        with self._state_lock:
            return {
                'alerts_sent_total': self.alerts_sent_count,
                'last_alert_timestamp': self.last_alert_time.timestamp() if self.last_alert_time else None,
                'cooldown_remaining_seconds': self._get_cooldown_remaining_unsafe(),
                'recipients_configured': len(self.email_config.recipients),
                'cooldown_minutes_configured': self.cooldown_minutes
            }


# === Factory-Funktionen ===

def create_alert_system_from_config(
    config_path: Optional[str] = None
) -> AlertSystem:
    """
    Erstellt AlertSystem aus Konfiguration.
    
    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        
    Returns:
        Konfiguriertes AlertSystem
    """
    from .config import load_config
    
    path = config_path if config_path is not None else "config/config.yaml"
    config = load_config(path)
    
    logger = logging.getLogger("alert")
    
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
            print("✅ Template-Rendering erfolgreich")
            print(f"Subject: {subject}")
            print(f"Body: {body}")
            return True
        except KeyError as e:
            print(f"❌ Template-Parameter fehlt: {e}")
            return False
    except Exception as e:
        print(f"❌ Template-Fehler: {e}")
        return False

# Testen
if __name__ == "__main__":
    test_template_rendering()