"""
E-Mail-System für Webcam-Überwachung mit E-Mail-Benachrichtigung.

Dieses Modul implementiert die E-Mail-Benachrichtigung bei anhaltender 
Bewegungslosigkeit gemäß Projektbeschreibung:
- Einfache SMTP-Integration ohne Sicherheitsfeatures
- E-Mail-Versand an mehrere Empfänger
- Webcam-Bild als Anhang
- Template-System für dynamische Inhalte

Fokus auf einfache, robuste Implementation.
"""

from __future__ import annotations
from collections.abc import Iterable
import html
import smtplib
import logging
import time
import re
import cv2
import numpy as np
import requests
import math
from nicegui import app
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import formatdate, make_msgid
from typing import Optional, List, Dict, Any, TYPE_CHECKING, Callable

from .config import EmailConfig, MeasurementConfig, AppConfig, get_logger

_RUNTIME_WEBSITE_URL_KEY = 'cvd.runtime_website_url'
_URL_PATTERN = re.compile(r"(https?://[^\s<>'\"]+)")


def _iterable_str_list(value: object) -> List[str]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [item for item in value if isinstance(item, str)]
    return []


class AlertSendAborted(RuntimeError):
    """Raised when an in-flight alert send must be cancelled."""


class EMailSystem:
    """
    Einfaches E-Mail-System für Webcam-Überwachung.
    
    Versendet E-Mails bei anhaltender Bewegungslosigkeit mit:
    - SMTP-Integration ohne SSL/TLS (wie in Projektbeschreibung)
    - Multi-Empfänger-Support
    - Webcam-Bild als Anhang
    - Template-basierte Nachrichten
    - Anti-Spam-Mechanismus
    
    Usage:
        email_system = EMailSystem(email_config, measurement_config)
        success = await email_system.send_motion_alert_async(last_motion_time, session_id)
    """
    
    def __init__(
        self,
        email_config: 'EmailConfig',
        measurement_config: 'MeasurementConfig',
        app_cfg: 'AppConfig',
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialisiert das E-Mail-System.
        """

        self.logger = logger or get_logger('email')

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
        
        # Ensure at least one effective recipient is configured (recipients or active groups)
        try:
            effective = email_config.get_target_recipients() if hasattr(email_config, 'get_target_recipients') else email_config.recipients
        except Exception:
            effective = email_config.recipients
        if not effective:
            raise ValueError("At least one recipient must be configured (static recipients or active groups)")

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
        self.alert_cooldown_seconds: int = 0
        self.cooldown_minutes: int = 0
        self._alert_session_id: Optional[str] = None

        self._state_lock = threading.RLock()
        self._smtp_lock = threading.Lock()
        self._smtp_connection: Optional[smtplib.SMTP] = None
        self._connection_timeout: int = 30
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._alert_system_cleanup = False
        self._refresh_alert_runtime_settings_unsafe()

        self.logger.info("EMailSystem initialized")

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
        if hasattr(self, '_smtp_lock'):
            self.close()

    def _refresh_alert_runtime_settings_unsafe(self) -> None:
        cooldown_seconds = max(0, int(getattr(self.measurement_config, 'alert_cooldown_seconds', 0)))
        self.alert_cooldown_seconds = cooldown_seconds
        self.cooldown_minutes = math.ceil(cooldown_seconds / 60) if cooldown_seconds > 0 else 0

    def _matches_alert_session_unsafe(self, session_id: Optional[str]) -> bool:
        if session_id is None:
            return True
        return self._alert_session_id is not None and self._alert_session_id == session_id

    def reset_alert_state(self, session_id: Optional[str] = None) -> None:
        """Reset per-session alert counters and cooldown tracking."""
        with self._state_lock:
            self.last_alert_time = None
            self.alerts_sent_count = 0
            self._alert_session_id = session_id
            self.logger.debug("Alert state reset for session %s", session_id or "<none>")

    def decrement_alert_count(self, session_id: Optional[str] = None, *, amount: int = 1) -> bool:
        """Decrease the per-session alert counter without touching cooldown state.

        Non-positive decrement requests are treated as no-ops and return ``False``.
        """
        decrement_by = max(0, int(amount)) if amount is not None else 1
        with self._state_lock:
            if not self._matches_alert_session_unsafe(session_id):
                return False
            if self._alert_session_id is None or self.alerts_sent_count <= 0 or decrement_by <= 0:
                return False
            previous_count = self.alerts_sent_count
            self.alerts_sent_count = max(0, self.alerts_sent_count - decrement_by)
            self.logger.debug(
                "Alert count decremented for session %s: %s -> %s",
                session_id or "<none>",
                previous_count,
                self.alerts_sent_count,
            )
            return True

    def reset_alert_count(self, session_id: Optional[str] = None) -> bool:
        """Reset the per-session alert counter without touching cooldown state."""
        with self._state_lock:
            if not self._matches_alert_session_unsafe(session_id):
                return False
            if self._alert_session_id is None:
                return False
            previous_count = self.alerts_sent_count
            self.alerts_sent_count = 0
            self.logger.debug(
                "Alert count reset for session %s: %s -> 0",
                session_id or "<none>",
                previous_count,
            )
            return True

    def can_send_alert(self, session_id: Optional[str] = None) -> bool:
        with self._state_lock:
            if not self._matches_alert_session_unsafe(session_id):
                return False
            return self._should_send_alert_unsafe()

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
            self._refresh_alert_runtime_settings_unsafe()

            self.logger.info("Alert-Configuration refreshed")
    
    def _get_current_email_config(self) -> 'EmailConfig':
        """
        Gibt die aktuelle E-Mail-Konfiguration zurück.
        
        Returns:
            Aktuelle EmailConfig-Instanz
        """
        with self._state_lock:
            return self.app_cfg.email

    def _resolve_website_url(self) -> str:
        """Resolve the best available app URL for email templates."""
        current_email_config = self._get_current_email_config()
        configured_value = getattr(current_email_config, 'website_url', '')
        configured_url = configured_value.strip() if isinstance(configured_value, str) else ''
        website_url_source = str(
            getattr(
                current_email_config,
                'website_url_source',
                getattr(current_email_config, 'WEBSITE_URL_SOURCE_RUNTIME_PERSIST', 'runtime_persist'),
            )
            or getattr(current_email_config, 'WEBSITE_URL_SOURCE_RUNTIME_PERSIST', 'runtime_persist')
        ).strip().lower()
        expected_config_source = str(
            getattr(current_email_config, 'WEBSITE_URL_SOURCE_CONFIG', 'config')
        ).strip().lower()
        try:
            runtime_url = str(app.storage.general.get(_RUNTIME_WEBSITE_URL_KEY, '') or '').strip()
        except Exception:
            runtime_url = ''
        if website_url_source == expected_config_source:
            return configured_url or runtime_url
        return runtime_url or configured_url

    def _get_effective_recipients(self) -> List[str]:
        """Return effective recipients using current email config.

        Prefers EmailConfig.get_target_recipients() when available, otherwise
        falls back to the base recipients list. Always returns a list and never raises.
        """
        try:
            current_email_config = self._get_current_email_config()
            if hasattr(current_email_config, 'get_target_recipients'):
                return _iterable_str_list(current_email_config.get_target_recipients())
            return _iterable_str_list(current_email_config.recipients)
        except Exception:
            try:
                return _iterable_str_list(self.email_config.recipients)
            except Exception:
                return []

    def _get_measurement_event_recipients(self, event_key: str) -> List[str]:
        """Return resolved lifecycle recipients for a specific measurement event."""
        try:
            current_email_config = self._get_current_email_config()
            resolver = getattr(current_email_config, 'get_measurement_event_recipients', None)
            if callable(resolver):
                return _iterable_str_list(resolver(event_key))
            if not bool(getattr(current_email_config, 'notifications', {}).get(event_key, False)):
                return []
            return self._get_effective_recipients()
        except Exception:
            self.logger.debug("Falling back to effective recipients for event %s", event_key, exc_info=True)
            return self._get_effective_recipients()
    
    def _build_common_template_params(
        self,
        session_id: Optional[str] = None,
        last_motion_time: Optional[datetime] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        duration: Optional[str] = None,
        reason: Optional[str] = None,
        snapshot_note: str = "",
    ) -> Dict[str, Any]:
        """
        Baut ein vollständiges Parameter-Set für alle E-Mail-Templates.
        Stellt sicher, dass alle erwarteten Platzhalter vorhanden sind.
        """
        now = datetime.now()
        current_email_config = self._get_current_email_config()

        # Metadata sicher ermitteln
        meta = getattr(self.app_cfg, "metadata", None)
        cvd_id = getattr(meta, "cvd_id", 0) if meta is not None else 0
        cvd_name = getattr(meta, "cvd_name", "Unknown")

        # Kamera-/Motion-Infos robust ermitteln
        camera_index = getattr(self.webcam_cfg, "camera_index", "unknown") if self.webcam_cfg else "unknown"
        sensitivity = getattr(self.motion_cfg, "sensitivity", "unknown") if self.motion_cfg else "unknown"
        try:
            roi_enabled = self.motion_cfg.get_roi().enabled if self.motion_cfg else "unknown"
        except Exception:
            roi_enabled = "unknown"

        # Zeiten formatieren
        ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
        last_motion_str = last_motion_time.strftime("%H:%M:%S") if last_motion_time else "unknown"
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time else ""
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S") if end_time else ""

        # Dauer ggf. aus Zeiten ableiten, falls nicht vorgegeben
        if duration is None:
            if start_time and end_time and end_time >= start_time:
                delta: timedelta = end_time - start_time
                secs = int(delta.total_seconds())
                h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
                duration = f"{h:02}:{m:02}:{s:02}"
            else:
                duration = ""

        return {
            "timestamp": ts_str,
            "session_id": session_id or "unknown",
            "last_motion_time": last_motion_str,
            "website_url": self._resolve_website_url(),
            "camera_index": camera_index,
            "sensitivity": sensitivity,
            "roi_enabled": roi_enabled,
            "start_time": start_str,
            "end_time": end_str,
            "duration": duration or "",
            "reason": reason or "",
            "cvd_id": cvd_id,
            "cvd_name": cvd_name,
            "snapshot_note": snapshot_note,
        }

    @staticmethod
    def _alert_snapshot_notice(*, inline_image: bool) -> str:
        if inline_image:
            return "The HTML version of this email contains the current webcam image inline."
        return "Attached is the current webcam image."

    @staticmethod
    def _looks_like_attachment_hint(line: str) -> bool:
        stripped = (line or "").strip()
        if not stripped:
            return False

        lowered = stripped.casefold()
        attachment_markers = (
            "attach",
            "attached",
            "attachment",
            "attaching",
            "anhang",
            "angehängt",
            "angehaengt",
            "beigefügt",
            "beigefuegt",
        )
        return any(marker in lowered for marker in attachment_markers)

    def _finalize_alert_body(self, body: str, *, keep_attachment_hints: bool) -> str:
        text = body or ""
        if keep_attachment_hints:
            return text.rstrip()

        lines = [
            line
            for line in text.splitlines()
            if not self._looks_like_attachment_hint(line)
        ]
        text = "\n".join(lines)
        text = re.sub(r"(?:\r?\n){3,}", "\n\n", text)
        return text.rstrip()

    @staticmethod
    def _split_trailing_url_punctuation(url: str) -> tuple[str, str]:
        trimmed_url = url or ""
        trailing = ""

        while trimmed_url and trimmed_url[-1] in ".,;:!?":
            trailing = trimmed_url[-1] + trailing
            trimmed_url = trimmed_url[:-1]

        unmatched_closers = {
            ")": "(",
            "]": "[",
            "}": "{",
        }
        while trimmed_url and trimmed_url[-1] in unmatched_closers:
            closing = trimmed_url[-1]
            opening = unmatched_closers[closing]
            if trimmed_url.count(closing) <= trimmed_url.count(opening):
                break
            trailing = closing + trailing
            trimmed_url = trimmed_url[:-1]

        return trimmed_url, trailing

    @staticmethod
    def _linkify_plain_text(text: str) -> str:
        if not text:
            return ""

        parts: list[str] = []
        last_idx = 0
        for match in _URL_PATTERN.finditer(text):
            start, end = match.span()
            if start > last_idx:
                parts.append(html.escape(text[last_idx:start]))
            url = match.group(0)
            normalized_url, trailing = EMailSystem._split_trailing_url_punctuation(url)
            safe_href = html.escape(normalized_url, quote=True)
            safe_label = html.escape(normalized_url)
            parts.append(f'<a href="{safe_href}">{safe_label}</a>')
            if trailing:
                parts.append(html.escape(trailing))
            last_idx = end
        if last_idx < len(text):
            parts.append(html.escape(text[last_idx:]))
        return "".join(parts)

    @staticmethod
    def _extract_first_url(text: str) -> Optional[str]:
        if not text:
            return None

        match = _URL_PATTERN.search(text)
        if match is None:
            return None

        normalized_url, _ = EMailSystem._split_trailing_url_punctuation(match.group(0))
        return normalized_url or None

    @staticmethod
    def _format_html_fragment(text: str) -> str:
        rendered = EMailSystem._linkify_plain_text(text or "")
        return rendered.replace("\n", "<br>")

    @staticmethod
    def _split_text_paragraphs(text: str) -> list[str]:
        if not text:
            return []
        return [chunk.strip() for chunk in re.split(r"(?:\r?\n){2,}", text) if chunk.strip()]

    @staticmethod
    def _normalized_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip()).casefold()

    @classmethod
    def _strip_duplicate_intro_heading(cls, paragraphs: list[str], title: str) -> list[str]:
        if not paragraphs:
            return []

        normalized_title = cls._normalized_text(title)
        if not normalized_title:
            return paragraphs

        updated = list(paragraphs)
        lines = updated[0].splitlines()
        heading_removed = False

        for line in lines:
            if not heading_removed and line.strip():
                if cls._normalized_text(line) == normalized_title:
                    heading_removed = True
                    continue
                break

        if not heading_removed:
            return updated

        remaining_lines = []
        skipped_heading = False
        for line in lines:
            if not skipped_heading and line.strip() and cls._normalized_text(line) == normalized_title:
                skipped_heading = True
                continue
            remaining_lines.append(line)

        first_paragraph = "\n".join(remaining_lines).strip()
        if first_paragraph:
            updated[0] = first_paragraph
            return updated
        return updated[1:]

    @classmethod
    def _extract_structured_email_sections(
        cls,
        body: str,
        *,
        title: str,
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        paragraphs = cls._split_text_paragraphs(body or "")
        if not paragraphs:
            return [], [], []

        details_index: Optional[int] = None
        details_lines: list[str] = []

        for index, paragraph in enumerate(paragraphs):
            lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
            if not lines:
                continue
            first_line = lines[0].rstrip(":").casefold()
            if first_line == "details":
                details_index = index
                details_lines = lines[1:]
                break

        if details_index is None:
            return cls._strip_duplicate_intro_heading(paragraphs, title), [], []

        intro_paragraphs = cls._strip_duplicate_intro_heading(paragraphs[:details_index], title)
        trailing_paragraphs = paragraphs[details_index + 1 :]

        detail_source_parts: list[str] = []
        if details_lines:
            detail_source_parts.append("\n".join(details_lines))

        footer_paragraphs: list[str] = []
        if trailing_paragraphs:
            detail_source_parts.append(trailing_paragraphs[0])
            footer_paragraphs = trailing_paragraphs[1:]

        detail_rows: list[tuple[str, str]] = []
        detail_block = "\n".join(part for part in detail_source_parts if part.strip())
        for line in detail_block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if ":" in stripped:
                label, value = stripped.split(":", 1)
                detail_rows.append((label.strip(), value.strip()))
            else:
                detail_rows.append(("", stripped))

        return intro_paragraphs, detail_rows, footer_paragraphs

    @staticmethod
    def _build_html_email_title(
        body: str,
        *,
        template_name: Optional[str] = None,
        subject: Optional[str] = None,
        template_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        params = template_params or {}
        cvd_id = str(params.get("cvd_id", "") or "").strip()
        cvd_name = str(params.get("cvd_name", "") or "").strip()
        cvd_label = f"CVD-Tracker{cvd_id}-{cvd_name}" if cvd_id or cvd_name else "CVD-Tracker"

        known_titles = {
            "alert": f"{cvd_label} Alert",
            "measurement_start": f"{cvd_label} Measurement Started",
            "measurement_end": f"{cvd_label} Measurement Ended",
            "measurement_stop": f"{cvd_label} Measurement Stopped",
            "test": subject or f"{cvd_label} Test Email",
        }
        if template_name in known_titles:
            return known_titles[template_name]

        if subject:
            return subject

        for line in (body or "").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return "CVD-Tracker Notification"

    @staticmethod
    def _html_button(url: str, label: str) -> str:
        safe_url = html.escape((url or "").strip(), quote=True)
        if not safe_url:
            return ""

        safe_label = html.escape(label, quote=True)
        return (
            '<div style="margin:20px 0 24px 0; text-align:center;">'
            f'<a href="{safe_url}" '
            'style="display:inline-block; padding:14px 24px; background:#2563eb; color:#ffffff; '
            'text-decoration:none; font-weight:700; font-size:15px; border-radius:10px; '
            'border:1px solid #1d4ed8;">'
            f"{safe_label}"
            "</a>"
            "</div>"
        )

    @staticmethod
    def _html_image_block(cid: str, alt_text: str) -> str:
        safe_cid = html.escape(cid, quote=True)
        safe_alt = html.escape(alt_text, quote=True)
        return (
            '<div style="margin:0 0 24px 0; text-align:center;">'
            f'<img src="cid:{safe_cid}" alt="{safe_alt}" '
            'style="max-width:100%; height:auto; border:1px solid #e5e7eb; '
            'border-radius:10px; display:block; margin:0 auto;">'
            "</div>"
        )

    def _html_table(self, rows: list[tuple[str, str]]) -> str:
        if not rows:
            return ""

        html_rows: list[str] = []
        for label, value in rows:
            rendered_value = self._format_html_fragment(value)
            if label:
                html_rows.append(
                    "<tr>"
                    f'<td style="padding:8px 12px; border-bottom:1px solid #e5e7eb; '
                    'font-weight:600; width:220px; vertical-align:top;">'
                    f"{html.escape(label)}</td>"
                    f'<td style="padding:8px 12px; border-bottom:1px solid #e5e7eb; vertical-align:top;">'
                    f"{rendered_value}</td>"
                    "</tr>"
                )
            else:
                html_rows.append(
                    "<tr>"
                    f'<td colspan="2" style="padding:8px 12px; border-bottom:1px solid #e5e7eb; vertical-align:top;">'
                    f"{rendered_value}</td>"
                    "</tr>"
                )

        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%; border-collapse:collapse; background:#ffffff; '
            'border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;">'
            f"{''.join(html_rows)}"
            "</table>"
        )

    def _html_note_block(self, paragraphs: list[str], *, heading: str) -> str:
        if not paragraphs:
            return ""

        rendered_paragraphs = "".join(
            f'<p style="margin:0 0 12px 0; line-height:1.6;">{self._format_html_fragment(paragraph)}</p>'
            for paragraph in paragraphs
        )
        safe_heading = html.escape(heading, quote=True)
        return (
            '<div style="margin-top:20px; padding:16px; background:#fff7ed; '
            'border:1px solid #fdba74; border-radius:8px;">'
            f'<div style="font-weight:700; margin-bottom:8px;">{safe_heading}</div>'
            f"{rendered_paragraphs}"
            "</div>"
        )

    @staticmethod
    def _html_wrapper(
        title: str,
        intro_html: str,
        button_html: str,
        details_html: str,
        image_html: str = "",
        footer_html: str = "",
    ) -> str:
        details_section = (
            f'<div style="margin-bottom:20px;">{details_html}</div>'
            if details_html
            else ""
        )

        return (
            "<!DOCTYPE html>"
            '<html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>{html.escape(title)}</title>"
            "</head>"
            '<body style="margin:0; padding:0; background:#f3f4f6; '
            'font-family:Arial, Helvetica, sans-serif; color:#111827;">'
            '<div style="max-width:760px; margin:0 auto; padding:24px;">'
            '<div style="background:#ffffff; border:1px solid #e5e7eb; '
            'border-radius:12px; overflow:hidden;">'
            '<div style="padding:24px 24px 12px 24px; background:#111827; color:#ffffff;">'
            f'<h1 style="margin:0; font-size:22px; line-height:1.3;">{html.escape(title)}</h1>'
            "</div>"
            '<div style="padding:24px;">'
            f'<div style="font-size:15px; line-height:1.6; margin-bottom:8px;">{intro_html}</div>'
            f"{button_html}"
            f"{image_html}"
            f"{details_section}"
            f"{footer_html}"
            "</div></div></div></body></html>"
        )

    def _render_html_email_body(
        self,
        body: str,
        *,
        subject: Optional[str] = None,
        template_name: Optional[str] = None,
        template_params: Optional[Dict[str, Any]] = None,
        inline_image_cid: Optional[str] = None,
        image_alt_text: str = "Embedded image",
    ) -> str:
        title = self._build_html_email_title(
            body,
            template_name=template_name,
            subject=subject,
            template_params=template_params,
        )
        intro_paragraphs, detail_rows, footer_paragraphs = self._extract_structured_email_sections(
            body,
            title=title,
        )

        if not intro_paragraphs and not detail_rows and not footer_paragraphs:
            intro_paragraphs = [body or ""]

        intro_html = "".join(
            f'<p style="margin:0 0 12px 0;">{self._format_html_fragment(paragraph)}</p>'
            for paragraph in intro_paragraphs
        )
        details_html = self._html_table(detail_rows)

        website_url = ""
        if template_params is not None:
            website_url = str(template_params.get("website_url", "") or "").strip()
        if not website_url:
            extracted_url = self._extract_first_url(body)
            website_url = extracted_url or ""
        button_html = self._html_button(website_url, "Open Web Application")

        image_block = (
            self._html_image_block(inline_image_cid, image_alt_text)
            if inline_image_cid
            else ""
        )
        footer_heading = "Snapshot note" if template_name == "alert" else "Additional note"
        footer_html = self._html_note_block(footer_paragraphs, heading=footer_heading)

        return self._html_wrapper(
            title,
            intro_html,
            button_html,
            details_html,
            image_html=image_block,
            footer_html=footer_html,
        )

    @staticmethod
    def _create_image_part(
        image_bytes: bytes,
        *,
        filename: str,
        disposition: str,
        content_id: Optional[str] = None,
    ) -> MIMEImage:
        image_part = MIMEImage(image_bytes)
        image_part.add_header("Content-Disposition", f'{disposition}; filename="{filename}"')
        if content_id:
            image_part.add_header("Content-ID", f"<{content_id}>")
        return image_part

    def _raise_if_alert_send_cancelled(
        self,
        *,
        session_id: Optional[str],
        abort_checker: Optional[Callable[[], bool]] = None,
    ) -> None:
        try:
            if abort_checker is not None and abort_checker():
                raise AlertSendAborted("alert send cancelled by measurement state")
        except AlertSendAborted:
            raise
        except Exception as exc:
            self.logger.warning("Alert abort checker failed: %s", exc)
            raise AlertSendAborted("alert send cancelled because abort checker failed") from exc

        with self._state_lock:
            if self._alert_system_cleanup:
                raise AlertSendAborted("alert send cancelled during cleanup")
            if session_id is not None and not self._matches_alert_session_unsafe(session_id):
                raise AlertSendAborted(f"alert send cancelled for stale or inactive session {session_id}")

    def send_motion_alert(
        self,
        last_motion_time: Optional[datetime] = None,
        session_id: Optional[str] = None,
        camera_frame: Optional[np.ndarray] = None,
        abort_checker: Optional[Callable[[], bool]] = None,
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
            self.logger.error("EMailSystem has been cleaned up, cannot send alert")
            raise RuntimeError("EMailSystem has been cleaned up")
    
        current_time = datetime.now()
        current_email_config = self._get_current_email_config()
        abort_check = lambda: self._raise_if_alert_send_cancelled(
            session_id=session_id,
            abort_checker=abort_checker,
        )

        with self._state_lock:
            if session_id is not None and not self._matches_alert_session_unsafe(session_id):
                self.logger.warning(
                    "Skipping alert for stale or inactive session %s; active session is %s",
                    session_id,
                    self._alert_session_id,
                )
                return False
            if not self._should_send_alert_unsafe():
                return False
            
            previous_alert_time = self.last_alert_time
            previous_count = self.alerts_sent_count
            
            self.last_alert_time = current_time
            temp_count = self.alerts_sent_count + 1

        try:
            abort_check()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            img_buffer: Optional[np.ndarray] = None
            filename: Optional[str] = None
            ok = False
            include_snapshot = bool(self.measurement_config.alert_include_snapshot)
            send_as_html = bool(getattr(current_email_config, 'send_as_html', False))
            should_process_frame = camera_frame is not None and include_snapshot

            if should_process_frame:
                ok, img_buffer, filename = self._encode_frame(camera_frame, ts=timestamp)

            abort_check()
            has_snapshot_attachment = bool(include_snapshot and img_buffer is not None and filename is not None)
            snapshot_note = self._alert_snapshot_notice(inline_image=send_as_html) if has_snapshot_attachment else ""
            attachment_bytes = img_buffer.tobytes() if has_snapshot_attachment and img_buffer is not None else None
            attachment_name = filename if has_snapshot_attachment and filename is not None else None
            template_params: Optional[Dict[str, Any]] = None

            try:
                template = current_email_config.alert_template()
                template_params = self._build_common_template_params(
                    session_id=session_id,
                    last_motion_time=last_motion_time,
                    start_time=None,
                    end_time=None,
                    duration="",
                    reason="",
                    snapshot_note=snapshot_note,
                )

                subject = template.subject.format(**template_params)
                body = self._finalize_alert_body(
                    template.body.format(**template_params),
                    keep_attachment_hints=bool(has_snapshot_attachment and not send_as_html),
                )

            except (KeyError, ValueError, AttributeError) as e:
                self.logger.error(f"Error when rendering the email template: {e}, use fallback template")
                subject = f"CVD-Alert: No motion detected - {timestamp}"
                body_lines = [
                    f"Motion has not been detected since {timestamp}!",
                    f"Please check the website at: {self._resolve_website_url()}",
                    "",
                    "Details:",
                    f"Session-ID: {session_id or 'unknown'}",
                    "Camera: Index currently not available",
                    "Sensitivity: currently not available",
                    "ROI enabled: currently not available",
                    f"Last motion at {last_motion_time.strftime('%H:%M:%S') if last_motion_time else 'unknown'}.",
                ]
                if has_snapshot_attachment:
                    body_lines.extend(["", snapshot_note])
                body = "\n".join(body_lines)

            abort_check()
            recipients = self._get_effective_recipients()
            messages: list[tuple[str, MIMEMultipart]] = []
            shared_inline_cid = (
                make_msgid(domain="cvd-tracker.local")[1:-1]
                if send_as_html and attachment_bytes is not None and attachment_name is not None
                else None
            )
            for recipient in recipients:
                html_body = None
                if send_as_html:
                    html_body = self._render_html_email_body(
                        body,
                        subject=subject,
                        template_name="alert",
                        template_params=template_params,
                        inline_image_cid=shared_inline_cid,
                        image_alt_text="Current webcam image",
                    )
                msg = self._create_email_message(
                    subject,
                    body,
                    recipient,
                    html_body=html_body,
                    inline_image_bytes=attachment_bytes if send_as_html else None,
                    inline_image_filename=attachment_name if send_as_html else None,
                    inline_image_content_id=shared_inline_cid,
                )
                if not send_as_html and attachment_bytes is not None and attachment_name is not None:
                    msg.attach(
                        self._create_image_part(
                            attachment_bytes,
                            filename=attachment_name,
                            disposition="attachment",
                        )
                    )
                messages.append((recipient, msg))
            success_count = self._send_emails_batch(messages, abort_check=abort_check)

            if success_count > 0:
                with self._state_lock:
                    if self._matches_alert_session_unsafe(session_id):
                        self.alerts_sent_count = temp_count
                self.logger.info(
                    f"Alert #{temp_count} sent ({success_count}/{len(recipients)} successful)"
                )
                return True

            with self._state_lock:
                if self._matches_alert_session_unsafe(session_id):
                    self.last_alert_time = previous_alert_time
                    self.alerts_sent_count = previous_count
                self.logger.error("All email sending attempts failed, state reset")
            return False
        except AlertSendAborted as exc:
            with self._state_lock:
                if self._matches_alert_session_unsafe(session_id):
                    self.last_alert_time = previous_alert_time
                    self.alerts_sent_count = previous_count
            self.logger.info("Alert send aborted: %s", exc)
            return False
        except Exception as exc:
            with self._state_lock:
                if self._matches_alert_session_unsafe(session_id):
                    self.last_alert_time = previous_alert_time
                    self.alerts_sent_count = previous_count
            self.logger.error(f"Critical error when sending alert: {exc}; state reset")
            return False

    async def send_motion_alert_async(
        self,
        last_motion_time: Optional[datetime] = None,
        session_id: Optional[str] = None,
        camera_frame: Optional[np.ndarray] = None,
        abort_checker: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """ Async Wrapper für send_motion_alert """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self.send_motion_alert,
            last_motion_time,
            session_id,
            camera_frame,
            abort_checker,
        )

    # ------------------------------------------------------------------
    # Measurement lifecycle notifications
    # ------------------------------------------------------------------
    def send_measurement_event(
        self,
        event: str,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Send start/end/stop measurement notification if enabled.

        Args:
            event: 'start' or 'end'
            session_id: session identifier
            start_time: session start time
            end_time: session end time
            reason: reason for end (manual/timeout/etc.)
        """
        event = (event or '').lower()
        if event not in ('start', 'end', 'stop'):
            self.logger.error(f"Unknown measurement event: {event}")
            return False

        current_email_config = self._get_current_email_config()
        flags = getattr(current_email_config, 'notifications', None)
        if not flags:
            self.logger.debug(
                "notifications disabled or missing for email config (sender=%s, id=%s)",
                getattr(current_email_config, 'sender_email', 'unknown'),
                id(current_email_config),
            )
            return False
        enabled_key = {'start': 'on_start', 'end': 'on_end', 'stop': 'on_stop'}[event]
        enabled = bool(flags.get(enabled_key, False))
        if not enabled:
            self.logger.debug("notification flag %s disabled for event '%s'", enabled_key, event)
            return False

        try:
            # Template passend zum Event wählen
            if event == 'start':
                template = current_email_config.measurement_start_template()
            elif event == 'end':
                template = current_email_config.measurement_end_template()
            else:  # stop
                template = current_email_config.measurement_stop_template()

            # Dauer berechnen (falls möglich)
            duration_str = ""
            if start_time and end_time and end_time >= start_time:
                delta: timedelta = end_time - start_time
                secs = int(delta.total_seconds())
                h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
                duration_str = f"{h:02}:{m:02}:{s:02}"

            # Vollständige Parameter liefern (inkl. cvd_id/cvd_name, Kamera, ROI etc.)
            params = self._build_common_template_params(
                session_id=session_id,
                last_motion_time=None,
                start_time=start_time,
                end_time=end_time,
                duration=duration_str,
                reason=reason,
            )

            subject = template.subject.format(**params)
            body = template.body.format(**params)

            recipients = self._get_measurement_event_recipients(enabled_key)
            if not recipients:
                self.logger.warning("No recipients configured; skipping measurement event email")
                return False
            send_as_html = bool(getattr(current_email_config, 'send_as_html', False))
            messages: list[tuple[str, MIMEMultipart]] = []
            for recipient in recipients:
                html_body = (
                    self._render_html_email_body(
                        body,
                        subject=subject,
                        template_name=f"measurement_{event}",
                        template_params=params,
                    )
                    if send_as_html
                    else None
                )
                messages.append((recipient, self._create_email_message(subject, body, recipient, html_body=html_body)))
            success_count = self._send_emails_batch(messages)
            return success_count > 0
        except Exception as exc:
            self.logger.error(f"Error sending measurement {event} notification: {exc}")
            return False

    async def send_measurement_event_async(
        self,
        event: str,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self.send_measurement_event, event, session_id, start_time, end_time, reason)

    def _log_batch_header(
        self,
        recipients: list[str],
        max_retries: int,
        config: 'EmailConfig',
    ) -> None:
        """Log the start of an email batch with configuration details."""
        self.logger.info("=" * 50)
        self.logger.info("\U0001F4E7 STARTING EMAIL BATCH SEND!")
        self.logger.info("=" * 50)
        self.logger.info("\U0001F4CA EMAIL CONFIGURATION:")
        self.logger.info("   SMTP Server: %s", config.smtp_server)
        self.logger.info("   SMTP Port: %s", config.smtp_port)
        self.logger.info("   Sender Email: %s", config.sender_email)
        self.logger.info("   Recipients: %s (%d total)", recipients, len(recipients))
        self.logger.info("   Max Retries: %d", max_retries)
        self.logger.info("   Connection Timeout: %ss", self._connection_timeout)
        self.logger.info("=" * 50)

    def _log_smtp_attempt_error(
        self,
        attempt: int,
        max_retries: int,
        exc: Exception,
        config: 'EmailConfig',
        recipients: list[str],
        *,
        critical: bool = False,
    ) -> None:
        """Log a failed SMTP send attempt with connection details."""
        if critical:
            self.logger.error("\u274c CRITICAL ERROR: %s", type(exc).__name__)
        else:
            self.logger.error(
                "\u274c SMTP ATTEMPT %d/%d FAILED: %s",
                attempt + 1,
                max_retries,
                type(exc).__name__,
            )
        self.logger.error("   Error: %s", exc)
        self.logger.error("   \U0001F4E1 CONNECTION DETAILS:")
        self.logger.error("      Server: %s", config.smtp_server)
        self.logger.error("      Port: %s", config.smtp_port)
        self.logger.error("      Sender: %s", config.sender_email)
        self.logger.error("      Timeout: %ss", self._connection_timeout)
        self.logger.error("      Recipients: %s", recipients)

    def _log_batch_result(
        self,
        success_count: int,
        total: int,
        config: 'EmailConfig',
        recipients: list[str],
    ) -> None:
        """Log the final result of an email batch."""
        self.logger.info("=" * 50)
        if success_count > 0:
            self.logger.info("\u2705 EMAIL BATCH COMPLETED SUCCESSFULLY")
            self.logger.info("   \U0001F4CA Results: %d/%d emails sent", success_count, total)
            self.logger.info(
                "   \U0001F4E1 Used config: Server: %s; Port: %s",
                config.smtp_server,
                config.smtp_port,
            )
            self.logger.info("   \U0001F4E4 Sender: %s", config.sender_email)
        else:
            self.logger.error("\u274c EMAIL BATCH FAILED COMPLETELY")
            self.logger.error("   \U0001F4CA Results: 0/%d emails sent", total)
            self.logger.error(
                "   \U0001F4E1 Failed config: Server: %s; Port: %s",
                config.smtp_server,
                config.smtp_port,
            )
            self.logger.error("   \U0001F4E4 Failed sender: %s", config.sender_email)
            self.logger.error("   \U0001F3AF Target recipients: %s", recipients)
        self.logger.info("=" * 50)

    def _send_emails_batch(
        self,
        messages: list[tuple[str, MIMEMultipart]],
        max_retries: int = 3,
        abort_check: Optional[Callable[[], None]] = None,
    ) -> int:
        """Send a message to multiple recipients; one item per recipient.

        messages: list of (recipient, message)
        Returns number of successful sends.
        """

        current_email_config = self._get_current_email_config()
        recipients = [r for r, _ in messages]
        success_count = 0

        self._log_batch_header(recipients, max_retries, current_email_config)

        for attempt in range(max_retries):
            try:
                if abort_check is not None:
                    abort_check()
                with self._smtp_lock:
                    with smtplib.SMTP(
                        current_email_config.smtp_server,
                        current_email_config.smtp_port,
                        timeout=self._connection_timeout,
                    ) as smtp:
                        success_count = 0
                        failed_total: Dict[str, Any] = {}
                        for r, m in messages:
                            try:
                                if abort_check is not None:
                                    try:
                                        abort_check()
                                    except AlertSendAborted:
                                        if success_count > 0:
                                            self.logger.info(
                                                "Alert send aborted after %s successful recipient(s)",
                                                success_count,
                                            )
                                        raise
                                failed = smtp.sendmail(
                                    current_email_config.sender_email,
                                    [r],
                                    m.as_string(),
                                )
                                if failed:
                                    failed_total.update(failed)
                                else:
                                    success_count += 1
                            except AlertSendAborted:
                                raise
                            except Exception as exc:
                                failed_total[r] = str(exc)

                        if failed_total:
                            self.logger.warning("Failed to send email to: %s", failed_total)

                if success_count > 0:
                    break

            except AlertSendAborted:
                raise
            except (smtplib.SMTPException, ConnectionError, OSError) as exc:
                self._log_smtp_attempt_error(
                    attempt,
                    max_retries,
                    exc,
                    current_email_config,
                    recipients,
                )

                with self._smtp_lock:
                    self._close_smtp_connection()
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.info("Retrying in %s seconds...", wait_time)
                    time.sleep(wait_time)
                    continue
                else:
                    self.logger.error(
                        "SMTP-connection failed after %s attempts: %s",
                        max_retries,
                        exc,
                    )
            except Exception as exc:
                self._log_smtp_attempt_error(
                    attempt,
                    max_retries,
                    exc,
                    current_email_config,
                    recipients,
                    critical=True,
                )

                with self._smtp_lock:
                    self._close_smtp_connection()
                break

        self._log_batch_result(success_count, len(recipients), current_email_config, recipients)
        return success_count

    def _should_send_alert_unsafe(self) -> bool:
        """Check cooldown and max-alert limits for the current session."""
        max_alerts = max(1, int(getattr(self.measurement_config, 'max_alerts_per_session', 1)))
        if self.alerts_sent_count >= max_alerts:
            self.logger.debug(
                "Alert limit reached for current session (%s/%s)",
                self.alerts_sent_count,
                max_alerts,
            )
            return False

        if self.last_alert_time is None:
            return True

        if self.alert_cooldown_seconds <= 0:
            return True

        time_since_last = datetime.now() - self.last_alert_time
        cooldown_reached = time_since_last.total_seconds() >= self.alert_cooldown_seconds

        if not cooldown_reached:
            remaining = self.alert_cooldown_seconds - time_since_last.total_seconds()
            self.logger.debug(f"Alert-cooldown active, remaining: {remaining:.0f}s")

        return cooldown_reached
    
    def _should_send_alert(self) -> bool:
        with self._state_lock:
            return self._should_send_alert_unsafe()

    def _create_email_message(
        self,
        subject: str,
        body: str,
        recipient: str,
        *,
        html_body: Optional[str] = None,
        inline_image_bytes: Optional[bytes] = None,
        inline_image_filename: Optional[str] = None,
        inline_image_content_id: Optional[str] = None,
    ) -> MIMEMultipart:
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

        if html_body is not None and inline_image_bytes is not None and inline_image_filename and inline_image_content_id:
            msg = MIMEMultipart('related')
            alternative_part = MIMEMultipart('alternative')
            alternative_part.attach(MIMEText(body, 'plain', 'utf-8'))
            alternative_part.attach(MIMEText(html_body, 'html', 'utf-8'))
            msg.attach(alternative_part)
            msg.attach(
                self._create_image_part(
                    inline_image_bytes,
                    filename=inline_image_filename,
                    disposition="inline",
                    content_id=inline_image_content_id,
                )
            )
            msg['From'] = current_email_config.sender_email
            msg['To'] = recipient
            msg['Subject'] = subject
            msg['Date'] = formatdate(localtime=True)
            return msg

        if html_body is not None:
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))
            msg['From'] = current_email_config.sender_email
            msg['To'] = recipient
            msg['Subject'] = subject
            msg['Date'] = formatdate(localtime=True)
            return msg

        msg = MIMEMultipart()
        msg['From'] = current_email_config.sender_email
        msg['To'] = recipient
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)
        
        # Text-Inhalt hinzufügen
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        return msg
    
    def _encode_frame(
        self,
        frame: Optional[np.ndarray],
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
        encoded_buffer = buf
        encoded_filename = filename
        # --------------------------------------------------------------------

        # --- Attachment erzeugen & anhängen ---------------------------------
        msg.attach(
            self._create_image_part(
                encoded_buffer.tobytes(),
                filename=encoded_filename,
                disposition="attachment",
            )
        )

        self.logger.debug(
            "Image attachment added: %s (%d bytes)", encoded_filename, encoded_buffer.size
        )

    # === Status export for GUI ===

    def get_alert_status(self) -> Dict[str, Any]:
        """
        Exports alert status for GUI.

        Returns:
            Dict with alert information
        """
        current_email_config = self._get_current_email_config()
        with self._state_lock:
            max_alerts = max(1, int(getattr(self.measurement_config, 'max_alerts_per_session', 1)))
            return {
                'last_alert_time': self.last_alert_time,
                'alerts_sent_count': self.alerts_sent_count,
                'max_alerts_per_session': max_alerts,
                'cooldown_remaining': self._get_cooldown_remaining_unsafe(),
                'can_send_alert': self._alert_session_id is not None and self._should_send_alert_unsafe(),
                'configured_recipients': len(self._get_effective_recipients()),
                'smtp_server': current_email_config.smtp_server,
            }

    def _get_cooldown_remaining_unsafe(self) -> Optional[float]:
        """
        Berechnet verbleibende Cooldown-Zeit.
        
        Returns:
            Verbleibende Sekunden, None wenn kein Cooldown aktiv
        """
        if self.last_alert_time is None:
            return None

        if self.alert_cooldown_seconds <= 0:
            return None

        time_since_last = datetime.now() - self.last_alert_time
        cooldown_seconds = self.alert_cooldown_seconds
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
            recipients_for_log = self._get_effective_recipients()
            self.logger.info(f"   Recipients: {recipients_for_log}")
            self.logger.info(f"   Website URL: {self._resolve_website_url()}")
            self.logger.info("=" * 50)

            # Build subject/body from configured test template (includes metadata placeholders)
            tpl = current_email_config.test_template()
            params = self._build_common_template_params(
                session_id='TEST',
                last_motion_time=None,
                start_time=None,
                end_time=None,
                duration='',
                reason=''
            )
            subject = tpl.subject.format(**params)
            test_message = tpl.body.format(**params)
            send_as_html = bool(getattr(current_email_config, 'send_as_html', False))
             
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

            attachment_bytes: Optional[bytes] = None
            attachment_name: Optional[str] = None
            if frame is not None:
                encoded_ok, encoded_buffer, encoded_filename = self._encode_frame(
                    frame,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                if encoded_ok and encoded_buffer is not None and encoded_filename is not None:
                    attachment_bytes = encoded_buffer.tobytes()
                    attachment_name = encoded_filename

            recipients = self._get_effective_recipients()
            messages: list[tuple[str, MIMEMultipart]] = []
            shared_inline_cid = (
                make_msgid(domain="cvd-tracker.local")[1:-1]
                if send_as_html and attachment_bytes is not None and attachment_name is not None
                else None
            )
            for recipient in recipients:
                html_body = None
                if send_as_html:
                    html_body = self._render_html_email_body(
                        test_message,
                        subject=subject,
                        template_name="test",
                        template_params=params,
                        inline_image_cid=shared_inline_cid,
                        image_alt_text="Test image",
                    )
                msg = self._create_email_message(
                    subject,
                    test_message,
                    recipient,
                    html_body=html_body,
                    inline_image_bytes=attachment_bytes if send_as_html else None,
                    inline_image_filename=attachment_name if send_as_html else None,
                    inline_image_content_id=shared_inline_cid,
                )
                if not send_as_html and attachment_bytes is not None and attachment_name is not None:
                    msg.attach(
                        self._create_image_part(
                            attachment_bytes,
                            filename=attachment_name,
                            disposition="attachment",
                        )
                    )
                messages.append((recipient, msg))
            success_count = self._send_emails_batch(messages)
            return success_count > 0
            
        except Exception as exc:
            current_email_config = self._get_current_email_config()
            self.logger.error("=" * 50)
            self.logger.error(f"💥 TEST EMAIL FAILED: {type(exc).__name__}")
            self.logger.error("=" * 50)            
            self.logger.error(f"   Error: {exc}")
            self.logger.error(f"   📡 CONFIG USED:")
            self.logger.error(f"      Server: {current_email_config.smtp_server}")
            self.logger.error(f"      Port: {current_email_config.smtp_port}")
            self.logger.error(f"      Sender: {current_email_config.sender_email}")
            recipients_for_log = self._get_effective_recipients()
            self.logger.error(f"      Recipients: {recipients_for_log}")
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
            self.logger.info("Starting EMailSystem cleanup...")
            
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
                self._alert_session_id = None
            
            self._alert_system_cleanup = True  # Set cleanup flag
            self.logger.info("EMailSystem cleanup completed")
            
        except Exception as exc:
            self.logger.error(f"Error during EMailSystem cleanup: {exc}")
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of the EMailSystem"""
        health: Dict[str, Any] = {
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
                recipients = self._get_effective_recipients()
                if not recipients:
                    config_ok = False
                    config_message = "No effective email recipients configured"
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
                'total_recipients_configured': len(current_email_config.recipients),
                'recipients_configured': len(self._get_effective_recipients()),
                'cooldown_minutes_configured': self.cooldown_minutes,
                'cooldown_seconds_configured': self.alert_cooldown_seconds,
            }


# === Factory-Funktionen ===

def create_email_system_from_config(
    config: Optional[AppConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> EMailSystem:
    """
    Erstellt EMailSystem aus Konfiguration.

    Args:
        config_path: Optional Pfad zur Konfigurationsdatei
        
    Returns:
        Konfiguriertes EMailSystem
    """
    from .config import load_config
    
    if config is None:
        config = load_config()
    return EMailSystem(config.email, config.measurement, config, logger)


# Backward compatibility factory (deprecated)
def create_alert_system_from_config(
    config: Optional['AppConfig'] = None,
    logger: Optional['logging.Logger'] = None,
) -> 'EMailSystem':
    import warnings
    warnings.warn("create_alert_system_from_config is deprecated; use create_email_system_from_config", DeprecationWarning, stacklevel=2)
    return create_email_system_from_config(config, logger)

# Test-Code zur Verifikation
def test_template_rendering() -> bool:
    """Testet ob Template-Rendering funktioniert"""
    import sys
    from pathlib import Path
    # Projekt-Root zum Python-Pfad hinzufügen
    project_root = Path(__file__).parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.config import load_config
    logger = logging.getLogger(__name__)
    try:
        template = load_config("config/config.yaml").email.alert_template()

        test_params = {
            'timestamp': '2025-01-14 10:30:00',
            'session_id': 'TEST-123',
            'last_motion_time': '10:25:00',
            'website_url': 'http://localhost:8080',
            'camera_index': 0,
            'sensitivity': 0.1,
            'roi_enabled': True,
            'cvd_id': 1,
            'cvd_name': 'Test_CVD',
            'start_time': '',
            'end_time': '',
            'duration': '',
            'reason': '',
            'snapshot_note': '',
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
