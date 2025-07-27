# Programmbeschreibung: simple Python-Anwendung mit NiceGUI zur Webcam-Überwachung und Bewegungserkennung

## 1. Webcam-Steuerung und Anzeige

- Das Programm soll einen Webcam-Stream auslesen und diesen in einer grafischen Benutzeroberfläche (GUI) anzeigen.
- Folgende UVC-Kameraeinstellungen sollen über die GUI angepasst werden:
  - Helligkeit (Brightness)
  - Farbton (Hue)
  - Kontrast (Contrast)
  - Sättigung (Saturation)
  - Schärfe (Sharpness)
  - Gamma
  - Weißabgleich (White Balance, manuell/automatisch)
  - Verstärkung (Gain)
  - Gegenlichtkompensation (Backlight Compensation)
  - Belichtung (Exposure, manuell/automatisch)

## 2. Bewegungserkennung

- Der Live-Stream der Webcam soll kontinuierlich auf Bewegungen analysiert werden.
- Das Bewegungsergebnis (Bewegung erkannt / keine Bewegung) soll in der GUI als Status angezeigt werden.
- Eine Region of Interest (ROI) soll definiert werden können, um den Analysebereich gezielt einzuschränken und Fehlalarme zu minimieren.
- Die Sensitivität der Bewegungserkennung soll über die GUI einstellbar sein.

## 3. Messungssteuerung und E-Mail-Benachrichtigung

- Messungen (Überwachungszeiträume) sollen über die GUI gestartet und gestoppt werden können.
- Während einer laufenden Messung soll bei anhaltender Bewegungslosigkeit (keine Bewegung für eine einstellbare Zeit, „Alert-Delay“) automatisch eine E-Mail-Benachrichtigung ausgelöst werden.
- Die E-Mail enthält:
  - Einen Zeitstempel (Datum und Uhrzeit des Ereignisses)
  - Link zu Website
  - nicetohave: aktuelles Webcambild (optional)
- Es sollen mehrere E-Mail-Empfänger hinterlegt werden, die im Alarmfall gleichzeitig benachrichtigt werden.

## 4. Technische Umsetzung

- Die gesamte Benutzerinteraktion und Visualisierung erfolgt über NiceGUI.
- Die Steuerung der Kameraeinstellungen erfolgt über UVC-kompatible Schnittstellen.
- Die Bewegungserkennung basiert auf Bildverarbeitung (mit OpenCV).
- Der E-Mail-Versand erfolgt automatisiert und unterstützt mehrere Empfänger. Da die E-Mails nur in einem gekapselten Intranet versendet werden sind weitere Sicherheitsmaßnahmen (SMTP-Passwort, ssl, user-Passwort) nicht zu ergreifen.
