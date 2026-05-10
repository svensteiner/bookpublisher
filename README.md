# BookPublisher Agent

Production-oriented Publisher Agent for German Amazon KDP nonfiction business books.

The agent prepares, reviews, scores, documents, and packages publishing assets. It never publishes, uploads, deletes, emails, or posts anything.

## Fuer Nicht-Technische Nutzer

On Windows, double-click:

```text
BookPublisher starten.bat
```

Dann:

1. Buchordner mit DOCX-Manuskript, Cover und Metadaten auswaehlen.
2. Schnelle Pruefrunde starten.
3. FIX/REVIEW-Punkte im Fenster lesen.
4. Buchdateien im Ordner anpassen.
5. Naechste Pruefrunde starten.

Jede Runde wird hier archiviert:

```text
artifacts\rounds\<project_id>\round_YYYYMMDD_HHMMSS\
```

Die schnelle Runde nutzt keine OpenAI API. Der Vollreview-Modus nutzt den konfigurierten OpenAI-Key.
Die GUI zeigt zuerst `beginner_summary.md`: eine einfache Ampel mit den naechsten konkreten Schritten und der betroffenen Datei.

Optional kann eine Desktop-Verknuepfung erstellt werden:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_shortcut.ps1
```

## What It Does

- Scans the KDP endversion folder for book projects.
- Detects manuscript, cover, title, subtitle, author, metadata, and missing assets.
- Reviews manuscript strength, Amazon conversion, voice preservation, cover readiness, and launch readiness.
- Runs a major-publisher style editorial board review across acquisition, developmental editing, line quality, production, Kindle ebook mechanics, metadata, sales, and launch.
- Checks Kindle sellability: first 10% sample strength, Look Inside flow, reflow-friendly structure, clickable TOC expectations, mobile readability, keywords/categories, and post-purchase review risk.
- Adds an industrial QA gate that runs without LLM calls and produces release decisions, machine-readable scores, and blocking fixes.
- Loads modular publishing skills from `skills/`.
- Maintains persistent agent memory in `artifacts/agent_memory.json` and snapshots it per review round.
- Generates German launch assets in the author's voice.
- Writes all outputs only to `artifacts/`.
- Logs every run to `logs/run_YYYYMMDD_HHMMSS.jsonl`.

## Install

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python -m venv .venv
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && .venv\Scripts\activate && pip install -r requirements.txt
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && copy .env.example .env
```

Open `.env` and add:

```text
ANTHROPIC_API_KEY=sk-ant-...
```

Get a key at https://console.anthropic.com → API Keys. The quick QA gate (`python main.py qa`) runs without any API key.

## Run

Default input folder: select via GUI or pass as argument. No hardcoded path required.

Commands:

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py scan
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py qa
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py round
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py review
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py cover
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py launch
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py all
```

Override input path:

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py all --input-path "C:\Path\To\Another\BookFolder"
```

Full review round with OpenAI:

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py round --full-review
```

## Outputs

Main output folder:

```text
C:\Automatisierungen\github projekte\bookpublisher\artifacts
```

For one detected project, key files are mirrored directly into `artifacts/`:

- `discovery_report.md`
- `discovery_report.json`
- `industrial_qa_report.md`
- `industrial_qa_report.json`
- `beginner_summary.md`
- `kindle_preview_check.md`
- `amazon_research_brief.md`
- `competitor_research_template.csv`
- `latest_round_summary.json`
- `manuscript_review.md`
- `manuscript_score.json`
- `voice_preservation_report.md`
- `amazon_conversion_review.md`
- `publisher_board_review.md`
- `cover_review.md`
- `kdp_publish_checklist.md`
- `launch_content.md`
- `final_publisher_summary.md`

For multiple projects, each project also gets:

```text
artifacts\<project_id>\
```

## Safety

Default mode is read-only analysis. The agent writes only to:

- `artifacts/`
- `logs/`

It does not modify manuscripts, covers, metadata, or source files.

## Industrial QA

`python main.py qa` is the production gate. It does not call the OpenAI API. It checks:

- required production assets
- Amazon metadata and storefront readiness
- Kindle ebook mechanics: reflow, headings, first 10%, tables, images, TOC signal
- cover production size, ratio, thumbnail contrast, and edge risk
- sellability markers: target reader, proof, anti-hype positioning, practical payoff

The output decision is:

- `GO`: ready for upload after normal human preview
- `GO_AFTER_FIXES`: commercially usable after listed fixes
- `HOLD`: blocking production issue

## Amazon Research And Kindle Preview

The agent does not scrape Amazon. It creates a manual research pack instead:

- `amazon_research_brief.md`
- `competitor_research_template.csv`

This keeps the workflow robust and lets a non-technical user copy visible Amazon data into a simple table.

The agent also writes:

- `kindle_preview_check.md`

It detects common Kindle Previewer install paths and lists the exact manual checks before upload.

## Build Windows App

For a distributable Windows folder app:

```bat
python -m pip install -r requirements-dev.txt
scripts\build_windows_app.bat
```

Output:

```text
dist\BookPublisher\BookPublisher.exe
```

## Skills And Memory

The agent now follows a template-style structure:

- `skills/major_publisher_board.yaml`
- `skills/kindle_ebook_production.yaml`
- `skills/amazon_storefront_conversion.yaml`
- `skills/voice_preservation.yaml`

These skills are loaded at runtime and included in QA/review context. The persistent memory file is:

```text
artifacts\agent_memory.json
```

It remembers project facts, previous QA decisions, scores, and open risks. Each round also writes:

```text
agent_memory_snapshot.json
```

## Tests

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && pytest
```
