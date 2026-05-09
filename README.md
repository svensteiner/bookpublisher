# BookPublisher Agent

Production-oriented Publisher Agent for German Amazon KDP nonfiction business books.

The agent prepares, reviews, scores, documents, and packages publishing assets. It never publishes, uploads, deletes, emails, or posts anything.

## What It Does

- Scans the KDP endversion folder for book projects.
- Detects manuscript, cover, title, subtitle, author, metadata, and missing assets.
- Reviews manuscript strength, Amazon conversion, voice preservation, cover readiness, and launch readiness.
- Runs a major-publisher style editorial board review across acquisition, developmental editing, line quality, production, Kindle ebook mechanics, metadata, sales, and launch.
- Checks Kindle sellability: first 10% sample strength, Look Inside flow, reflow-friendly structure, clickable TOC expectations, mobile readability, keywords/categories, and post-purchase review risk.
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
OPENAI_API_KEY=your_key_here
```

## Run

Default input folder:

```text
C:\Users\svens\OneDrive\Desktop\Buch für Amazon\AI_Studioxyz_KDP\endversion
```

Commands:

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py scan
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py review
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py cover
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py launch
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py all
```

Override input path:

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && python main.py all --input-path "C:\Path\To\Another\BookFolder"
```

## Outputs

Main output folder:

```text
C:\Automatisierungen\github projekte\bookpublisher\artifacts
```

For one detected project, key files are mirrored directly into `artifacts/`:

- `discovery_report.md`
- `discovery_report.json`
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

## Tests

```bat
cd /d "C:\Automatisierungen\github projekte\bookpublisher" && pytest
```
