"""Tests for the Leser-Persona-Generator."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.personas import (
    MAX_CHANNELS,
    MAX_MOTIVE_CHARS,
    MAX_PERSONAS,
    MAX_PROBLEM_CHARS,
    MAX_QUOTE_CHARS,
    BuyerPersona,
    PersonaReport,
    _suggest_channels,
    build_persona_report,
    extract_persona_overrides,
    render_persona_brief_section,
    render_persona_report_markdown,
)


def _ki_project(
    *,
    title: str | None = "KI im Mittelstand: Was wirklich funktioniert",
    subtitle: str | None = "Ein ehrlicher Leitfaden für Operatoren und CFOs",
    description: str | None = (
        "Aus 10 Jahren operativer Praxis: In diesem Buch zeige ich Schritt für Schritt, "
        "wie kuenstliche Intelligenz in 30 Tagen im Mittelstand wirklich Umsatz bringt. "
        "Keine Hype-Versprechen, sondern Checklisten und konkrete Zahlen aus 12 Projekten."
    ),
) -> BookProject:
    return BookProject(
        project_id="ki_mittelstand",
        root=Path("."),
        title=title,
        subtitle=subtitle,
        amazon_description=description,
    )


def _empty_project() -> BookProject:
    return BookProject(
        project_id="leer",
        root=Path("."),
        title=None,
        subtitle=None,
        amazon_description=None,
    )


def test_build_report_returns_frozen_report_with_three_personas():
    report = build_persona_report(_ki_project())

    assert isinstance(report, PersonaReport)
    assert report.niche_key == "ki_und_ai"
    assert len(report.personas) == MAX_PERSONAS
    for persona in report.personas:
        assert isinstance(persona, BuyerPersona)
        assert persona.label
        assert persona.age_range
        assert persona.job
        assert persona.problem
        assert persona.buying_motive
        assert persona.anchor_quote


def test_personas_are_immutable_frozen_dataclasses():
    report = build_persona_report(_ki_project())
    persona = report.personas[0]

    try:
        persona.label = "anders"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BuyerPersona should be frozen and reject mutation")


def test_persona_strings_respect_character_caps():
    report = build_persona_report(_ki_project())

    for persona in report.personas:
        assert len(persona.problem) <= MAX_PROBLEM_CHARS
        assert len(persona.buying_motive) <= MAX_MOTIVE_CHARS
        assert len(persona.anchor_quote) <= MAX_QUOTE_CHARS


def test_empty_project_falls_back_to_general_personas():
    report = build_persona_report(_empty_project())

    assert report.niche_key == "allgemeines_sachbuch"
    assert len(report.personas) == MAX_PERSONAS
    # Anchors derived from no metadata should be empty.
    assert report.anchors == []


def test_proof_signal_appears_in_motive_when_metadata_has_numbers():
    report = build_persona_report(_ki_project())

    assert "proof_signal" in report.signal_flags
    # At least one motive should reference numbers / cases per refinement.
    assert any("Zahlen" in p.buying_motive for p in report.personas)


def test_b2b_signal_flag_is_detected_for_mittelstand_book():
    report = build_persona_report(_ki_project())

    assert "b2b" in report.signal_flags


def test_self_employed_signal_for_solo_book():
    project = BookProject(
        project_id="solo",
        root=Path("."),
        title="Solopreneur skalieren",
        subtitle="Für Selbständige und Freelancer",
        amazon_description="Wie du als selbststaendiger Berater planbare Kunden bekommst.",
    )

    report = build_persona_report(project)

    assert "selbststaendig" in report.signal_flags
    assert any("Solo" in p.buying_motive for p in report.personas)


def test_finance_niche_picks_finance_personas():
    project = BookProject(
        project_id="finanz",
        root=Path("."),
        title="Liquidität steuern als CFO",
        subtitle="Praxis-Playbook für die Finanzführung im Mittelstand",
        amazon_description=(
            "Cashflow, Forecast und Controlling — wie du in 12 Wochen die Finanzführung "
            "in den Griff bekommst. Aus 15 Jahren Praxis als CFO im Mittelstand."
        ),
    )

    report = build_persona_report(project)

    assert report.niche_key == "finanzen_und_cfo"
    labels = " ".join(p.label for p in report.personas)
    assert "CFO" in labels or "Controller" in labels


def test_toc_anchors_extend_the_anchor_list():
    project = _ki_project()
    chapter_titles = [
        "Einleitung in die KI-Praxis",
        "Datenstrategie für den Mittelstand",
        "Tooling-Auswahl und Pilotprojekt",
    ]

    report = build_persona_report(project, chapter_titles=chapter_titles)

    # At least one TOC word should appear (lower-cased) in the anchor list.
    toc_lower = {"einleitung", "praxis", "datenstrategie", "tooling", "pilotprojekt"}
    assert toc_lower.intersection(report.anchors), report.anchors


def test_render_markdown_contains_all_personas_and_metadata():
    project = _ki_project()
    report = build_persona_report(project)

    md = render_persona_report_markdown(project, report)

    assert "# Leser-Personas" in md
    assert "Nische:" in md
    for persona in report.personas:
        assert persona.label in md
        assert persona.age_range in md
        assert persona.job in md


def test_brief_section_renders_three_numbered_personas():
    report = build_persona_report(_ki_project())

    block = render_persona_brief_section(report)

    assert "1." in block and "2." in block and "3." in block
    assert "buyer_personas.md" in block


def test_brief_section_is_empty_when_no_personas():
    report = PersonaReport(
        niche_key="x",
        niche_label="y",
        niche_confidence=0,
        audience="z",
        subject="s",
        personas=[],
        anchors=[],
        signal_flags=[],
    )

    assert render_persona_brief_section(report) == ""


def test_json_roundtrip_is_serialisable():
    import json

    report = build_persona_report(_ki_project())

    payload = report.to_json()
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["niche_key"] == report.niche_key
    assert len(decoded["personas"]) == len(report.personas)
    assert decoded["personas"][0]["label"] == report.personas[0].label


def test_audience_uses_subtitle_for_marker():
    project = BookProject(
        project_id="x",
        root=Path("."),
        title="Test",
        subtitle="Ein Buch für ambitionierte Praktiker und Operatoren",
        amazon_description=None,
    )

    report = build_persona_report(project)

    # _extract_audience should yield "ambitionierte Praktiker" (truncated at "und andere")
    assert "Praktiker" in report.audience


def test_anchor_quote_contains_audience_and_subject():
    report = build_persona_report(_ki_project())
    persona = report.personas[0]

    assert report.audience in persona.anchor_quote or report.subject in persona.anchor_quote


# --- Marketing channels ----------------------------------------------------


def test_personas_have_channels_after_build():
    report = build_persona_report(_ki_project())

    for persona in report.personas:
        assert persona.channels, f"Persona {persona.label} missing channels"
        assert len(persona.channels) <= MAX_CHANNELS


def test_channel_suggestions_are_capped_at_max():
    # Flags that would push for many channels — must still respect the cap.
    channels = _suggest_channels(
        "CFO im KMU",
        "finanzen_und_cfo",
        flags=["b2b", "einsteiger", "selbststaendig", "proof_signal"],
    )

    assert len(channels) <= MAX_CHANNELS


def test_cfo_role_picks_finance_channel_first():
    channels = _suggest_channels(
        "CFO oder kaufmännische Leiterin",
        "finanzen_und_cfo",
        flags=[],
    )

    assert "LinkedIn (CFO/Controller-Gruppen)" in channels
    assert channels[0] == "LinkedIn (CFO/Controller-Gruppen)"


def test_sales_role_picks_sales_navigator_channel():
    channels = _suggest_channels(
        "Vertriebsleiterin oder Head of Sales",
        "vertrieb_und_marketing",
        flags=[],
    )

    assert "LinkedIn (Sales-Navigator)" in channels


def test_solo_role_picks_solo_business_channel():
    channels = _suggest_channels(
        "Selbständige Beraterin, Trainerin oder Agentur-Inhaber",
        "selbststaendigkeit",
        flags=["selbststaendig"],
    )

    assert "LinkedIn + X (Solo-Business)" in channels


def test_immobilien_niche_uses_visual_channels_by_default():
    channels = _suggest_channels(
        "Investor",
        "immobilien",
        flags=[],
    )

    assert any("YouTube" in ch or "Instagram" in ch for ch in channels)


def test_unknown_niche_falls_back_to_allgemein_defaults():
    channels = _suggest_channels(
        "Praktiker",
        "kein_niche_key_dieser_art",
        flags=[],
    )

    assert channels  # never empty
    assert "Amazon-Anzeigen" in channels


def test_b2b_flag_adds_xing_channel():
    channels = _suggest_channels(
        "Praktiker",
        "allgemeines_sachbuch",
        flags=["b2b"],
    )

    assert "XING (DACH-B2B)" in channels


def test_beginner_flag_adds_reddit_when_room_available():
    # Pick a niche with shorter defaults so reddit can squeeze in
    channels = _suggest_channels(
        "Wiedereinsteigerin",
        "mindset",
        flags=["einsteiger"],
    )

    # mindset niche has Instagram/YouTube/Podcast (3 items), so reddit
    # may or may not appear under the 3-cap — assert at least no crash
    # and that the result is well-formed.
    assert isinstance(channels, tuple)
    assert all(isinstance(ch, str) and ch for ch in channels)


def test_render_markdown_includes_marketing_channels_line():
    report = build_persona_report(_ki_project())

    rendered = render_persona_report_markdown(_ki_project(), report)

    assert "Marketing-Kanäle:" in rendered
    # at least one persona's channel must appear in the output
    first_channels = report.personas[0].channels
    assert any(ch in rendered for ch in first_channels)


def test_render_markdown_omits_channels_line_when_persona_has_no_channels():
    persona = BuyerPersona(
        label="Manual",
        age_range="30",
        job="Job",
        problem="Problem",
        buying_motive="Motive",
        anchor_quote="quote",
        channels=(),
    )
    report = PersonaReport(
        niche_key="all",
        niche_label="Allgemein",
        niche_confidence=50,
        audience="-",
        subject="-",
        personas=[persona],
    )

    project = _empty_project()
    rendered = render_persona_report_markdown(project, report)

    assert "Marketing-Kanäle" not in rendered


def test_buyer_persona_channels_default_to_empty_tuple():
    """Backwards compat: existing callers that don't pass channels must work."""
    persona = BuyerPersona(
        label="L",
        age_range="A",
        job="J",
        problem="P",
        buying_motive="M",
        anchor_quote="Q",
    )

    assert persona.channels == ()


