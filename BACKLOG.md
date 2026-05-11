# BookPublisher BACKLOG

Verbesserungen die den Agenten in Richtung 9,5/10 bringen.
Ein Item pro Run vollständig implementieren — kein Halbzeug.

## KRITISCH (direkter Qualitätssprung für den Autor)

- [x] **Kapitel-Analyse:** Jedes Kapitel einzeln bewerten — Versprechen, Beweis, Wert, Übergang. Output pro Kapitel: Score + konkrete Fix-Zeile. *(modules/chapters.py + chapter_review() in modules/review.py, wired into pipeline run_qa)*
- [x] **Konkrete Rewrite-Vorschläge:** Nicht "Titel zu kurz" sondern 3 alternative Titel mit Keyword-Score und Kaufmotivation. Gilt für Titel, Untertitel, Amazon-Description. *(modules/rewrites.py: build_rewrite_report() + render_rewrite_report_markdown(), pure-Python, in run_qa pipeline integriert — artifacts: rewrite_suggestions.md/.json)*
- [x] **Amazon-Description-HTML:** Generator der die KDP-Beschreibung in echtem Amazon-HTML ausgibt (`<b>`, `<br>`, Bullet-Liste) — copy-paste-fertig für KDP Backend. *(modules/amazon_html.py: build_amazon_description_html() + render_amazon_description_report_markdown(), HTML auf KDP-erlaubte Tag-Subset beschränkt + HTML-Escaping, in run_qa pipeline integriert — artifacts: amazon_description.html/.json + amazon_description_report.md)*
- [x] **7 KDP-Keywords konkret befüllen:** Statt "Keywords fehlen" → liefere die exakten 7 Keyword-Strings (max 50 Zeichen each) basierend auf Titel, Subtitle, Beschreibung. *(modules/kdp_keywords.py: build_kdp_keywords() + render_kdp_keywords_report_markdown(), pure-Python, KDP-Regeln durchgesetzt: ≤50 Zeichen, lowercase, Umlaute zu ae/oe/ue/ss, keine subjektiven/Marketing-Tokens, keine Titel-Wiederholung. In run_qa pipeline integriert — artifacts: kdp_keywords.md/.json)*
- [x] **First-10%-Deep-Scan:** Die ersten 10% des Manuskripts sind der Kindle-Sample. Bewerte jeden Abschnitt: Hält der Leser durch oder bricht er ab? Hook-Stärke, Versprechen-Klarheit, erster konkreter Wert. *(modules/sample_scan.py: build_sample_scan_report() + render_sample_scan_markdown(), pure-Python, vier Achsen Hook/Versprechen/Wert/Lesbarkeit, Abbruch-Risiko-Ampel pro Abschnitt. In run_qa pipeline integriert — artifacts: sample_scan.md/.json)*
- [x] **Runden-Gedächtnis mit Delta:** In Runde 2+ prüfen ob die Fixes aus Runde 1 umgesetzt wurden. *(modules/round_delta.py: RoundDelta + compute_round_delta() + render_round_delta_markdown(); AgentMemory.compare_rounds() in modules/agent_core.py; in run_qa pipeline integriert — artifacts: round_delta.md/.json. Klassifiziert Fixes als erledigt / persistent / neu und zeigt Score- + Investor-Grade-Delta inkl. Decision-Wechsel.)*

## WICHTIG (professionelle Qualität)

- [x] **LLM-Fallback:** Wenn claude-sonnet-4-6 fehlschlägt → claude-haiku-4-5-20251001 als automatischer Fallback mit Logging. *(modules/llm.py: `_call_model()` als testbarer Helper extrahiert, `complete()` versucht primaries Modell, fällt bei Exception automatisch auf `config.fallback_model` zurück, loggt `model_call_started`/`model_call_error`/`model_fallback_started`/`model_fallback_completed`. Bei doppeltem Fehlschlag ein klarer `ConfigError` der beide Modelle und beide Fehlertexte nennt — kein Traceback für den End-User. Edge Cases: kein Fallback konfiguriert, Fallback identisch mit primary, explizites Modell-Argument als Override — alle abgedeckt durch tests/test_llm.py.)*
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
- [ ] **Sample-Scan-Top-Risiko in beginner_summary:** Schwächsten Sample-Abschnitt (höchstes Abbruch-Risiko) inkl. Fix-Zeile direkt in beginner_summary.md zeigen, damit der Autor sofort weiss, wo der Kindle-Leser bricht.
- [ ] **Sample-Scan mit LLM-Pass:** Aktuelle Bewertung ist heuristisch (Regex-Marker). Optional eine LLM-Variante, die jedes Sample-Stück liest und einen Rewrite des Eröffnungssatzes vorschlägt — schaltbar via AppConfig.
- [ ] **Sample-Ratio Konfig:** SAMPLE_RATIO (10%) und MAX_SECTIONS in AppConfig auslagern, damit Autoren mit kurzen Sachbüchern den Scan-Umfang erweitern können.
- [ ] **Round-Delta in beginner_summary:** Top-Highlight (z.B. "3 Fixes erledigt, Score +15") direkt in beginner_summary.md aufnehmen, damit der Autor in der zweiten Runde sofort den Fortschritt sieht.
- [ ] **Round-Delta mit LLM-Pass:** Optional eine LLM-Variante die persistente Fixes interpretiert ("warum bleibt dieser Fix offen — fehlt Zeit, Verständnis oder Material?") — schaltbar via AppConfig.
- [ ] **LLM-Fallback Metrik in beginner_summary:** Wenn in einem Lauf das Fallback-Modell verwendet wurde, eine kurze Notiz in beginner_summary.md aufnehmen ("⚠️ Primärmodell `claude-sonnet-4-6` war nicht erreichbar — Bewertung kam von `claude-haiku-4-5-20251001`"), damit der Autor die niedrigere Tiefe einordnen kann.
- [ ] **LLM-Retry mit Backoff vor Fallback:** Aktuell schaltet `complete()` bei der ersten Exception sofort auf das Fallback-Modell um. Transientes Rate-Limit oder Timeout könnte vor dem Modellwechsel 1× retryt werden (exponential backoff, max 2 Versuche pro Modell), bevor der Fallback einspringt.
