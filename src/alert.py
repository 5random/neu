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
import cv2
import numpy as np
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import EmailConfig, MeasurementConfig


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
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert das AlertSystem.
        
        Args:
            email_config: E-Mail-Konfiguration mit SMTP-Einstellungen
            measurement_config: Optional für Bild-Speicherung
            logger: Optional Logger für Alert-Tracking
        """
        self.email_config = email_config
        self.measurement_config = measurement_config
        self.logger = logger or logging.getLogger(__name__)
        
        # Alert-State-Management
        self.last_alert_time: Optional[datetime] = None
        self.alerts_sent_count: int = 0
        self.cooldown_minutes: int = 5  # Minimum 5 Minuten zwischen E-Mails
        
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
        if not self._should_send_alert():
            return False
        
        try:
            # E-Mail-Template rendern
            template = self.email_config.alert_template()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            subject = template.subject.format(
                timestamp=timestamp,
                session_id=session_id or "Unbekannt"
            )
            
            body = template.body.format(
                timestamp=timestamp,
                session_id=session_id or "Unbekannt",
                last_motion_time=last_motion_time.strftime("%H:%M:%S") if last_motion_time else "Unbekannt"
            )
            
            # E-Mail-Nachrichten für alle Empfänger erstellen
            messages = []
            for recipient in self.email_config.recipients:
                msg = self._create_email_message(subject, body, recipient)
                
                # Bild-Anhang hinzufügen wenn verfügbar
                if camera_frame is not None:
                    self._attach_camera_image(msg, camera_frame, timestamp)
                
                messages.append((recipient, msg))
            
            # E-Mails versenden
            success_count = 0
            for recipient, message in messages:
                if self._send_email(recipient, message):
                    success_count += 1
                    self.logger.info(f"E-Mail erfolgreich gesendet an {recipient}")
                else:
                    self.logger.error(f"E-Mail-Versendung fehlgeschlagen an {recipient}")
            
            # Erfolg wenn mindestens eine E-Mail gesendet wurde
            if success_count > 0:
                self.last_alert_time = datetime.now()
                self.alerts_sent_count += 1
                self.logger.info(f"Alert #{self.alerts_sent_count} gesendet ({success_count}/{len(messages)} erfolgreich)")
                return True
            else:
                self.logger.error("Alle E-Mail-Versendungen fehlgeschlagen")
                return False
                
        except Exception as exc:
            self.logger.error(f"Fehler beim Alert-Versand: {exc}")
            return False
    
    def _should_send_alert(self) -> bool:
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
            # Bild-Format und Qualität aus Config
            image_format = 'jpg'
            image_quality = 85
            
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
                filename = f"alert_{timestamp.replace(':', '-').replace(' ', '_')}.{image_format}"
                
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
                
                self.logger.info(f"Alert-Bild gespeichert: {file_path}")
        except Exception as exc:
            self.logger.error(f"Fehler beim Speichern des Alert-Bilds: {exc}")
    
    def _send_email(self, recipient: str, message: MIMEMultipart) -> bool:
        """
        Sendet E-Mail über SMTP.
        
        Args:
            recipient: Empfänger-Adresse
            message: MIME-Nachricht
            
        Returns:
            True wenn erfolgreich gesendet
        """
        try:
            # SMTP-Verbindung aufbauen
            with smtplib.SMTP(self.email_config.smtp_server, self.email_config.smtp_port, timeout=self._connection_timeout) as smtp:
                # Einfache SMTP-Verbindung ohne SSL/TLS (wie in Projektbeschreibung)
                smtp.sendmail(self.email_config.sender_email, recipient, message.as_string())
                return True
                
        except smtplib.SMTPException as exc:
            self.logger.error(f"SMTP-Fehler beim Senden an {recipient}: {exc}")
            return False
        except Exception as exc:
            self.logger.error(f"Allgemeiner Fehler beim Senden an {recipient}: {exc}")
            return False
    
    # === Status-Export für GUI ===
    
    def get_alert_status(self) -> Dict[str, Any]:
        """
        Exportiert Alert-Status für GUI.
        
        Returns:
            Dict mit Alert-Informationen
        """
        return {
            'last_alert_time': self.last_alert_time,
            'alerts_sent_count': self.alerts_sent_count,
            'cooldown_remaining': self._get_cooldown_remaining(),
            'can_send_alert': self._should_send_alert(),
            'configured_recipients': len(self.email_config.recipients),
            'smtp_server': self.email_config.smtp_server
        }
    
    def _get_cooldown_remaining(self) -> Optional[float]:
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
    
    def test_connection(self) -> bool:
        """
        Testet SMTP-Verbindung ohne E-Mail zu senden.
        
        Returns:
            True wenn Verbindung erfolgreich
        """
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
            
            success_count = 0
            for recipient in self.email_config.recipients:
                msg = self._create_email_message(subject, test_message, recipient)
                
                if self._send_email(recipient, msg):
                    success_count += 1
                    self.logger.info(f"Test-E-Mail erfolgreich gesendet an {recipient}")
                else:
                    self.logger.error(f"Test-E-Mail-Versendung fehlgeschlagen an {recipient}")
            
            return success_count > 0
            
        except Exception as exc:
            self.logger.error(f"Fehler beim Test-E-Mail-Versand: {exc}")
            return False


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
    
    return AlertSystem(config.email, config.measurement, logger)