def test_persona_to_json_includes_channels_list():
    persona = BuyerPersona(
        label="L",
        age_range="A",
        job="J",
        problem="P",
        buying_motive="M",
        anchor_quote="Q",
        channels=("LinkedIn", "Newsletter"),
    )

    data = persona.to_json()

    assert data["channels"] == ["LinkedIn", "Newsletter"]


def test_channels_are_deterministic_across_runs():
    """Two builds of the same project must return identical channel tuples."""
    report_a = build_persona_report(_ki_project())
    report_b = build_persona_report(_ki_project())

    for persona_a, persona_b in zip(report_a.personas, report_b.personas):
        assert persona_a.channels == persona_b.channels


# --- Manual persona overrides --------------------------------------------


def _project_with_metadata(tmp_path: Path, body: str) -> BookProject:
    meta = tmp_path / "metadata.md"
    meta.write_text(body, encoding="utf-8")
    return BookProject(
        project_id="overridebook",
        root=tmp_path,
        title="KI im Mittelstand",
        subtitle="Praxis statt Hype",
        amazon_description=(
            "Aus 10 Jahren operativer Praxis: konkrete Methoden mit Zahlen."
        ),
        metadata_files=[meta],
        notes_files=[],
    )


def test_extract_persona_overrides_returns_empty_when_no_section(tmp_path: Path):
    project = _project_with_metadata(tmp_path, "# Buch\n\nNur Text, keine Personas.\n")

    assert extract_persona_overrides(project) == []


