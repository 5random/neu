#TODO
"""
# 📋 **Ausführliche TODO-Liste für motion.py**

## 🔍 **Aktuelle Analyse der motion.py**

Die `motion.py` ist **komplett leer** und muss vollständig implementiert werden. Basierend auf der Projektbeschreibung und der bestehenden #codebase ist dies ein **kritisches Kernmodul** für die Bewegungserkennung.

**Erforderliche Funktionalitäten aus der Projektbeschreibung:**
- ✅ Kontinuierliche Bewegungsanalyse des Live-Streams
- ✅ Status-Rückgabe (Bewegung erkannt / keine Bewegung)
- ✅ ROI (Region of Interest) Support
- ✅ Einstellbare Sensitivität
- ✅ Integration mit Alert-System für E-Mail-Benachrichtigung

---

## 🎯 **TODO 1: Grundlegende Datenstrukturen definieren**

### **Problem:**
Es gibt keine Datenstrukturen für Bewegungsergebnisse und Motion-Status.

### **Was zu tun ist:**
1. **MotionResult Dataclass erstellen:**
   - `motion_detected: bool` - Hauptstatus der Bewegungserkennung
   - `contour_area: float` - Größe der erkannten Bewegung
   - `timestamp: float` - Zeitstempel der Erkennung
   - `confidence: float` - Konfidenz der Erkennung (0.0-1.0)
   - `roi_used: bool` - Ob ROI verwendet wurde

2. **MotionStatus Enum definieren:**
   - `NO_MOTION` - Keine Bewegung erkannt
   - `MOTION_DETECTED` - Bewegung erkannt
   - `MOTION_TIMEOUT` - Bewegung zu lange ausgeblieben (für Alert-System)

3. **MotionMetrics Dataclass für Debugging:**
   - `frame_count: int` - Anzahl verarbeiteter Frames
   - `avg_processing_time: float` - Durchschnittliche Verarbeitungszeit
   - `false_positive_rate: float` - Geschätzte Falsch-Positiv-Rate

---

## 🎯 **TODO 2: Haupt-MotionDetector Klasse implementieren**

### **Problem:**
Keine Bewegungserkennungs-Engine vorhanden.

### **Was zu tun ist:**
1. **Konstruktor definieren:**
   - Parameter: `MotionDetectionConfig` aus der bestehenden Config
   - Initialisierung des Background Subtractors (MOG2 oder KNN)
   - ROI-Setup aus Config
   - Logger-Integration

2. **Kern-Eigenschaften implementieren:**
   - `background_subtractor` - OpenCV BackgroundSubtractor
   - `roi` - ROI-Objekt aus Config
   - `sensitivity` - Sensitivitätsfaktor
   - `min_contour_area` - Minimale Kontur-Größe für gültige Bewegung
   - `frame_buffer` - Ringpuffer für die letzten N Frames (optional)

3. **State-Management:**
   - `last_motion_time` - Zeitstempel der letzten Bewegung
   - `motion_history` - Liste der letzten Bewegungsergebnisse
   - `is_learning` - Background-Learning Status

---

## 🎯 **TODO 3: Bewegungserkennung-Algorithmus implementieren**

### **Problem:**
Kein Algorithmus für die eigentliche Bewegungserkennung vorhanden.

### **Was zu tun ist:**
1. **Haupt-Erkennungsmethode: `detect_motion(frame)`:**
   - Frame-Preprocessing (Größenanpassung, Farbkonvertierung)
   - ROI-Extraktion falls aktiviert
   - Background Subtraction anwenden
   - Rauschunterdrückung (Morphological Operations)
   - Kontur-Erkennung und -Filterung
   - Bewegungsentscheidung basierend auf Kontur-Größe

2. **ROI-Handling implementieren:**
   - Methode `_apply_roi(frame)` für ROI-Extraktion
   - ROI-Koordinaten-Validierung
   - Fallback auf Vollbild bei ungültiger ROI

3. **Sensitivitäts-Anpassung:**
   - Dynamische Anpassung der `min_contour_area` basierend auf Sensitivität
   - Lerning-Rate-Anpassung für Background Subtractor
   - Adaptive Schwellwert-Berechnung

---

## 🎯 **TODO 4: Integration mit bestehender Konfiguration**

### **Problem:**
Die Motion-Detection muss nahtlos mit `MotionDetectionConfig` arbeiten.

### **Was zu tun ist:**
1. **Config-Integration implementieren:**
   - ROI aus `config.get_roi()` übernehmen
   - Sensitivität aus `config.sensitivity` anwenden
   - Learning-Rate aus `config.background_learning_rate` verwenden
   - Min-Kontur-Area aus `config.min_contour_area` übernehmen

2. **Dynamische Konfiguration:**
   - Methoden für Live-Änderung der Sensitivität: `update_sensitivity(value)`
   - ROI-Update-Methode: `update_roi(roi_config)`
   - Config-Reload ohne Neustart: `reload_config(config)`

3. **Validierung:**
   - ROI-Validierung gegen aktuelle Frame-Größe
   - Sensitivitäts-Range-Prüfung (0.001-1.0)
   - Min-Contour-Area Plausibilitätsprüfung

---

## 🎯 **TODO 5: Performance-Optimierung für "einfaches Programm"**

### **Problem:**
Bewegungserkennung kann CPU-intensiv sein - für ein "einfaches Programm" sollte es optimiert sein.

### **Was zu tun ist:**
1. **Frame-Skalierung implementieren:**
   - Verkleinerung der Frames für schnellere Verarbeitung
   - Konfigurierbare Skalierungsfaktoren
   - Qualitäts-vs-Performance Balance

2. **Smart Processing:**
   - Frame-Skipping bei hoher CPU-Last
   - Adaptive Verarbeitungsqualität basierend auf System-Performance
   - Bewegungserkennung nur in definierten Intervallen

3. **Speicher-Management:**
   - Effiziente Frame-Buffer-Verwaltung
   - Garbage Collection für alte Bewegungsdaten
   - Maximale Memory-Limits definieren

---

## 🎯 **TODO 6: Alert-System Integration vorbereiten**

### **Problem:**
Motion-Detection muss mit dem zukünftigen Alert-System für E-Mail-Benachrichtigung kommunizieren.

### **Was zu tun ist:**
1. **Timing-Integration:**
   - Tracking der Zeit seit letzter Bewegung
   - Integration mit `MeasurementConfig.alert_delay_seconds`
   - Methode `time_since_last_motion()` für Alert-System

2. **Status-Bereitstellung:**
   - Erweiterte Status-Informationen für GUI
   - Bewegungshistorie für Alert-Entscheidungen
   - Confidence-Level für Fehlalarm-Reduzierung

3. **Event-Callbacks vorbereiten:**
   - Callback-System für Bewegungsänderungen
   - Alert-Ready-Status für E-Mail-Trigger
   - Integration mit `Camera.motion_callback`

---

## 🎯 **TODO 7: GUI-Integration vorbereiten**

### **Problem:**
Die GUI muss Motion-Status anzeigen und Sensitivität ändern können.

### **Was zu tun ist:**
1. **Status-Export-Methoden:**
   - `get_current_status()` → aktueller Bewegungsstatus
   - `get_motion_history()` → Historie für GUI-Graphiken
   - `get_performance_metrics()` → FPS, CPU-Usage etc.

2. **Live-Parameter-Änderung:**
   - `set_sensitivity(value)` für GUI-Slider
   - `set_roi(x, y, width, height)` für interaktive ROI-Auswahl
   - `toggle_roi(enabled)` für ROI ein/aus

3. **Debugging-Support:**
   - Visualisierung der erkannten Konturen
   - ROI-Overlay für das Video-Display
   - Motion-Heatmap für Bewegungsverteilung

---

## 🎯 **TODO 8: Error-Handling und Robustheit**

### **Problem:**
Bewegungserkennung muss robust gegen verschiedene Eingaben und Fehler sein.

### **Was zu tun ist:**
1. **Input-Validation:**
   - Frame-Format-Prüfung (Farbtiefe, Größe)
   - Null-Frame-Handling
   - Korrupte Frame-Daten abfangen

2. **Background-Subtractor-Robustheit:**
   - Automatic-Reset bei zu vielen Fehlalarmen
   - Learning-Rate-Anpassung bei schlechter Performance
   - Fallback-Algorithmus bei Subtractor-Fehlern

3. **Resource-Management:**
   - Memory-Leak-Prevention
   - CPU-Überlastungs-Schutz
   - Graceful Degradation bei System-Überlastung

---

## 🎯 **TODO 9: Testing und Kalibrierung**

### **Problem:**
Bewegungserkennung muss kalibriert und getestet werden können.

### **Was zu tun ist:**
1. **Kalibrierungs-Modus:**
   - Automatische Sensitivitäts-Kalibrierung
   - Background-Learning-Periode definieren
   - ROI-Optimierung basierend auf typischen Bewegungsmustern

2. **Test-Modi:**
   - Simulation von Bewegung für Testing
   - Performance-Benchmarking
   - Falsch-Positiv/Negativ-Rate-Messung

3. **Logging und Debugging:**
   - Detaillierte Bewegungs-Logs
   - Frame-Export für Debugging
   - Konfigurationsprofil-Export/-Import

---

## 🎯 **TODO 10: Vereinfachung und Usability**

### **Problem:**
Für ein "einfaches Programm" sollte die Motion-Detection benutzerfreundlich und selbsterklärend sein.

### **Was zu tun ist:**
1. **Auto-Configuration:**
   - Intelligente Default-Werte basierend auf Kamera-Setup
   - Automatic-Tuning der Parameter
   - One-Click-Setup für Standard-Anwendungsfälle

2. **Benutzerfreundliche API:**
   - Einfache Enable/Disable-Funktionen
   - Preset-Modi (z.B. "Indoor", "Outdoor", "High Sensitivity")
   - Minimale Konfiguration für Standard-Use-Cases

3. **Dokumentation und Hilfe:**
   - Inline-Dokumentation für alle wichtigen Methoden
   - Beispiel-Konfigurationen für typische Anwendungen
   - Troubleshooting-Guides für häufige Probleme

---

## 📊 **Prioritäts-Reihenfolge für die Umsetzung:**

### **🔥 Kritisch (Kern-Funktionalität):**
1. **TODO 1** - Grundlegende Datenstrukturen
2. **TODO 2** - Haupt-MotionDetector Klasse
3. **TODO 3** - Bewegungserkennung-Algorithmus
4. **TODO 4** - Config-Integration

### **⚡ Hoch (System-Integration):**
5. **TODO 6** - Alert-System Integration
6. **TODO 7** - GUI-Integration vorbereiten
7. **TODO 8** - Error-Handling

### **📋 Mittel (Optimierung):**
8. **TODO 5** - Performance-Optimierung
9. **TODO 9** - Testing und Kalibrierung

### **📝 Niedrig (Usability):**
10. **TODO 10** - Vereinfachung und Usability

**Die motion.py ist das Herzstück der Bewegungserkennung und muss vollständig von Grund auf implementiert werden. Diese TODOs führen zu einem robusten, aber einfachen Motion-Detection-System.**

---

## 🔗 **Integration mit bestehender Codebase:**

- **Verwendet:** `MotionDetectionConfig`, `ROI` aus der Config
- **Integriert mit:** `Camera.motion_callback` für Frame-Verarbeitung
- **Bereitet vor:** Alert-System (noch zu implementieren) und GUI-System
- **Logging:** Nutzt das bestehende `LoggingConfig` System
"""