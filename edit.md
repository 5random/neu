# edit.md

Diese Datei sammelt die wichtigsten Hinweise zum manuellen Bearbeiten von `config/config.yaml` und `help/help.yaml`, damit diese Erklaerungen nicht als Header in mehreren Dateien doppelt gepflegt werden muessen.

## `config/config.yaml`

`config/config.yaml` ist die laufzeitnahe Hauptkonfiguration der Anwendung. Die Datei wird von der App gelesen und bei Speichervorgaengen aus der GUI auch wieder geschrieben. Wenn du sie manuell editierst, sollte die Anwendung dabei moeglichst nicht parallel dieselben Einstellungen speichern, weil deine Aenderungen sonst ueberschrieben werden koennen.

Wichtige Hinweise zur manuellen Bearbeitung:

- YAML ist einrueckungssensitiv. Die Struktur wird also ueber Leerzeichen definiert.
- Bestehende Abschnittsnamen sollten unveraendert bleiben.
- Listen wie `resolution`, `recipients`, `active_groups` oder `static_recipients` muessen als YAML-Listen erhalten bleiben.
- Mehrzeilige Mail-Texte unter `email.templates` sollten im Blockformat (`|` oder `|-`) bleiben.

### Bereiche und Einstellungen

#### `metadata`

Beschreibt die Instanz selbst:

- `version`: fachliche oder interne Versionsangabe der Instanz
- `description`: allgemeine Beschreibung
- `cvd_id`: numerische Instanzkennung
- `cvd_name`: lesbarer Name der Instanz
- `released_at`: Datumsfeld fuer die Instanz oder Konfiguration

#### `webcam`

Steuert Kameraquelle und Aufloesungen:

- `camera_index`: welches Kamerageraet verwendet wird
- `default_resolution`: bevorzugte Startaufloesung
- `fps`: Aufnahme-FPS
- `preview_fps`: Bildrate der Vorschau in der GUI
- `preview_max_width`: maximale Breite der Vorschau
- `preview_jpeg_quality`: JPEG-Qualitaet der Vorschau
- `resolution`: Liste zulaessiger bzw. angebotener Aufloesungen

#### `uvc_controls`

Enthaelt die Kamera-Bildeinstellungen, also die klassischen UVC-Regler:

- `brightness`, `hue`, `contrast`, `saturation`, `sharpness`, `gamma`
- `white_balance.auto` und `white_balance.value`
- `gain`
- `backlight_compensation`
- `exposure.auto` und `exposure.value`

Diese Werte beschreiben das Kamerabild, nicht die Bewegungslogik.

#### `motion_detection`

Steuert die Bewegungserkennung:

- `region_of_interest`: aktiver Ausschnitt mit `enabled`, `x`, `y`, `width`, `height`
- `sensitivity`: Empfindlichkeit der Erkennung
- `background_learning_rate`: wie schnell sich das Hintergrundmodell anpasst
- `min_contour_area`: Mindestgroesse erkannter Bewegungsflaechen
- `frame_skip`: wie viele Frames ausgelassen werden
- `processing_max_width`: interne Verarbeitungsbreite

#### `measurement`

Regelt den eigentlichen Messbetrieb und die Alert-Logik:

- `auto_start`: automatischer Start
- `session_timeout_minutes` und `session_timeout_seconds`: Laufzeitgrenzen der Sitzung
- `save_alert_images`: ob Alert-Bilder gespeichert werden
- `image_save_path`: Speicherort der Bilder
- `image_format`: Format der gespeicherten Bilder
- `image_quality`: Qualitaet gespeicherter Bilder
- `alert_delay_seconds`: Wartezeit bis ein Alert entstehen darf
- `max_alerts_per_session`: Begrenzung pro Sitzung
- `alert_check_interval`: Pruefintervall
- `alert_cooldown_seconds`: Sperrzeit zwischen Alerts
- `alert_include_snapshot`: ob Alerts ein Bild enthalten
- `inactivity_timeout_minutes`: Inaktivitaetsgrenze
- `motion_summary_interval_seconds`: Intervall fuer Bewegungszusammenfassungen
- `enable_motion_summary_logs`: ob diese Zusammenfassungen geloggt werden
- `history_path`: Speicherort der Alert-Historie

