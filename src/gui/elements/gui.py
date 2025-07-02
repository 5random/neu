#TODO
"""
# 📋 **Ausführliche TODO-Liste für gui.py**

## 🔍 **Aktuelle Analyse der gui.py**

Die `gui.py` ist **komplett leer** und muss vollständig implementiert werden. Basierend auf der Projektbeschreibung und der bestehenden #codebase ist dies das **zentrale UI-Modul** für die Benutzerinteraktion.

**Erforderliche Funktionalitäten aus der Projektbeschreibung:**
- ✅ Webcam-Stream-Anzeige
- ✅ UVC-Kameraeinstellungen (alle 10 Parameter)
- ✅ Bewegungserkennung-Status und -Steuerung
- ✅ ROI-Definition und -Bearbeitung
- ✅ Messungssteuerung (Start/Stop)
- ✅ E-Mail-Empfänger-Verwaltung
- ✅ "Möglichst einfaches und simples Programm"

---

## 🎯 **TODO 1: Haupt-GUI-Klasse und Layout-Struktur definieren**

### **Problem:**
Keine GUI-Architektur vorhanden. NiceGUI-Integration muss von Grund auf aufgebaut werden.

### **Was zu tun ist:**
1. **CVDTrackerGUI Hauptklasse erstellen:**
   - Initialisierung mit `AppConfig`
   - Integration von `Camera`, `MotionDetector` (zukünftig)
   - State-Management für GUI-Status

2. **Grundlegendes Layout definieren:**
   - **Links:** Video-Stream-Anzeige (Hauptbereich)
   - **Rechts:** Steuerungsbereich mit Tabs/Accordion-Struktur
   - **Unten:** Status-Leiste mit aktuellen Informationen

3. **Responsive Design:**
   - Automatische Größenanpassung bei verschiedenen Bildschirmgrößen
   - Minimale Fenstergröße für Usability definieren
   - Skalierbare Icons und Schriftgrößen

---

## 🎯 **TODO 2: Video-Stream-Integration implementieren**

### **Problem:**
NiceGUI muss den Live-Stream der `Camera` anzeigen.

### **Was zu tun ist:**
1. **Video-Element erstellen:**
   - Integration mit `Camera` über FastAPI-Endpoint
   - Automatische Stream-URL-Generierung
   - Fallback auf Placeholder-Bild bei Kamera-Fehlern

2. **Stream-Kontrollen hinzufügen:**
   - Play/Pause-Button für Stream
   - Vollbild-Modus für Video
   - Zoom-Funktionalität (falls gewünscht)

3. **Performance-Optimierung:**
   - Frame-Rate-Begrenzung für GUI-Performance
   - Adaptive Qualität bei langsamer Verbindung
   - Pufferung für flüssige Wiedergabe

---

## 🎯 **TODO 3: UVC-Kamerasteuerung-GUI implementieren**

### **Problem:**
Alle 10 UVC-Parameter aus der Projektbeschreibung müssen über die GUI steuerbar sein.

### **Was zu tun ist:**
1. **UVC-Parameter-Panel erstellen:**
   - **Slider für kontinuierliche Werte:**
     - Helligkeit (Brightness)
     - Kontrast (Contrast)
     - Sättigung (Saturation)
     - Farbton (Hue)
     - Schärfe (Sharpness)
     - Gamma
     - Verstärkung (Gain)
     - Gegenlichtkompensation (Backlight Compensation)

2. **Auto/Manual-Steuerung implementieren:**
   - **Weißabgleich (White Balance):**
     - Toggle-Switch für Auto/Manual
     - Slider für manuelle Werte (nur bei Manual aktiv)
   - **Belichtung (Exposure):**
     - Toggle-Switch für Auto/Manual
     - Slider für manuelle Belichtungszeit

3. **Parameter-Synchronisation:**
   - Live-Updates bei Slider-Änderungen
   - Rückmeldung der tatsächlich gesetzten Werte
   - Reset-Button für Standard-Werte

---

## 🎯 **TODO 4: Bewegungserkennung-GUI implementieren**

### **Problem:**
Bewegungserkennung-Status und -Steuerung müssen visualisiert werden.

### **Was zu tun ist:**
1. **Motion-Status-Anzeige:**
   - **Echtzeit-Status-Indikator:**
     - Grün: Bewegung erkannt
     - Rot: Keine Bewegung
     - Grau: Bewegungserkennung inaktiv
   - **Textuelle Status-Anzeige** mit Zeitstempel der letzten Bewegung

2. **Sensitivitäts-Steuerung:**
   - **Slider für Sensitivität** (0.001 - 1.0)
   - **Preset-Buttons:** "Niedrig", "Mittel", "Hoch"
   - **Live-Anzeige** der aktuellen Empfindlichkeit

3. **Motion-History-Visualisierung:**
   - **Mini-Chart** der letzten Bewegungsereignisse
   - **Aktivitäts-Indikator** für die letzten Minuten
   - **Performance-Anzeige** (FPS, Verarbeitungszeit)

---

## 🎯 **TODO 5: ROI (Region of Interest) Editor implementieren**

### **Problem:**
Benutzer müssen eine ROI definieren können, um Fehlalarme zu minimieren.

### **Was zu tun ist:**
1. **ROI-Steuerung:**
   - **Enable/Disable Toggle** für ROI
   - **Koordinaten-Eingabe:** X, Y, Breite, Höhe
   - **Reset-Button** für Vollbild-ROI

2. **Visueller ROI-Editor:**
   - **Overlay auf Video-Stream** mit ROI-Rechteck
   - **Drag-and-Drop** Funktionalität für ROI-Anpassung
   - **Resize-Handles** an den Ecken des Rechtecks

3. **ROI-Validierung:**
   - **Automatische Größenbegrenzung** auf Video-Dimensionen
   - **Mindestgröße** für sinnvolle Bewegungserkennung
   - **Live-Preview** der ROI-Auswirkung

---

## 🎯 **TODO 6: Messungssteuerung-Interface implementieren**

### **Problem:**
Überwachungszeiträume müssen gestartet und gestoppt werden können.

### **Was zu tun ist:**
1. **Messungssteuerung:**
   - **Start/Stop-Button** (großer, prominenter Button)
   - **Session-Timer** mit aktueller Laufzeit
   - **Status-Anzeige:** "Aktiv", "Gestoppt", "Warten auf Bewegung"

2. **Alert-Delay-Konfiguration:**
   - **Eingabefeld** für Alert-Delay in Sekunden
   - **Live-Countdown** bis zum nächsten Alert
   - **Warnung** bei sehr kurzen oder langen Delays

3. **Messung-Historie:**
   - **Liste der letzten Messungen** mit Dauer und Ergebnis
   - **Export-Funktion** für Messdaten
   - **Statistiken** (Durchschnittliche Messzeit, Alert-Rate)

---

## 🎯 **TODO 7: E-Mail-System-GUI implementieren**

### **Problem:**
Mehrere E-Mail-Empfänger müssen verwaltet und konfiguriert werden.

### **Was zu tun ist:**
1. **E-Mail-Empfänger-Verwaltung:**
   - **Liste aktueller Empfänger** mit Add/Remove-Funktionalität
   - **E-Mail-Validierung** beim Hinzufügen
   - **Test-E-Mail-Funktion** für jeden Empfänger

2. **E-Mail-Template-Editor:**
   - **Betreff-Feld** mit Platzhaltern ({timestamp}, {camera_index})
   - **Nachrichtentext-Editor** mit Vorschau
   - **Bildanhang-Optionen** (Format, Qualität)

3. **SMTP-Konfiguration:**
   - **Server/Port-Eingabe** (einfach, da keine Sicherheit erforderlich)
   - **Test-Verbindung-Button**
   - **E-Mail-Historie** der versendeten Nachrichten

---

## 🎯 **TODO 8: Status-System und Feedback implementieren**

### **Problem:**
Benutzer brauchen Feedback über den aktuellen System-Status.

### **Was zu tun ist:**
1. **Status-Leiste erstellen:**
   - **Kamera-Status:** Verbunden/Getrennt, Auflösung, FPS
   - **Motion-Status:** Aktiv/Inaktiv, letzte Bewegung
   - **System-Status:** CPU-Last, Speicherverbrauch

2. **Notification-System:**
   - **Toast-Nachrichten** für wichtige Events
   - **Error-Alerts** bei Problemen
   - **Success-Bestätigungen** bei Aktionen

3. **Log-Viewer:**
   - **Mini-Log-Fenster** mit den letzten Systemmeldungen
   - **Filter-Optionen** nach Log-Level
   - **Export-Funktion** für Logs

---

## 🎯 **TODO 9: Vereinfachung und Usability für "einfaches Programm"**

### **Problem:**
GUI muss trotz vieler Features einfach und intuitiv bleiben.

### **Was zu tun ist:**
1. **Intelligente Defaults:**
   - **Auto-Konfiguration** beim ersten Start
   - **Sinnvolle Standardwerte** für alle Parameter
   - **Wizard-Modus** für Erst-Setup

2. **Progressive Disclosure:**
   - **Basic/Advanced-Modi** mit unterschiedlicher Komplexität
   - **Accordion/Tab-Layout** für übersichtliche Gruppierung
   - **Hilfe-Tooltips** bei allen wichtigen Elementen

3. **One-Click-Funktionen:**
   - **Quick-Start-Button** für sofortige Überwachung
   - **Preset-Profile** für typische Anwendungsfälle
   - **Import/Export** von Konfigurationen

---

## 🎯 **TODO 10: Error-Handling und Robustheit**

### **Problem:**
GUI muss robust gegen Benutzer-Fehler und System-Probleme sein.

### **Was zu tun ist:**
1. **Input-Validierung:**
   - **Echtzeit-Validierung** aller Eingabefelder
   - **Range-Begrenzung** für numerische Werte
   - **Format-Prüfung** für E-Mail-Adressen

2. **Graceful Degradation:**
   - **Fallback-UI** bei Kamera-Ausfall
   - **Offline-Modus** bei Netzwerk-Problemen
   - **Read-Only-Modus** bei kritischen Fehlern

3. **Recovery-Mechanismen:**
   - **Auto-Reconnect** bei Verbindungsverlust
   - **Configuration-Backup** vor Änderungen
   - **Safe-Mode** mit minimaler Funktionalität

---

## 📊 **Prioritäts-Reihenfolge für die Umsetzung:**

### **🔥 Kritisch (Basis-Funktionalität):**
1. **TODO 1** - Haupt-GUI-Klasse und Layout
2. **TODO 2** - Video-Stream-Integration
3. **TODO 3** - UVC-Kamerasteuerung
4. **TODO 6** - Messungssteuerung

### **⚡ Hoch (Kern-Features):**
5. **TODO 4** - Bewegungserkennung-GUI
6. **TODO 5** - ROI-Editor
7. **TODO 7** - E-Mail-System-GUI

### **📋 Mittel (Usability):**
8. **TODO 8** - Status-System und Feedback
9. **TODO 9** - Vereinfachung und Usability

### **📝 Niedrig (Robustheit):**
10. **TODO 10** - Error-Handling und Robustheit

**Die gui.py ist das Herzstück der Benutzerinteraktion und muss alle Funktionen der Projektbeschreibung in einer einfachen, intuitiven Oberfläche vereinen.**

---

## 🔗 **Integration mit bestehender Codebase:**

- **Verwendet:** `AppConfig`, `WebcamConfig`, `UVCConfig`, `MotionDetectionConfig`
- **Integriert mit:** `Camera` für Video-Stream und UVC-Steuerung
- **Bereitet vor:** `MotionDetector` (noch zu implementieren), Alert-System
- **Framework:** NiceGUI für Web-basierte GUI
- **Einfachheit:** Fokus auf intuitive Bedienung trotz umfangreicher Funktionalität
"""