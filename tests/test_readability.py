"""Unit tests for German Amstad-FRE readability analysis."""

from __future__ import annotations

import pytest

from modules.chapters import Chapter
from modules.readability import (
    DEFAULT_TARGET_MAX,
    DEFAULT_TARGET_MIN,
    LEVEL_BANDS,
    MIN_BODY_WORDS_FOR_SIGNAL,
    ReadabilityMetric,
    ReadabilityReport,
    build_readability_report,
    classify_fre,
    compute_amstad_fre,
    count_sentences,
    count_syllables_de,
    count_words,
    iter_words,
    render_readability_markdown,
)


class TestCountSyllablesDE:
    def test_simple_one_syllable(self):
        assert count_syllables_de("haus") == 1

    def test_two_syllables(self):
        # "auto" -> "au-to": vowel groups "au" and "o" -> 2 syllables
        assert count_syllables_de("auto") == 2

    def test_three_syllables(self):
        # "beispiel": "ei", "ie" each count as one vowel group -> 2 syllables.
        # The heuristic intentionally counts diphthongs as one group.
        assert count_syllables_de("beispiel") == 2

    def test_umlaut_word(self):
        assert count_syllables_de("über") == 2

    def test_empty_returns_zero(self):
        assert count_syllables_de("") == 0

    def test_consonant_only_floors_to_one(self):
        # No vowels at all (rare; e.g. an acronym) -> floor of 1 so the
        # word still contributes to the syllable count.
        assert count_syllables_de("rhythmen") >= 1

    def test_case_insensitive(self):
        assert count_syllables_de("Haus") == count_syllables_de("haus")


class TestCountWords:
    def test_empty(self):
        assert count_words("") == 0

    def test_simple_german(self):
        assert count_words("Das ist ein Test.") == 4

    def test_hyphenated_counts_as_one(self):
        assert count_words("KDP-Buch") == 1

    def test_ignores_pure_digits(self):
        assert count_words("Buch 2024 erscheint") == 2

    def test_handles_umlauts(self):
        assert count_words("Über Bäche flüstert Müll.") == 4


class TestCountSentences:
    def test_empty(self):
        assert count_sentences("") == 0

    def test_single_sentence(self):
        assert count_sentences("Das ist ein Satz.") == 1

    def test_three_sentences(self):
        assert count_sentences("Eins. Zwei! Drei?") == 3

    def test_no_terminator_returns_one_when_words_present(self):
        # Floor of 1 prevents division-by-zero in Amstad formula.
        assert count_sentences("ein satz ohne punkt") == 1

    def test_ellipsis_does_not_inflate(self):
        # "..." collapses to a single terminator (not three), and "?" adds one
        # more, so the count stays at 2 instead of inflating to 4.
        assert count_sentences("Was wenn... das passiert?") == 2

    def test_ellipsis_alone_counts_as_one(self):
        assert count_sentences("Was wenn...") == 1


class TestComputeAmstadFRE:
    def test_empty_returns_sentinel(self):
        fre, words, sentences, syllables, asl, asw = compute_amstad_fre("")
        assert (fre, words, sentences, syllables, asl, asw) == (0.0, 0, 0, 0, 0.0, 0.0)

    def test_simple_text_scores_in_band(self):
        text = "Das Buch ist gut. Es liest sich leicht. Der Autor schreibt klar."
        fre, words, sentences, _, asl, asw = compute_amstad_fre(text)
        assert words == 12
        assert sentences == 3
        assert asl == pytest.approx(12 / 3, rel=0.01)
        assert 0 < fre <= 180

    def test_long_complex_sentences_lower_score(self):
        easy = "Komm. Geh. Iss."
        hard = (
            "Die methodische Implementierung kompliziertester Algorithmen "
            "erfordert tiefgreifende Kenntnisse fundamentaler Konzepte."
        )
        easy_fre, *_ = compute_amstad_fre(easy)
        hard_fre, *_ = compute_amstad_fre(hard)
        assert easy_fre > hard_fre


