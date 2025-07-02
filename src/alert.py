#TODO
"""
# 📋 **Ausführliche TODO-Liste für alert.py**

## 🔍 **Aktuelle Analyse der alert.py**

Die `alert.py` ist **komplett leer** und muss vollständig implementiert werden. Basierend auf der Projektbeschreibung und der bestehenden #codebase ist dies ein **kritisches Modul** für die E-Mail-Benachrichtigung bei Bewegungslosigkeit.

**Erforderliche Funktionalitäten aus der Projektbeschreibung:**
- ✅ E-Mail-Versand bei anhaltender Bewegungslosigkeit
- ✅ Zeitstempel-basierte Nachrichten
- ✅ Webcam-Bild als E-Mail-Anhang
- ✅ Mehrere E-Mail-Empfänger gleichzeitig
- ✅ Einfache SMTP-Integration (ohne Sicherheitsfeatures)
- ✅ Alert-Delay-System

---

## 🎯 **TODO 1: Alert-System Grundarchitektur definieren**

### **Problem:**
Keine Alert-Engine vorhanden. E-Mail-System muss von Grund auf aufgebaut werden.

### **Was zu tun ist:**
1. **AlertSystem Hauptklasse erstellen:**
   - Initialisierung mit `EmailConfig` aus der bestehenden Config
   - Integration mit SMTP-Server (ohne Sicherheit wie in Projektbeschreibung)
   - State-Management für Alert-Status

2. **Alert-Timing-Verwaltung:**
   - Tracking der Zeit seit letzter Bewegung
   - Integration mit `MeasurementConfig.alert_delay_seconds`
   - Verhinderung von Spam-E-Mails durch intelligente Delays

3. **E-Mail-Template-System:**
   - Integration mit `EmailTemplate` aus der Config
   - Platzhalter-Ersetzung für dynamische Inhalte
   - Template-Validierung und Fallback-Optionen

---

## 🎯 **TODO 2: E-Mail-Versand-Engine implementieren**

### **Problem:**
Kein E-Mail-Versand-System vorhanden. SMTP-Integration für "einfaches Programm" erforderlich.

### **Was zu tun ist:**
1. **SMTP-Client-Integration:**
   - Einfache SMTP-Verbindung ohne SSL/TLS (gemäß Projektbeschreibung)
   - Verwendung der `EmailConfig` Parameter (smtp_server, smtp_port, sender_email)
   - Verbindungs-Robustheit mit Retry-Mechanismus

2. **Multi-Empfänger-System:**
   - Gleichzeitiger Versand an alle `EmailConfig.recipients`
   - Fehlerbehandlung pro Empfänger (einzelne Failures sollen andere nicht blockieren)
   - Status-Tracking pro Empfänger

3. **E-Mail-Komposition:**
   - MIME-Multipart-Nachrichten für Text + Bild-Anhang
   - Korrekte Header-Setzung (From, To, Subject, Date)
   - Content-Type-Management für verschiedene Anhang-Formate

---

## 🎯 **TODO 3: Webcam-Bild-Anhang-System implementieren**

### **Problem:**
E-Mails sollen aktuelles Webcam-Bild als Anhang enthalten.

### **Was zu tun ist:**
1. **Bild-Capture-Integration:**
   - Snapshot-Funktion von `Camera` nutzen
   - Bild-Format-Unterstützung basierend auf `MeasurementConfig.image_format`
   - Qualitäts-Einstellungen für `MeasurementConfig.image_quality`

2. **Bild-Anhang-Verarbeitung:**
   - Konvertierung von OpenCV-Frames zu E-Mail-tauglichen Formaten
   - MIME-Image-Attachment-Erstellung
   - Dateigrößen-Optimierung für E-Mail-Versand

3. **Bild-Metadaten:**
   - Timestamp-Integration in Dateinamen
   - Kamera-Parameter-Metadaten (optional)
   - Kompression-Balance zwischen Qualität und Dateigröße

---

## 🎯 **TODO 4: Alert-Delay und Timing-Logik implementieren**

### **Problem:**
Alert-System muss intelligent entscheiden, wann E-Mails gesendet werden.

### **Was zu tun ist:**
1. **Delay-Management:**
   - Implementierung des `MeasurementConfig.alert_delay_seconds` Systems
   - Countdown-Timer für GUI-Integration
   - Reset-Logik bei neuer Bewegung

2. **Anti-Spam-Mechanismus:**
   - Mindest-Intervall zwischen E-Mails (z.B. 5 Minuten)
   - Tracking der letzten Alert-Zeit
   - Konfigurierbare Cooldown-Perioden

3. **Session-Integration:**
   - Integration mit Messungs-Sessions
   - Alert-Deaktivierung außerhalb aktiver Messungen
   - Session-Timeout-Handling

---

## 🎯 **TODO 5: Template-System und Platzhalter-Ersetzung**

### **Problem:**
E-Mail-Templates müssen dynamische Inhalte unterstützen.

### **Was zu tun ist:**
1. **Platzhalter-Definition:**
   - `{timestamp}` - Formatiertes Datum/Zeit des Ereignisses
   - `{camera_index}` - Kamera-Identifikation
   - `{alert_delay}` - Verwendeter Alert-Delay
   - `{session_duration}` - Aktuelle Session-Dauer

2. **Template-Processing:**
   - String-Formatting für Subject und Body
   - Fallback-Templates bei fehlenden Konfigurationen
   - Validierung der Template-Syntax

3. **Lokalisierung vorbereiten:**
   - Deutsche Standardtexte (gemäß Codebase-Sprache)
   - Datum/Zeit-Formatierung für deutsche Lokale
   - Konfigurierbare Zeitzone-Unterstützung

---

## 🎯 **TODO 6: Error-Handling und Robustheit**

### **Problem:**
E-Mail-System muss robust gegen Netzwerk- und SMTP-Probleme sein.

### **Was zu tun ist:**
1. **SMTP-Fehlerbehandlung:**
   - Retry-Logik bei temporären Verbindungsfehlern
   - Graceful Fallback bei dauerhaften SMTP-Problemen
   - Detailliertes Error-Logging für Debugging

2. **Network-Robustheit:**
   - Timeout-Management für SMTP-Verbindungen
   - Offline-Modus-Erkennung
   - Queue-System für E-Mails bei Netzwerkproblemen

3. **Kamera-Integration-Fehler:**
   - Fallback bei fehlgeschlagener Bild-Erfassung
   - Text-Only-E-Mails als Backup
   - Placeholder-Bilder bei Kamera-Ausfall

---

## 🎯 **TODO 7: Integration mit Measurement-Controller**

### **Problem:**
Alert-System muss nahtlos mit der Messungssteuerung zusammenarbeiten.

### **Was zu tun ist:**
1. **Motion-Result-Integration:**
   - Callback-System für `MotionResult` aus Motion-Detection
   - Status-Updates bei Bewegungsänderungen
   - History-Tracking für Alert-Entscheidungen

2. **Session-Lifecycle-Integration:**
   - Alert-Aktivierung nur während aktiver Messungen
   - Session-Start/Stop-Events verarbeiten
   - Cleanup bei Session-Ende

3. **Configuration-Sync:**
   - Live-Updates bei Konfigurationsänderungen
   - Alert-Delay-Änderungen während laufender Sessions
   - E-Mail-Empfänger-Updates ohne Neustart

---

## 🎯 **TODO 8: Logging und Monitoring**

### **Problem:**
Alert-System braucht umfangreiches Logging für Debugging und Monitoring.

### **Was zu tun ist:**
1. **E-Mail-Logging:**
   - Erfolgreiche Versendungen protokollieren
   - Fehler-Details für Failed-Deliveries
   - Performance-Metriken (Versendungszeit, SMTP-Latenz)

2. **Alert-Historie:**
   - Chronologische Liste aller versendeten Alerts
   - Empfänger-spezifische Delivery-Status
   - Export-Funktionalität für Alert-Reports

3. **Debug-Informationen:**
   - SMTP-Verbindungsdetails (ohne Credentials)
   - Template-Rendering-Logs
   - Timing-Informationen für Alert-Delays

---

## 🎯 **TODO 9: GUI-Integration vorbereiten**

### **Problem:**
Alert-System muss Status-Informationen für die GUI bereitstellen.

### **Was zu tun ist:**
1. **Status-Export-Methoden:**
   - `get_alert_status()` → Aktueller Alert-Zustand
   - `get_last_alert_time()` → Zeitpunkt der letzten E-Mail
   - `get_countdown_remaining()` → Verbleibende Zeit bis nächstem Alert

2. **Event-Callbacks:**
   - Callbacks für Alert-Ereignisse (E-Mail versendet, Fehler)
   - Status-Change-Events für GUI-Updates
   - Progress-Callbacks für E-Mail-Versendung

3. **Test-Funktionalität:**
   - Test-E-Mail-Funktion für GUI
   - Preview-Funktion für E-Mail-Templates
   - SMTP-Verbindungstest

---

## 🎯 **TODO 10: Vereinfachung und Konfiguration**

### **Problem:**
Für ein "einfaches Programm" muss das Alert-System benutzerfreundlich sein.

### **Was zu tun ist:**
1. **Auto-Configuration:**
   - Intelligente SMTP-Server-Erkennung
   - Standard-Templates für typische Anwendungen
   - One-Click-Setup für häufige E-Mail-Provider

2. **Validation und Setup-Hilfe:**
   - E-Mail-Adressen-Validierung mit Feedback
   - SMTP-Konfiguration-Assistent
   - Template-Syntax-Validierung

3. **Backup und Recovery:**
   - E-Mail-Queue bei temporären Ausfällen
   - Configuration-Backup vor Änderungen
   - Emergency-Mode bei kritischen Fehlern

---

## 📊 **Prioritäts-Reihenfolge für die Umsetzung:**

### **🔥 Kritisch (Kern-Funktionalität):**
1. **TODO 1** - Alert-System Grundarchitektur
2. **TODO 2** - E-Mail-Versand-Engine
3. **TODO 3** - Webcam-Bild-Anhang-System
4. **TODO 4** - Alert-Delay und Timing-Logik

### **⚡ Hoch (System-Integration):**
5. **TODO 5** - Template-System und Platzhalter
6. **TODO 7** - Integration mit Measurement-Controller
7. **TODO 9** - GUI-Integration vorbereiten

### **📋 Mittel (Robustheit):**
8. **TODO 6** - Error-Handling und Robustheit
9. **TODO 8** - Logging und Monitoring

### **📝 Niedrig (Usability):**
10. **TODO 10** - Vereinfachung und Konfiguration

**Die alert.py ist das kritische Modul für die E-Mail-Benachrichtigung und muss zuverlässig, aber einfach funktionieren.**

---

## 🔗 **Integration mit bestehender Codebase:**

- **Verwendet:** `EmailConfig`, `EmailTemplate`, `MeasurementConfig`
- **Integriert mit:** `Camera` für Bild-Capture, `MotionDetector` für Motion-Status
- **Bereitet vor:** GUI-Integration für Status-Anzeige und Test-Funktionen
- **Logging:** Nutzt das bestehende `LoggingConfig` System
- **Einfachheit:** Fokus auf einfache SMTP-Integration ohne komplexe Sicherheitsfeatures
"""