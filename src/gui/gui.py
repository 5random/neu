"""
CVDTracker - Webcam-Überwachungssystem mit Bewegungserkennung und E-Mail-Benachrichtigung.

Diese Datei implementiert die NiceGUI-basierte Benutzeroberfläche für das Webcam-Überwachungssystem
gemäß der Projektbeschreibung. Die GUI ermöglicht:
- Anzeige des Webcam-Streams
- Steuerung der UVC-Kameraeinstellungen
- Konfiguration der Bewegungserkennung mit ROI-Editor
- Steuerung von Messungen und Alert-E-Mail-Konfiguration
"""

from __future__ import annotations

import time
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable

import cv2
import numpy as np
import sys
from pathlib import Path
# Projekt-Root ins Suchpfad einfügen für absolute Imports
# Projekt-SRC-Ordner zum Suchpfad hinzufügen, damit Importe funktionieren
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # Projekt-Root für Imports
from fastapi import Response
from nicegui import app, ui, run, Client
from src.config import load_config, save_config, AppConfig
from src.cam.camera import Camera
from src.cam.motion import MotionResult
from src.measurement import MeasurementController
from src.alert import AlertSystem

# Logging konfigurieren
logger = logging.getLogger(__name__)

class WebcamMonitoringApp:
    """Hauptklasse für die Webcam-Überwachungs-GUI."""

    def __init__(self):
        """Initialisiert die Anwendung und lädt die Konfiguration."""
        self.config_path = "config/config.yaml"
        self.app_config = load_config(self.config_path)
        
        # Komponenten initialisieren
        self.camera = Camera(self.config_path)
        self.alert_system = AlertSystem(self.app_config.email, self.app_config.measurement)
        self.measurement = MeasurementController(self.app_config.measurement, self.alert_system)
        
        # GUI-State
        self.status_message = "Keine Bewegung"
        self.status_color = "green"
        self.measurement_active = False
        self.measurement_progress = 0.0
        self.roi_edit_mode = False
        self.last_alert_time = None
        self.email_list = self.app_config.email.recipients
        
        # API-Routes für Webcam-Stream einrichten
        self._setup_routes()

    def _setup_routes(self):
        """Richtet die API-Routes für die Webcam-Stream-Bereitstellung ein."""
        @app.get("/video/frame")
        async def video_frame_route() -> Response:
            return await self.camera.grab_video_frame()

    def create_ui(self):
        """Erstellt die Haupt-Benutzeroberfläche."""
        # Header/Titel
        with ui.header().classes('bg-primary text-white'):
            ui.label('CVD-Tracker: Webcam-Überwachungssystem').classes('text-h6')
        
        # Hauptlayout in zwei Spalten
        with ui.row().classes('w-full no-wrap'):
            # Linke Spalte: Video und Status
            self._create_left_column()
            
            # Rechte Spalte: Konfiguration
            self._create_right_column()
            
        # Footer
        with ui.footer().classes('bg-gray-200'):
            ui.label('CVD-Tracker v0.1.0').classes('text-center w-full')

    def _create_left_column(self):
        """Erstellt die linke Spalte mit Webcam-Stream und Statusanzeigen."""
        with ui.column().classes('w-1/2'):
            # Webcam-Stream
            with ui.card().classes('w-full'):
                ui.label('Webcam-Stream').classes('text-h6')
                # Platzhalter für Stream
                self.video = ui.interactive_image().classes('w-full h-64')
                ui.timer(0.1, lambda: self.video.set_source(f'/video/frame?{time.time()}'))
            
            # Statusanzeige
            with ui.card().classes('w-full items-center'):
                ui.label('Status').classes('text-h6')
                self.status_label = ui.label('Keine Bewegung').classes('text-2xl text-green-500')
                self.status_icon = ui.icon('check_circle').classes('text-xl text-green-500')
            
            # Messungstool
            with ui.card().classes('w-full'):
                ui.label('Messung').classes('text-h6')
                with ui.row().classes('w-full items-center'):
                    self.start_button = ui.button('Messung starten', on_click=self.toggle_measurement)
                    self.stop_button = ui.button('Messung stoppen', on_click=self.toggle_measurement).bind_visibility(lambda: self.measurement_active)
                
                self.duration_slider = ui.slider(min=1, max=120, value=30).props('label="Messdauer (Minuten)"')
                self.measurement_status = ui.label('Messung läuft: 0 von 30 Min')
                self.measurement_progress_bar = ui.linear_progress(0.0)
                self.last_alert_label = ui.label('Letzter Alarm: -')
                
                # Timer für Status-Updates
                ui.timer(1.0, self.update_status)

    def _create_right_column(self):
        """Erstellt die rechte Spalte mit Akkordeon-Menüs für Einstellungen."""
        with ui.column().classes('w-1/2'):
            # Akkordeon: Kameraeinstellungen
            self._create_camera_settings_accordion()
            
            # Akkordeon: Bewegungserkennung
            self._create_motion_detection_accordion()
            
            # Akkordeon: E-Mail-Benachrichtigungen
            self._create_email_notification_accordion()

    def _create_camera_settings_accordion(self):
        """Erstellt das Akkordeon für Kameraeinstellungen."""
        with ui.expansion('Kameraeinstellungen', icon='camera_alt').classes('w-full'):
            # Gruppe: Bildqualität
            with ui.expansion('Bildqualität', icon='tune'):
                # UVC-Parameter-Slider
                self.brightness_slider = ui.slider(min=0, max=255, value=128).props('label="Helligkeit"')
                self.brightness_slider.on('update:model-value', lambda e: self.camera.set_brightness(e.value))
                
                self.hue_slider = ui.slider(min=-180, max=180, value=0).props('label="Farbton"')
                self.hue_slider.on('update:model-value', lambda e: self.camera.set_hue(e.value))
                
                self.contrast_slider = ui.slider(min=0, max=255, value=128).props('label="Kontrast"')
                self.contrast_slider.on('update:model-value', lambda e: self.camera.set_contrast(e.value))
                
                self.saturation_slider = ui.slider(min=0, max=255, value=128).props('label="Sättigung"')
                self.saturation_slider.on('update:model-value', lambda e: self.camera.set_saturation(e.value))
                
                self.sharpness_slider = ui.slider(min=0, max=255, value=128).props('label="Schärfe"')
                self.sharpness_slider.on('update:model-value', lambda e: self.camera.set_sharpness(e.value))
            
            # Gruppe: Belichtung & Weißabgleich
            with ui.expansion('Belichtung & Weißabgleich', icon='wb_sunny'):
                # Weißabgleich
                self.wb_auto = ui.toggle({True: 'Auto', False: 'Manuell'}, value=True).props('label="Weißabgleich"')
                self.wb_auto.on('update:model-value', lambda e: self.camera.set_auto_white_balance(e.value))
                
                self.wb_manual = ui.slider(min=2000, max=6500, value=4500).props('label="Weißabgleich Manuell"')
                self.wb_manual.bind_visibility_from(self.wb_auto, 'value', value=False)
                self.wb_manual.on('update:model-value', lambda e: self.camera.set_manual_white_balance(e.value))
                
                # Belichtung
                self.exp_auto = ui.toggle({True: 'Auto', False: 'Manuell'}, value=True).props('label="Belichtung"')
                self.exp_auto.on('update:model-value', lambda e: self.camera.set_auto_exposure(e.value))
                
                self.exp_manual = ui.slider(min=-13, max=0, value=-7).props('label="Belichtung Manuell"')
                self.exp_manual.bind_visibility_from(self.exp_auto, 'value', value=False)
                self.exp_manual.on('update:model-value', lambda e: self.camera.set_manual_exposure(e.value))
                
                # Weitere Parameter
                self.gamma_slider = ui.slider(min=100, max=800, value=200).props('label="Gamma"')
                self.gamma_slider.on('update:model-value', lambda e: self.camera.set_gamma(e.value))
                
                self.gain_slider = ui.slider(min=0, max=255, value=0).props('label="Gain"')
                self.gain_slider.on('update:model-value', lambda e: self.camera.set_gain(e.value))
                
                self.backlight_comp = ui.checkbox('Gegenlichtkompensation')
                self.backlight_comp.on('update:model-value', 
                                       lambda e: self.camera.set_backlight_compensation(255 if e.value else 0))
            
            # Button: Standardwerte zurücksetzen
            ui.button('Standardwerte zurücksetzen', 
                     on_click=lambda: self.camera.reset_uvc_to_defaults() and self.load_camera_values())

    def _create_motion_detection_accordion(self):
        """Erstellt das Akkordeon für Bewegungserkennungseinstellungen."""
        with ui.expansion('Bewegungserkennung', icon='motion_photos_on').classes('w-full'):
            # Sensitivität
            self.sensitivity_slider = ui.slider(min=1, max=100, value=50).props('label="Sensitivität"')
            self.sensitivity_slider.on('update:model-value', 
                                      lambda e: self.update_motion_sensitivity(e.value / 100.0))
            
            # ROI Bearbeitung
            with ui.row():
                self.roi_edit_button = ui.button('ROI bearbeiten', 
                                                on_click=lambda: self.toggle_roi_edit_mode(True))
                self.roi_save_button = ui.button('ROI speichern', 
                                               on_click=lambda: self.save_roi()).bind_visibility(lambda: self.roi_edit_mode)
                self.roi_cancel_button = ui.button('Abbrechen', 
                                                 on_click=lambda: self.toggle_roi_edit_mode(False)).bind_visibility(lambda: self.roi_edit_mode)
            
            # Tooltip für Sensitivität
            with ui.tooltip('Je höher der Wert, desto empfindlicher reagiert die Erkennung.'):
                ui.icon('help')

    def _create_email_notification_accordion(self):
        """Erstellt das Akkordeon für E-Mail-Benachrichtigungseinstellungen."""
        with ui.expansion('E-Mail-Benachrichtigungen', icon='email').classes('w-full'):
            # Alert-Delay
            self.alert_delay = ui.number(label='Alert-Delay (Sekunden)', value=30, min=1)
            with ui.tooltip('Zeit in Sekunden ohne Bewegung, bevor ein Alarm ausgelöst wird.'):
                ui.icon('help')
            
            ui.separator()
            
            # E-Mail-Liste
            ui.label('Empfänger-Liste').classes('text-bold')
            with ui.row().classes('w-full items-center'):
                self.email_input = ui.input(label='Neue E-Mail-Adresse', 
                                           placeholder='test@example.com').props('type="email"')
                ui.button('Hinzufügen', on_click=self.add_email)
            
            # Liste der E-Mail-Empfänger
            self.email_list_container = ui.list()
            self.update_email_list()
            
            ui.separator()
            
            # SMTP-Einstellungen
            self.smtp_host = ui.input(label='SMTP Host', value=self.app_config.email.smtp_server)
            self.smtp_port = ui.number(label='SMTP Port', value=self.app_config.email.smtp_port)
            ui.button('Test-E-Mail senden', on_click=self.send_test_email)

    def update_status(self):
        """Aktualisiert die Statusanzeigen."""
        # Kamera-Status prüfen
        camera_status = self.camera.get_camera_status()
        if not camera_status.get('connected', False):
            self.status_label.text = 'Kamera nicht verbunden'
            self.status_label.classes(replace='text-2xl text-red-500')
            self.status_icon.props(replace={'name': 'videocam_off', 'color': 'red-500'})
            return

        # Bewegungsstatus prüfen
        if self.camera.is_motion_active():
            motion_result = self.camera.get_last_motion_result()
            if motion_result and motion_result.motion_detected:
                self.status_label.text = 'Bewegung erkannt!'
                self.status_label.classes(replace='text-2xl text-red-500')
                self.status_icon.props(replace={'name': 'motion_photos_on', 'color': 'red-500'})
            else:
                self.status_label.text = 'Keine Bewegung'
                self.status_label.classes(replace='text-2xl text-green-500')
                self.status_icon.props(replace={'name': 'check_circle', 'color': 'green-500'})
        
        # Messungsstatus aktualisieren wenn aktiv
        if self.measurement_active:
            session_status = self.measurement.get_session_status()
            if session_status.get('is_active', False):
                duration = session_status.get('duration_minutes', 0)
                total = self.duration_slider.value
                self.measurement_status.text = f'Messung läuft: {duration} von {total} Min'
                self.measurement_progress_bar.value = min(1.0, duration / total)
                
                # Alarm-Countdown anzeigen falls vorhanden
                alert_countdown = session_status.get('alert_countdown')
                if alert_countdown is not None:
                    self.status_label.text = f'Keine Bewegung (Alert in {int(alert_countdown)}s)'
            else:
                # Session beendet
                self.measurement_active = False
                self.start_button.text = 'Messung starten'
                self.measurement_status.text = 'Keine Messung aktiv'
                self.measurement_progress_bar.value = 0.0

    def toggle_measurement(self):
        """Startet oder stoppt eine Überwachungssitzung."""
        if not self.measurement_active:
            # Starten
            success = self.measurement.start_session()
            if success:
                self.measurement_active = True
                self.start_button.text = 'Messung läuft...'
                self.camera.enable_motion_detection(self.on_motion_detected)
                ui.notify('Messung gestartet')
        else:
            # Stoppen
            self.measurement.stop_session()
            self.measurement_active = False
            self.start_button.text = 'Messung starten'
            self.camera.disable_motion_detection()
            self.measurement_status.text = 'Keine Messung aktiv'
            self.measurement_progress_bar.value = 0.0
            ui.notify('Messung gestoppt')

    def on_motion_detected(self, frame: np.ndarray, motion_result: MotionResult):
        """Callback für Bewegungserkennung."""
        if self.measurement_active:
            self.measurement.on_motion_detected(motion_result)
            
            # Prüfen ob Alert ausgelöst werden soll
            if self.measurement.should_trigger_alert():
                success = self.measurement.trigger_alert()
                if success:
                    self.last_alert_time = datetime.now()
                    self.last_alert_label.text = f'Letzter Alarm: {self.last_alert_time.strftime("%H:%M:%S")}'

    def update_motion_sensitivity(self, value: float):
        """Aktualisiert die Sensitivität der Bewegungserkennung."""
        if self.camera.motion_detector:
            self.camera.motion_detector.update_sensitivity(value)
            ui.notify(f'Sensitivität auf {value*100:.0f}% gesetzt')

    def toggle_roi_edit_mode(self, active: bool):
        """Aktiviert oder deaktiviert den ROI-Editiermodus."""
        self.roi_edit_mode = active
        # Hier würde noch Code folgen, um die ROI-Overlay-Darstellung umzuschalten
        ui.notify('ROI-Editiermodus ' + ('aktiviert' if active else 'deaktiviert'))

    def save_roi(self):
        """Speichert die aktuell definierte ROI."""
        # Hier würde der Code folgen, um die ROI zu speichern
        self.roi_edit_mode = False
        ui.notify('ROI gespeichert')

    def add_email(self):
        """Fügt eine neue E-Mail-Adresse zur Empfängerliste hinzu."""
        email = self.email_input.value
        if not email:
            ui.notify('Bitte eine E-Mail-Adresse eingeben', color='warning')
            return
            
        # E-Mail-Validierung mit einfachem Regex
        email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(email_regex, email):
            ui.notify('Ungültige E-Mail-Adresse', color='negative')
            return
            
        if email not in self.email_list:
            self.email_list.append(email)
            self.app_config.email.recipients = self.email_list
            save_config(self.app_config, self.config_path)
            self.update_email_list()
            self.email_input.value = ''
            ui.notify(f'E-Mail {email} hinzugefügt')

    def update_email_list(self):
        """Aktualisiert die Anzeige der E-Mail-Liste."""
        self.email_list_container.clear()
        for email in self.email_list:
            # neues List-Item anlegen, Parent ist das Container-Objekt
            with ui.item(str(self.email_list_container)):
                # Haupt-Section mit Adresse
                with ui.item_section():
                    ui.item_label(email)
                # Side-Section mit Remove-Button
                with ui.item_section().props('side'):
                    ui.button(icon='delete',
                              on_click=lambda e, address=email: self.remove_email(address)
                             ).props('flat dense')

    def remove_email(self, email: str):
        """Entfernt eine E-Mail-Adresse aus der Empfängerliste."""
        if email in self.email_list:
            self.email_list.remove(email)
            self.app_config.email.recipients = self.email_list
            save_config(self.app_config, self.config_path)
            self.update_email_list()
            ui.notify(f'E-Mail {email} entfernt')

    def send_test_email(self):
        """Sendet eine Test-E-Mail mit den aktuellen Einstellungen."""
        # SMTP-Einstellungen aus UI übernehmen
        self.app_config.email.smtp_server = self.smtp_host.value
        self.app_config.email.smtp_port = int(self.smtp_port.value)
        save_config(self.app_config, self.config_path)
        
        # Test-E-Mail senden
        success = self.alert_system.send_test_email("Test-E-Mail vom Webcam-Überwachungssystem")
        if success:
            ui.notify('Test-E-Mail erfolgreich versendet', color='positive')
        else:
            ui.notify('Fehler beim Versenden der Test-E-Mail', color='negative')

    def load_camera_values(self):
        """Lädt die aktuellen Kameraeinstellungen und aktualisiert die UI."""
        # UVC-Werte laden und UI aktualisieren
        uvc_values = self.camera.get_uvc_current_values()
        
        # Slider aktualisieren
        if 'brightness' in uvc_values: self.brightness_slider.value = uvc_values['brightness']
        if 'hue' in uvc_values: self.hue_slider.value = uvc_values['hue']
        if 'contrast' in uvc_values: self.contrast_slider.value = uvc_values['contrast']
        if 'saturation' in uvc_values: self.saturation_slider.value = uvc_values['saturation']
        if 'sharpness' in uvc_values: self.sharpness_slider.value = uvc_values['sharpness']
        if 'gamma' in uvc_values: self.gamma_slider.value = uvc_values['gamma']
        if 'gain' in uvc_values: self.gain_slider.value = uvc_values['gain']
        
        # Auto/Manuell-Toggles aktualisieren
        auto_wb = bool(uvc_values.get('auto_white_balance', 1))
        auto_exp = bool(uvc_values.get('auto_exposure', 1))
        
        self.wb_auto.value = auto_wb
        self.exp_auto.value = auto_exp
        
        # Manuelle Werte aktualisieren
        if 'white_balance' in uvc_values: self.wb_manual.value = uvc_values['white_balance']
        if 'exposure' in uvc_values: self.exp_manual.value = uvc_values['exposure']
        
        # Backlight
        backlight = uvc_values.get('backlight_compensation', 0)
        self.backlight_comp.value = backlight > 0
        
        ui.notify('Kameraeinstellungen geladen')


def main():
    """Hauptfunktion zum Starten der Anwendung."""
    app_instance = WebcamMonitoringApp()
    app_instance.create_ui()
    
    # Ressourcen beim Beenden freigeben
    @app.on_shutdown
    async def shutdown():
        await app_instance.camera.cleanup()

# Diese Zeile nur ausführen, wenn die Datei direkt ausgeführt wird (nicht bei Import)
if __name__ == "__main__":
    main()
    # NiceGUI starten
    ui.run(title="CVD-Tracker", favicon="📷", dark=True)