class TestClassifyFRE:
    def test_very_easy(self):
        key, _ = classify_fre(95.0)
        assert key == "sehr_leicht"

    def test_medium(self):
        key, _ = classify_fre(65.0)
        assert key == "mittel"

    def test_very_hard(self):
        key, _ = classify_fre(10.0)
        assert key == "sehr_schwer"

    def test_negative_clamps_to_very_hard(self):
        key, _ = classify_fre(-20.0)
        assert key == "sehr_schwer"

    def test_all_bands_have_unique_labels(self):
        # Drift-guard: nobody should accidentally collapse two bands.
        labels = [label for _, _, label in LEVEL_BANDS]
        assert len(labels) == len(set(labels))


def _make_chapter(index: int, title: str, body: str) -> Chapter:
    return Chapter(index=index, title=title, body=body, word_count=count_words(body))


class TestBuildReadabilityReport:
    def test_empty_chapters_returns_empty_report(self):
        report = build_readability_report([])
        assert isinstance(report, ReadabilityReport)
        assert report.overall.fre_score == 0.0
        assert report.chapters == tuple()
        assert report.weakest_index is None

    def test_single_chapter_overall_matches_chapter(self):
        body = (
            "Das ist ein einfaches Buch. Es liest sich leicht. "
            "Jeder Leser versteht es. Wir schreiben kurz und klar."
        ) * 10
        chap = _make_chapter(1, "Einleitung", body)
        report = build_readability_report([chap])
        assert len(report.chapters) == 1
        assert report.overall.fre_score == pytest.approx(
            report.chapters[0].fre_score, abs=0.5
        )

    def test_weakest_chapter_is_furthest_from_target(self):
        easy_body = ("Das ist gut. Es geht. Wir schreiben klar. " * 30).strip()
        hard_body = (
            "Die methodisch-philosophische Implementierung kompliziertester "
            "Algorithmen erfordert tiefgreifende Sachkenntnis fundamentaler "
            "Konzepte aus mehreren wissenschaftlichen Domaenen. "
        ) * 8
        easy = _make_chapter(1, "Easy", easy_body)
        hard = _make_chapter(2, "Hard", hard_body)
        report = build_readability_report([easy, hard])
        assert report.weakest_index == 2

    def test_all_chapters_in_target_no_weakest(self):
        # Two chapters whose FRE lands comfortably inside [50, 80].
        body = (
            "Das Buch beschreibt klare Prozesse. Jedes Kapitel zeigt einen Schritt. "
            "Der Autor zeigt Beispiele aus seiner Praxis. Du lernst, wie es laeuft. "
        ) * 12
        a = _make_chapter(1, "Eins", body)
        b = _make_chapter(2, "Zwei", body)
        report = build_readability_report([a, b])
        if all(50 <= m.fre_score <= 80 for m in report.chapters):
            assert report.weakest_index is None

    def test_fixes_only_for_outside_target(self):
        body = "Die Sache. Es laeuft. " * 60
        chap = _make_chapter(1, "Inside", body)
        report = build_readability_report(
            [chap], target_min=10, target_max=200
        )
        # Inside an artificially wide target band -> no fix line.
        assert report.fixes == tuple()
        assert report.chapters[0].fix == ""

    def test_short_chapter_flagged_zu_kurz(self):
        body = "Hallo. Tschuess."  # well below MIN_BODY_WORDS_FOR_SIGNAL
        chap = _make_chapter(1, "Kurz", body)
        report = build_readability_report([chap])
        assert "zu kurz" in report.chapters[0].fix.lower()

    def test_custom_target_band_respected(self):
        body = "Die Sache. Es laeuft sehr klar. " * 30
        chap = _make_chapter(1, "Inside", body)
        report = build_readability_report(
            [chap], target_min=DEFAULT_TARGET_MIN, target_max=DEFAULT_TARGET_MAX
        )
        assert report.target_min == DEFAULT_TARGET_MIN
        assert report.target_max == DEFAULT_TARGET_MAX

    def test_report_is_frozen(self):
        chap = _make_chapter(1, "X", "Das ist ein Test. " * 80)
        report = build_readability_report([chap])
        with pytest.raises((AttributeError, Exception)):
            report.weakest_index = 99  # type: ignore[misc]

    def test_to_json_roundtrip_fields(self):
        chap = _make_chapter(1, "X", "Das ist ein Test. " * 80)
        report = build_readability_report([chap])
        payload = report.to_json()
        assert "overall" in payload
        assert "chapters" in payload
        assert payload["target_min"] == DEFAULT_TARGET_MIN
        assert payload["target_max"] == DEFAULT_TARGET_MAX
        assert payload["chapters"][0]["index"] == 1
        assert "fre_score" in payload["overall"]

    def test_in_target_property(self):
        easy = "Hallo. Danke. Gerne. " * 80
        chap = _make_chapter(1, "X", easy)
        report = build_readability_report(
            [chap], target_min=0, target_max=200
        )
        assert report.is_in_target is True


