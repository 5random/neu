"""
[ Kopfzeile / Titel der Anwendung ]

---------------------------------------------------------------------------------------------------------------------
|                                |                                                                                  |
|   [ Webcam-Stream Bereich ]    |   [ AKKORDEON: Kameraeinstellungen ]                                             |
|   (Groß, zentral)              |       - Gruppe: Bildqualität                                                     |
|                                |           - Radioelemente (Helligkeit, Kontrast, Sättigung, Schärfe) + Wertanzeige|
|                                |       - Gruppe: Belichtung & Weißabgleich                                        |
|                                |           - Toggle (Weißabgleich Auto/Manuell)                                   |
|                                |           - Radioelement (Weißabgleich Manuell) - nur sichtbar bei "Manuell"     |
|                                |           - Toggle (Belichtung Auto/Manuell)                                     |
|                                |           - Radioelement (Belichtung Manuell) - nur sichtbar bei "Manuell"       |
|                                |           - Radioelement (Gamma, Gain, Backlight Comp.) + Wertanzeige            |
|                                |       - Button: "Standardwerte zurücksetzen"                                     |
|--------------------------------|----------------------------------------------------------------------------------|      
|   [ Statusanzeige ]            |                                                                                  |
|   (Großer Text + Icon)         |   [ AKKORDEON: Bewegungserkennung ]                                              |
|   "Bewegung erkannt!" (Rot)    |       - Slider (Sensitivität) + Wertanzeige                                      |
|   "Keine Bewegung" (Grün/Grau) |       - Button: "ROI bearbeiten" (aktiviert Overlay-Modus im Stream)             |
|--------------------------------|           - (Im ROI-Modus: Button "ROI speichern", "Abbrechen")                  |
|                                |       - Tooltip: Was ist Sensitivität?                                           |
|   [ MESSUNGSTOOL ]             |----------------------------------------------------------------------------------|
|   - Button "Messung starten/stoppen"   |                                                                          |
|                                |   [ AKKORDEON: E-Mail-Benachrichtigungen ]                                       |
|   - Input/Slider "Messdauer (Minuten)" |       - Input: "Alert-Delay (Sekunden)" + Tooltip                        |
|   - Fortschrittsanzeige:       |       - Gruppe: Empfänger-Liste                                                  |
|     "Messung läuft: X von Y Min" |           - Input: "Neue E-Mail-Adresse" (mit Validierung)                     |
|     (Optional: Fortschrittsbalken) |           - Button: "Hinzufügen"                                             |
|   - Anzeige "Letzter Alarm: [Zeitstempel]" |           - Liste der E-Mail-Empfänger (mit "X" zum Entfernen)       |
|                                |       - Button: "Test-E-Mail senden" (mit Erfolgs-/Fehlermeldung)                |
|                                |       - Input: "SMTP Host"                                                       |
|                                |       - Input: "SMTP Port"                                                       |
|-------------------------------------------------------------------------------------------------------------------|
|   [ Footer mit App-Info/Version ]                                                                                 |
---------------------------------------------------------------------------------------------------------------------
"""
"""
Frontend-Skizze für Webcam-Überwachung GUI mit NiceGUI
Nur Layout und Benutzeroberfläche - keine Backend-Logik
"""
