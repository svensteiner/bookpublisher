# Release-Distribution

Dieser Ordner enthält die Dateien, die in das Customer-Download-ZIP
(`BookPublisher.zip` auf der Homepage) gepackt werden.

## Inhalt

```
release/
├── beispielbuch/                    # Cross-Selling-Beispielbuch
│   ├── Unter_Fuenfzig_Euro.docx     # Echtes Manuskript des Autors
│   ├── cover.jpg                    # Echtes KDP-Cover (1600x2560)
│   ├── metadata.md                  # KDP-Titel, Untertitel, Beschreibung, Amazon-Link
│   └── LIES_MICH.txt                # 4-Schritte-Anleitung für den Kunden
└── README.md                        # Diese Datei
```

## Customer Journey

Wenn ein Kunde `BookPublisher.zip` von der Homepage runterlädt, sieht er:

1. **Entpacken auf den Desktop.**
2. **Doppelklick** auf `BookPublisher.exe` (oder `BookPublisher starten.bat`).
3. **Ordner wählen → `beispielbuch/`** auswählen.
4. **„Prüfrunde starten"** klicken (Schnellmodus, kein API-Key nötig).
5. **`beginner_summary.md`** öffnet sich automatisch → Ampel + nächste Schritte.

Der Kunde sieht beim allerersten Test das echte Buch des Entwicklers,
inklusive Amazon-Kauflink in der `metadata.md`. Damit verkauft das
Werkzeug das Buch und das Buch das Werkzeug.

## Aktualisierung

Vor jedem neuen Release:

1. Aktuelle `*.docx` aus deinem Endversions-Ordner nach
   `release/beispielbuch/Unter_Fuenfzig_Euro.docx` kopieren.
2. `metadata.md` prüfen — wenn sich Titel/Untertitel/Beschreibung
   geändert haben, anpassen.
3. `tests/test_customer_journey.py` ausführen → muss grün sein.
4. EXE bauen: `scripts\build_windows_app.bat`.
5. Release-ZIP bauen: `scripts\build_release_zip.bat`. Das Skript
   schreibt `dist\BookPublisher.zip` mit der EXE (falls vorhanden),
   `beispielbuch/`, `BookPublisher starten.bat` und einer kurzen
   `LIES_MICH.txt` auf Top-Level. Genau diese Datei wandert dann
   auf die Homepage.

Der Customer-Journey-Test in `tests/test_customer_journey.py` läuft
gegen genau diesen Ordner und stellt sicher, dass der Kunde nach
dem Doppelklick keine Fehlermeldung sieht.