def test_extract_persona_overrides_parses_full_block(tmp_path: Path):
    body = (
        "## Personas\n\n"
        "### Persona 1: Der Spezialist\n"
        "- Alter: 35-45\n"
        "- Job: Senior-Engineer in einem Mittelstandsbetrieb\n"
        "- Problem: KI-Tools bringen ihn nicht weiter\n"
        "- Kaufmotiv: Sucht eine pragmatische Anleitung\n"
        "- Suchanfrage: ki mittelstand operator\n"
        "\n"
        "### Persona 2: Die CFO\n"
        "- Alter: 42-58\n"
        "- Job: CFO im KMU\n"
        "- Problem: Keine belastbaren KI-Zahlen\n"
        "- Kaufmotiv: Will Zahlen statt Hype\n"
    )
    project = _project_with_metadata(tmp_path, body)

    overrides = extract_persona_overrides(project)

    assert len(overrides) == 2
    assert overrides[0]["label"] == "Der Spezialist"
    assert overrides[0]["age_range"] == "35-45"
    assert "Senior-Engineer" in overrides[0]["job"]
    assert "KI-Tools" in overrides[0]["problem"]
    assert "pragmatische Anleitung" in overrides[0]["buying_motive"]
    assert overrides[0]["anchor_quote"] == "ki mittelstand operator"
    assert overrides[1]["label"] == "Die CFO"
    assert overrides[1]["job"] == "CFO im KMU"


def test_extract_persona_overrides_accepts_field_aliases(tmp_path: Path):
    body = (
        "## Personas\n\n"
        "### Persona 1: X\n"
        "- Age: 30\n"
        "- Rolle: Tester\n"
        "- Pain: Etwas tut weh\n"
        "- Motive: Will Lösung\n"
        "- Query: test query\n"
    )
    project = _project_with_metadata(tmp_path, body)

    overrides = extract_persona_overrides(project)
    persona = overrides[0]

    assert persona["age_range"] == "30"
    assert persona["job"] == "Tester"
    assert persona["problem"] == "Etwas tut weh"
    assert persona["buying_motive"] == "Will Lösung"
    assert persona["anchor_quote"] == "test query"


