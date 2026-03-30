# CVD-Tracker Projektbeschreibung

Der **CVD-Tracker** ist eine Python-basierte Anwendung zur Webcam-Überwachung und Bewegungserkennung mit einer modernen Web-Oberfläche (NiceGUI). Das System ist darauf ausgelegt, Bewegungslosigkeit in definierten Zeiträumen zu erkennen und entsprechende Alarm-Benachrichtigungen per E-Mail zu versenden.

## 1. Kernfunktionen

### 1.1 Webcam-Steuerung & Live-View
- **Live-Stream:** Anzeige des Webcam-Bildes in Echtzeit (MJPEG-Stream) auf dem Dashboard.
- **UVC-Steuerung:** Umfangreiche Einstellungsmöglichkeiten für UVC-kompatible Kameras direkt über die GUI:
  - Helligkeit (Brightness)
  - Kontrast (Contrast)
  - Sättigung (Saturation)
  - Schärfe (Sharpness)
  - Gamma
  - Verstärkung (Gain)
  - Gegenlichtkompensation (Backlight Compensation)
  - Farbton (Hue)
  - **Weißabgleich:** Automatisch oder Manuell (Farbtemperatur).
  - **Belichtung:** Automatisch oder Manuell.
- **Auflösung:** Konfigurierbare Auflösung und Framerate.

### 1.2 Bewegungserkennung (Motion Detection)
- **Algorithmus:** Basiert auf OpenCV (MOG2 Background Subtraction) zur robusten Erkennung von Änderungen im Bild.
- **Region of Interest (ROI):** Interaktive Festlegung des zu überwachenden Bildbereichs, um Fehlalarme durch irrelevante Bewegungen im Hintergrund zu vermeiden.
- **Sensitivität:** Stufenlos einstellbare Empfindlichkeit der Erkennung.
- **Status-Anzeige:** Visuelles Feedback im Dashboard, ob aktuell Bewegung erkannt wird.

### 1.3 Messungs-Sitzungen (Measurement Sessions)
- **Konzept:** Überwachung erfolgt in expliziten "Sitzungen", die manuell gestartet und gestoppt werden.
- **Dauer:** Optionale Begrenzung der Sitzungsdauer (z.B. 1 Stunde) sowie Inaktivitäts-Timeouts.
- **Alarm-Logik:** Ein Alarm wird ausgelöst, wenn **während einer aktiven Sitzung** für eine definierte Zeitspanne (`Alert Delay`) **keine Bewegung** erkannt wurde.
- **Cooldown:** Einstellbare Wartezeit zwischen zwei Alarmen, um E-Mail-Flut zu verhindern.

### 1.4 E-Mail-Benachrichtigung
- **Auslöser:**
  - Alarm bei Bewegungslosigkeit.
  - Start/Ende/Stopp einer Messung (konfigurierbar).
- **Inhalt:**
  - Dynamische Templates für Betreff und Nachrichtentext.
  - Platzhalter für Zeitstempel, CVD-ID, Dauer, Grund, etc.
  - **Anhang:** Aktuelles Webcam-Bild zum Zeitpunkt des Alarms.
- **Empfänger:**
  - Verwaltung von Empfänger-Listen.
  - Gruppierung von Empfängern (z.B. "Technik", "Management") mit Aktivierung/Deaktivierung ganzer Gruppen.
- **Transport:** SMTP-Versand (spezifiziert für Intranet-Nutzung ohne SSL/Auth).

## 2. Benutzeroberfläche (GUI)

Die Oberfläche ist als Web-Applikation (NiceGUI) realisiert und in zwei Hauptbereiche unterteilt:

### 2.1 Dashboard (Home)
- **Live-Feed:** Großes Kamerabild.
- **Schnellzugriff:** Start/Stopp der Messung, Einstellung der Dauer.
- **Status:** Anzeige von Motion-Status, aktiver Messung und Alarm-Countdown.
- **Kamera-Quick-Settings:** Wichtige UVC-Regler (Helligkeit, Kontrast etc.) direkt griffbereit.

### 2.2 Einstellungen (Settings)
Detaillierte Konfiguration aller Systemparameter:
- **Kamera:** Auswahl der Kamera-ID, Auflösung, detaillierte UVC-Kontrollen.
- **Motion Detection:** ROI-Editor, Sensitivität, Lernrate.
- **Measurement:** Timeouts, Delays, Speicherpfade für Bilder.
- **E-Mail:** SMTP-Server, Absender, Empfänger-Verwaltung, Template-Editor.
- **System:**
  - **Logs:** Live-Log-Viewer und Download der Logs als ZIP.
  - **Update:** Integrierte Update-Funktion (Git-basiert).
  - **Metadata:** Einstellung von CVD-ID und Name zur Identifikation des Geräts.

## 3. Technische Details

- **Sprache:** Python 3.10+
- **Frameworks:**
  - **GUI:** NiceGUI (basierend auf FastAPI/Vue.js)
  - **Computer Vision:** OpenCV (cv2), NumPy
- **Konfiguration:** Persistente Speicherung in `config/config.yaml`.
- **Logging:** Rotierende Log-Dateien (`logs/cvd_tracker.log`) mit konfigurierbarem Level.
- **Update-Mechanismus:** Selbst-Update fähig durch `git pull` und automatische Abhängigkeitsinstallation (`pip`).

## 4. Sicherheit & Datenschutz
- **Netzwerk:** Konzipiert für den Einsatz in geschlossenen Intranets.
- **E-Mail:** SMTP-Implementierung verzichtet bewusst auf Verschlüsselung/Auth (gemäß Anforderung für lokales Relay).
- **Daten:** Bilder werden nur temporär für den Alarmversand gespeichert (konfigurierbar).
