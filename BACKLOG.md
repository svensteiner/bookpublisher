# BookPublisher BACKLOG

Verbesserungen die den Agenten in Richtung 9,5/10 bringen.
Ein Item pro Run vollständig implementieren — kein Halbzeug.

## KRITISCH (direkter Qualitätssprung für den Autor)

- [x] **Kapitel-Analyse:** Jedes Kapitel einzeln bewerten — Versprechen, Beweis, Wert, Übergang. Output pro Kapitel: Score + konkrete Fix-Zeile. *(modules/chapters.py + chapter_review() in modules/review.py, wired into pipeline run_qa)*
- [x] **Konkrete Rewrite-Vorschläge:** Nicht "Titel zu kurz" sondern 3 alternative Titel mit Keyword-Score und Kaufmotivation. Gilt für Titel, Untertitel, Amazon-Description. *(modules/rewrites.py: build_rewrite_report() + render_rewrite_report_markdown(), pure-Python, in run_qa pipeline integriert — artifacts: rewrite_suggestions.md/.json)*
- [x] **Amazon-Description-HTML:** Generator der die KDP-Beschreibung in echtem Amazon-HTML ausgibt (`<b>`, `<br>`, Bullet-Liste) — copy-paste-fertig für KDP Backend. *(modules/amazon_html.py: build_amazon_description_html() + render_amazon_description_report_markdown(), HTML auf KDP-erlaubte Tag-Subset beschränkt + HTML-Escaping, in run_qa pipeline integriert — artifacts: amazon_description.html/.json + amazon_description_report.md)*
- [x] **7 KDP-Keywords konkret befüllen:** Statt "Keywords fehlen" → liefere die exakten 7 Keyword-Strings (max 50 Zeichen each) basierend auf Titel, Subtitle, Beschreibung. *(modules/kdp_keywords.py: build_kdp_keywords() + render_kdp_keywords_report_markdown(), pure-Python, KDP-Regeln durchgesetzt: ≤50 Zeichen, lowercase, Umlaute zu ae/oe/ue/ss, keine subjektiven/Marketing-Tokens, keine Titel-Wiederholung. In run_qa pipeline integriert — artifacts: kdp_keywords.md/.json)*
- [ ] **First-10%-Deep-Scan:** Die ersten 10% des Manuskripts sind der Kindle-Sample. Bewerte jeden Abschnitt: Hält der Leser durch oder bricht er ab? Hook-Stärke, Versprechen-Klarheit, erster konkreter Wert.
- [ ] **Runden-Gedächtnis mit Delta:** In Runde 2+ prüfen ob die Fixes aus Runde 1 umgesetzt wurden. AgentMemory.compare_rounds().

## WICHTIG (professionelle Qualität)

- [ ] **LLM-Fallback:** Wenn claude-sonnet-4-6 fehlschlägt → claude-haiku-4-5-20251001 als automatischer Fallback mit Logging.
- [ ] **Strukturierter Score-Verlauf:** JSON-Datei die über alle Runden den Industrial-Score trackt. Graph-fähig (Datum, Score, Top-3-Fixes). In artifacts/score_history.json.
- [ ] **Kapitel-Reihungscheck:** Prüfe ob die Kapitelreihenfolge dem klassischen Sachbuch-Bogen folgt (Problem → Lösung → Beweis → Transformation).
- [ ] **Competitive-Positioning-Prompt:** Basierend auf Titel + Beschreibung generiere "Was macht dieses Buch einzigartig vs. die Top-Wettbewerber in dieser Nische?" Als eigener Review-Abschnitt in publisher_board_review.
- [ ] **Leser-Persona-Generator:** Aus Titel + Beschreibung + Inhaltsverzeichnis automatisch 3 konkrete Buyer-Personas ableiten (Alter, Job, Problem, Kaufmotiv). Output in amazon_research_brief.md.

## POLISH (letzter Schliff)

- [ ] **Einheitliche Score-Darstellung:** Alle Gates nutzen dieselbe Skala und dasselbe Farbschema in beginner_summary.md (🟢 ≥85, 🟡 65–84, 🔴 <65).
- [ ] **.env.example aktualisieren:** ANTHROPIC_API_KEY dokumentieren + Kommentare welche Features welchen Key brauchen.
- [ ] **Fehler-Meldungen verbessern:** Wenn ein DOCX nicht gelesen werden kann → klarer Text welche Datei fehlt und was der User tun soll. Kein Python-Traceback für den End-User.

## Neu entdeckt (während Implementierung gefunden)

- [ ] **Kapitel-Scoring mit LLM verfeinern:** Aktuelle Bewertung ist heuristisch (Regex-Marker). Optional eine LLM-Pass-Variante für reichere Fix-Vorschläge — laufzeit-gesteuert über AppConfig.
- [ ] **Per-Kapitel Wortzähl-Balance:** Wenn ein Kapitel >3× das Median-Volumen hat, flaggen ("Kapitel X ist viermal länger als der Durchschnitt — splitten?").
- [ ] **Beginner-Summary erweitern:** Top-3 schwächste Kapitel mit ihrer Fix-Zeile direkt in beginner_summary.md übernehmen.
- [ ] **Rewrite-Varianten mit LLM verfeinern:** Aktuelle Varianten folgen festen Bestseller-Mustern. Optional eine LLM-Pass-Variante die das Original direkt umschreibt (statt Template) — schaltbar über AppConfig.
- [ ] **Rewrite-Top-Pick in beginner_summary:** Top-Variante (höchster Keyword-Score) automatisch in beginner_summary.md vorschlagen.
- [ ] **KDP-HTML Live-Preview im beginner_summary:** Kurze Vorschau der ersten 3 Zeilen (gerendert, ohne HTML-Tags) damit der Autor sieht, wie die Beschreibung auf Amazon aussieht.
- [ ] **Amazon-Description-HTML mit LLM-Pass:** Optional eine LLM-Variante, die statt Template-Bullets echte Highlight-Bullets aus dem Manuskript zieht — schaltbar via AppConfig.
- [ ] **KDP-Keywords mit LLM-Pass:** Aktuelle Slots sind Template-basiert. Optional eine LLM-Variante, die echte Long-Tail-Suchphrasen aus dem Manuskript ableitet — schaltbar via AppConfig.
- [ ] **KDP-Keywords-Top-3 in beginner_summary:** Die 3 stärksten Slots (z.B. subject_audience + audience_format + anchor_pair) direkt in beginner_summary.md als Copy-Paste-Block aufnehmen.
- [ ] **KDP-Keywords-Konflikt-Check:** Warnen, wenn ein Keyword-Slot mit der gewählten KDP-Kategorie überlappt (Amazon ignoriert solche Slots).