#### `email`

Steuert Versand, Empfaenger und Mail-Inhalte:

- `website_url`: Basislink, der in E-Mails verwendet werden kann
- `website_url_source`: legt fest, woher der Link effektiv genommen wird
- `recipients`: einfache Empfaengerliste
- `smtp_server`, `smtp_port`, `sender_email`: Versandparameter
- `send_as_html`: HTML- oder Textversand
- `templates`: Vorlagen fuer `alert`, `measurement_start`, `measurement_end`, `measurement_stop`
- `groups`: definierte Empfaengergruppen
- `active_groups`: aktuell aktive Gruppen fuer den Lauf
- `static_recipients`: immer aktive Empfaenger
- `explicit_targeting`: schaltet explizite Zielgruppenlogik
- `notifications`: globale Schalter fuer `on_start`, `on_end`, `on_stop`
- `group_prefs`: Ereignisrechte je Gruppe
- `recipient_prefs`: Ereignisrechte je Empfaenger

#### `gui`

Beschreibt, wie die Weboberflaeche bereitgestellt wird:

- `title`: Fenstertitel bzw. Oberflaechentitel
- `host`, `port`: Bind-Adresse und Port
- `reverse_proxy_enabled`: Reverse-Proxy-Modus
- `forwarded_allow_ips`: erlaubte Proxy-IPs fuer Forward-Header
- `root_path`: Subpfadbetrieb hinter einem Proxy
- `session_cookie_https_only`: HTTPS-Only fuer Session-Cookies
- `auto_open_browser`: Browser beim Start automatisch oeffnen
- `update_interval_ms`: schnelles GUI-Updateintervall
- `status_refresh_interval_ms`: Intervall fuer Statusaktualisierungen

#### `logging`

Steuert die Log-Ausgabe:

- `level`: gewuenschtes Log-Level
- `file`: Pfad zur Logdatei
- `max_file_size_mb`: Groesse je Logdatei
- `backup_count`: Anzahl alter Rotationsdateien
- `console_output`: zusaetzliche Ausgabe auf der Konsole

## `help/help.yaml`

`help/help.yaml` enthaelt die redaktionellen Inhalte der Hilfeseite. Jede Section besteht im Wesentlichen aus:

- `title`: sichtbarer Titel der Hilfekarte
- `link`: Zielroute in der Anwendung
- `content`: Markdown-Inhalt der Section

Beim Bearbeiten gilt:

- Neue Hilfeseiten werden als weiterer Eintrag unter `help.sections` angelegt.
- `content` sollte als mehrzeiliger Markdown-Block gepflegt werden.
- Bei `/settings#...`-Links darf einfach die Fragment-Schreibweise verwendet werden; die Anwendung ergaenzt intern noetige Fallbacks fuer die Navigation.

### Routen, die aktuell in `help/help.yaml` verwendet werden

- `/`
- `/settings`
- `/settings#camera`
- `/settings#measurement`
- `/settings#email`
- `/settings#appearance`
- `/settings#metadata`
- `/settings#config`
- `/settings#update`
- `/settings#logs`

### Zusaetzliche sinnvolle Routen fuer kuenftige Help-Links

Diese Routen sind im Code ebenfalls vorhanden oder als Anker erreichbar und koennen bei Bedarf in `help.yaml` verlinkt werden:

- `/help`
- `/settings#motion`
- `/shutdown`
- `/restart`
- `/pi-restart`
- `/pi-shutdown`
- `/updating`

### Aktuelle Zuordnung der bestehenden Help-Sections

- `Getting Started` -> `/`
- `Camera Settings` -> `/settings#camera`
- `Motion Detection` -> `/settings#camera`
- `Measurement & Sessions` -> `/settings#measurement`
- `E-Mail Notifications` -> `/settings#email`
- `Appearance` -> `/settings#appearance`
- `Metadata` -> `/settings#metadata`
- `Configuration` -> `/settings#config`
- `Software Update` -> `/settings#update`
- `Logs` -> `/settings#logs`
- `Troubleshooting and other tips and tricks` -> `/settings#logs`
- `Power Menu and Restarting` -> `/settings`