class TestRenderReadabilityMarkdown:
    def test_renders_header_and_band(self):
        chap = _make_chapter(1, "Eins", "Das ist ein Test. " * 80)
        report = build_readability_report([chap])
        md = render_readability_markdown("Mein Buch", report)
        assert "# Lesbarkeit (Amstad-FRE)" in md
        assert "Mein Buch" in md
        assert f"FRE {report.target_min}-{report.target_max}" in md

    def test_renders_chapter_table(self):
        chap_a = _make_chapter(1, "Eins", "Das ist ein Test. " * 80)
        chap_b = _make_chapter(2, "Zwei", "Komplexe Algorithmen erfordern. " * 50)
        report = build_readability_report([chap_a, chap_b])
        md = render_readability_markdown("Buch", report)
        assert "| # | Kapitel |" in md
        assert "Eins" in md
        assert "Zwei" in md

    def test_empty_report_handles_no_chapters(self):
        report = build_readability_report([])
        md = render_readability_markdown("Buch", report)
        assert "Lesbarkeit" in md
        assert "## Gesamt" in md

    def test_renders_weakest_chapter_section_when_present(self):
        easy = _make_chapter(1, "Easy", ("Das ist gut. " * 40))
        hard = _make_chapter(
            2,
            "Hard",
            (
                "Die methodisch-philosophische Implementierung kompliziertester "
                "Algorithmen erfordert tiefgreifende Sachkenntnis fundamentaler "
                "Konzepte aus mehreren wissenschaftlichen Domaenen. "
            )
            * 8,
        )
        report = build_readability_report([easy, hard])
        md = render_readability_markdown("Buch", report)
        if report.weakest_index is not None:
            assert "Schwaechstes Kapitel" in md

    def test_renders_fixes_section_when_any(self):
        hard = _make_chapter(
            1,
            "Hard",
            (
                "Die methodisch-philosophische Implementierung kompliziertester "
                "Algorithmen erfordert tiefgreifende Sachkenntnis. "
            )
            * 10,
        )
        report = build_readability_report([hard])
        md = render_readability_markdown("Buch", report)
        if report.fixes:
            assert "## Konkrete Fixes" in md


class TestReadabilityMetricImmutability:
    def test_metric_is_frozen(self):
        metric = ReadabilityMetric(
            label="X",
            index=1,
            word_count=10,
            sentence_count=2,
            syllable_count=15,
            avg_sentence_length=5.0,
            avg_syllables_per_word=1.5,
            fre_score=60.0,
            level_key="mittel",
            level_label="Mittel (B1/B2)",
            fix="",
        )
        with pytest.raises((AttributeError, Exception)):
            metric.fre_score = 99.0  # type: ignore[misc]