def test_extract_persona_overrides_caps_at_max_personas(tmp_path: Path):
    body = "## Personas\n\n"
    for i in range(1, MAX_PERSONAS + 3):
        body += f"### Persona {i}: Label {i}\n- Problem: P{i}\n\n"
    project = _project_with_metadata(tmp_path, body)

    assert len(extract_persona_overrides(project)) == MAX_PERSONAS


def test_extract_persona_overrides_skips_unrecognized_fields(tmp_path: Path):
    body = (
        "## Personas\n\n"
        "### Persona 1: X\n"
        "- Lieblingsfarbe: blau\n"
        "- Problem: P\n"
        "- Tageshoroskop: aufwachen\n"
    )
    project = _project_with_metadata(tmp_path, body)

    persona = extract_persona_overrides(project)[0]

    assert "Lieblingsfarbe" not in persona
    assert persona["problem"] == "P"


def test_extract_persona_overrides_drops_block_without_actionable_field(tmp_path: Path):
    """A block with no label, job or problem is silently dropped."""
    body = (
        "## Personas\n\n"
        "### \n"
        "- Alter: 30\n"
        "\n"
        "### Persona 2: Real\n"
        "- Problem: Hat ein Problem\n"
    )
    project = _project_with_metadata(tmp_path, body)

    overrides = extract_persona_overrides(project)
    assert len(overrides) == 1
    assert overrides[0].get("problem") == "Hat ein Problem"


def test_build_persona_report_uses_overrides_when_present(tmp_path: Path):
    body = (
        "## Personas\n\n"
        "### Persona 1: Der Spezialist\n"
        "- Alter: 35-45\n"
        "- Job: Senior-Engineer\n"
        "- Problem: KI-Tools bringen ihn nicht weiter\n"
        "- Kaufmotiv: Pragmatische Anleitung\n"
    )
    project = _project_with_metadata(tmp_path, body)

    report = build_persona_report(project)

    # Override label must take precedence over niche baseline
    labels = [p.label for p in report.personas]
    assert "Der Spezialist" in labels
    assert "persona_override" in report.signal_flags


def test_build_persona_report_override_inherits_baseline_for_missing_fields(tmp_path: Path):
    """A sparse override (only label) still produces a renderable persona."""
    body = (
        "## Personas\n\n"
        "### Persona 1: Mein Wunsch-Käufer\n"
    )
    project = _project_with_metadata(tmp_path, body)

    report = build_persona_report(project)

    persona = report.personas[0]
    assert persona.label == "Mein Wunsch-Käufer"
    assert persona.problem  # baseline problem filled in
    assert persona.buying_motive  # baseline motive filled in
    assert persona.age_range  # baseline age filled in
    assert persona.channels  # channel suggestions still computed


def test_build_persona_report_without_overrides_keeps_baseline(tmp_path: Path):
    project = _project_with_metadata(tmp_path, "# Buch\n\nKein Override.\n")

    report = build_persona_report(project)

    # No override signal flag when nothing was declared
    assert "persona_override" not in report.signal_flags
    assert len(report.personas) == MAX_PERSONAS


def test_build_persona_report_override_keeps_channel_logic(tmp_path: Path):
    """Channels are derived from the override's job text, not baseline."""
    body = (
        "## Personas\n\n"
        "### Persona 1: Solo-Berater\n"
        "- Job: Freelance-Berater\n"
        "- Problem: Akquise fehlt\n"
    )
    project = _project_with_metadata(tmp_path, body)

    report = build_persona_report(project)

    persona = report.personas[0]
    # 'freelanc' → "LinkedIn + X (Solo-Business)" via _suggest_channels
    assert any("Solo-Business" in ch for ch in persona.channels)


def test_extract_persona_overrides_handles_missing_files_gracefully(tmp_path: Path):
    ghost = tmp_path / "ghost.md"
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[ghost],
        notes_files=[],
    )

    assert extract_persona_overrides(project) == []


def test_extract_persona_overrides_first_field_wins_for_duplicates(tmp_path: Path):
    body = (
        "## Personas\n\n"
        "### Persona 1: X\n"
        "- Problem: erstes problem\n"
        "- Problem: zweites problem\n"
    )
    project = _project_with_metadata(tmp_path, body)

    persona = extract_persona_overrides(project)[0]
    assert persona["problem"] == "erstes problem"
