from __future__ import annotations

import json
from pathlib import Path

from modules.agent_core import AgentMemory, SkillRegistry
from modules.amazon_html import (
    BULLETS_SOURCES,
    BULLETS_SOURCE_TEMPLATE,
    build_amazon_description_html,
    extract_amazon_bullets_via_llm,
    render_amazon_description_report_markdown,
)
from modules.artifacts import ArtifactWriter
from modules.chapters import (
    ChapterReport,
    apply_chapter_fixes,
    balance_thresholds_from_app,
    extract_chapter_fixes_via_llm,
    extract_chapter_intros,
    extract_docx_chapters,
)
from modules.competitive_positioning import (
    PositioningReport,
    build_positioning_report,
    render_positioning_markdown,
)
from modules.config import AppConfig
from modules.cover import render_cover_review
from modules.discovery import BookProject, discover_books, render_discovery_markdown
from modules.industrial import build_industrial_qa, render_beginner_summary, render_industrial_qa_markdown
from modules.kdp_keywords import (
    KDPKeyword,
    build_kdp_keywords,
    extract_kdp_categories,
    extract_kdp_keywords_via_llm,
    find_keyword_conflicts,
    render_kdp_keywords_report_markdown,
)
from modules.llm import LLMClient
from modules.persona_match import build_persona_match_report, render_persona_match_section
from modules.personas import PersonaReport, build_persona_report, render_persona_report_markdown
from modules.review import (
    amazon_review,
    build_chapter_review_report,
    chapter_arc_review,
    checklist,
    render_chapter_review,
    executive_summary,
    launch_content,
    manuscript_review,
    publisher_board_review,
    project_metadata,
    readability_review,
    voice_report,
)
from modules.release_assets import (
    render_amazon_research_brief,
    render_competitor_template_csv,
    render_kindle_preview_check,
)
from modules.rewrites import (
    RewriteReport,
    apply_rewrite_variants,
    build_rewrite_report,
    extract_rewrite_variants_via_llm,
    render_rewrite_report_markdown,
)
from modules.round_delta import RoundDelta, render_round_delta_markdown
from modules.rounds import make_round_id, snapshot_round
from modules.run_logger import RunLogger
from modules.sample_scan import (
    SampleScanReport,
    apply_sample_rewrites,
    build_sample_scan_report,
    extract_sample_rewrites_via_llm,
    render_sample_scan_markdown,
    sample_scan_config_from_app,
    section_bodies_from_paragraphs,
)
from modules.score_history import (
    append_score_history,
    load_score_history,
    render_score_history_markdown,
)
from modules.score_history_graph import (
    build_chart_dataset,
    render_history_chart_png,
)


def _weakest_chapter_payload(
    chapter_json: dict | None, limit: int = 3
) -> list[dict] | None:
    """Extract the N weakest chapters from a chapter_review payload.

    Returns ``None`` when chapter data is unavailable so the caller can
    distinguish "no data" from "no weak chapters". Each entry carries the
    fields ``render_beginner_summary`` needs: ``index``, ``title``,
    ``overall`` and ``fix``.
    """

    if not chapter_json:
        return None
    chapters = chapter_json.get("chapters") or []
    if not chapters:
        return []
    flattened: list[dict] = []
    for chap in chapters:
        scores = chap.get("scores") or {}
        flattened.append({
            "index": chap.get("index"),
            "title": chap.get("title") or "",
            "overall": chap.get("overall") or 0,
            "fix": chap.get("fix") or "",
            "status": chap.get("status") or "",
            "scores": scores,
        })
    flattened.sort(key=lambda c: (int(c.get("overall") or 0), int(c.get("index") or 0)))
    return flattened[: max(0, limit)]


def _top_rewrite_payload(rewrite_json: dict | None) -> dict | None:
    """Pick the strongest single rewrite variant for beginner_summary.

    Selects from bundles that have at least one diagnosis finding — if a
    field is already in good shape we do not suggest a rewrite for it.
    Among eligible bundles, returns the option with the highest
    ``keyword_score``; ties are broken by shorter ``char_count`` (more
    punchy) and a stable field priority (title > subtitle > description).

    Returns ``None`` when no rewrite data is available or when no field
    has any diagnosis finding — keeping the summary clean when the
    existing metadata needs no copy work.
    """

    if not rewrite_json:
        return None
    bundles = rewrite_json.get("bundles") or []
    if not bundles:
        return None
    field_priority: dict[str, int] = {
        "title": 0,
        "subtitle": 1,
        "description_lead": 2,
    }
    candidates: list[tuple[int, int, int, dict]] = []
    for bundle in bundles:
        diagnosis = bundle.get("diagnosis") or []
        if not diagnosis:
            continue
        field_key = str(bundle.get("field") or "")
        priority = field_priority.get(field_key, 99)
        for option in bundle.get("options") or []:
            text = str(option.get("text") or "").strip()
            if not text:
                continue
            keyword_score = int(option.get("keyword_score") or 0)
            char_count = int(option.get("char_count") or len(text))
            candidates.append(
                (
                    -keyword_score,
                    char_count,
                    priority,
                    {
                        "field": field_key,
                        "text": text,
                        "keyword_score": keyword_score,
                        "char_count": char_count,
                        "motivation": str(option.get("motivation") or ""),
                    },
                )
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _round_delta_payload(
    delta: RoundDelta | None,
    *,
    fix_limit: int = 2,
) -> dict | None:
    """Compact round-over-round progress signal for beginner_summary.

    Returns ``None`` when there is no previous round to compare against —
    round 1 has nothing to celebrate, so the section should be omitted
    entirely. Otherwise returns a dict the renderer can consume without
    re-reading round_delta data.

    Caps ``top_resolved`` and ``top_persistent`` to ``fix_limit`` items so
    the highlight stays short — the full round_delta.md remains the
    source of truth for the complete list.
    """

    if delta is None or not delta.has_previous:
        return None
    previous = delta.previous_round or {}
    current = delta.current_round
    limit = max(0, fix_limit)
    return {
        "resolved_count": len(delta.resolved_fixes),
        "persistent_count": len(delta.persistent_fixes),
        "new_count": len(delta.new_fixes),
        "score_delta": delta.score_delta,
        "decision_changed": delta.decision_changed,
        "previous_decision": previous.get("decision"),
        "current_decision": current.get("decision"),
        "top_resolved": list(delta.resolved_fixes[:limit]),
        "top_persistent": list(delta.persistent_fixes[:limit]),
    }


def _score_history_payload(
    history: dict | None,
    *,
    limit: int = 3,
) -> dict | None:
    """Compact score-history highlight for beginner_summary.

    Returns ``None`` when there are fewer than two entries in the history —
    a single data point has no trend to show and would only add noise to
    the summary. With two or more entries, returns a dict the renderer can
    consume without re-reading score_history data:

    - ``series``: last ``limit`` entries, each carrying ``timestamp``,
      ``score`` and an ``delta`` against the previous entry in the window
      (``None`` for the first entry).
    - ``first_score`` / ``latest_score``: anchors for the trend headline.
    - ``delta_total``: ``latest_score - first_score`` over the window.
    - ``trend``: ``"rising"`` / ``"falling"`` / ``"stable"`` — one of three
      stable keys so the renderer can pick the right badge and German
      label without re-deriving the comparison.

    The helper is immutable: it copies values out of ``history.entries``
    so a caller mutating the returned dict cannot mutate the source.
    """

    if not history:
        return None
    entries = history.get("entries") or []
    if len(entries) < 2:
        return None
    window_size = max(2, limit)
    window = entries[-window_size:]
    series: list[dict] = []
    previous_score: int | None = None
    for entry in window:
        try:
            score = int(entry.get("industrial_score") or 0)
        except (TypeError, ValueError):
            score = 0
        timestamp = str(entry.get("timestamp") or "")
        delta: int | None = (
            None if previous_score is None else score - previous_score
        )
        series.append({"timestamp": timestamp, "score": score, "delta": delta})
        previous_score = score
    first_score = series[0]["score"]
    latest_score = series[-1]["score"]
    delta_total = latest_score - first_score
    if delta_total > 0:
        trend = "rising"
    elif delta_total < 0:
        trend = "falling"
    else:
        trend = "stable"
    return {
        "series": series,
        "first_score": first_score,
        "latest_score": latest_score,
        "delta_total": delta_total,
        "trend": trend,
        "entry_count": len(entries),
    }


TOP_KDP_KEYWORD_MAX_LIMIT: int = 7


def _top_kdp_keywords_payload(
    keywords: list[KDPKeyword] | None,
    *,
    limit: int = 3,
) -> list[dict] | None:
    """Pick the top-N KDP keyword slots for a beginner_summary copy block.

    Prefers source diversity — the first keyword from each distinct source
    family (``subject_format``, ``subject_audience``, ``audience_format``,
    ``anchor_pair`` …) is selected before any duplicate-source keyword.
    The canonical pick is ``subject_audience + audience_format +
    anchor_pair`` rather than three near-identical ``subject_format``
    variants, so the author sees a *spread* of search intents.

    Falls back to ordered keywords if source diversity does not fill the
    limit. Returns ``None`` when no keywords were generated (so the
    section is omitted entirely) and ``[]`` when ``limit <= 0``.

    Each returned dict carries ``text``, ``char_count``, ``source`` and
    ``rationale`` — exactly what the renderer needs without re-reading the
    KDPKeyword object.
    """

    if not keywords:
        return None
    cap = max(0, min(TOP_KDP_KEYWORD_MAX_LIMIT, int(limit)))
    if cap == 0:
        return []
    picked_texts: set[str] = set()
    picked: list[dict] = []
    seen_sources: set[str] = set()
    for keyword in keywords:
        if keyword.source in seen_sources:
            continue
        if keyword.text in picked_texts:
            continue
        seen_sources.add(keyword.source)
        picked.append({
            "text": keyword.text,
            "char_count": keyword.char_count,
            "source": keyword.source,
            "rationale": keyword.rationale,
        })
        picked_texts.add(keyword.text)
        if len(picked) >= cap:
            return picked
    for keyword in keywords:
        if len(picked) >= cap:
            break
        if keyword.text in picked_texts:
            continue
        picked.append({
            "text": keyword.text,
            "char_count": keyword.char_count,
            "source": keyword.source,
            "rationale": keyword.rationale,
        })
        picked_texts.add(keyword.text)
    return picked


def _top_persona_payload(
    persona_report: PersonaReport | None,
) -> dict | None:
    """Compact buyer-persona highlight for beginner_summary.

    Surfaces the single most likely buyer (Persona #1 of the report)
    so the author sees who to write the first three description lines
    for, without opening ``buyer_personas.md`` separately. Persona #1
    is by convention the most likely buyer in the niche baseline — the
    persona report itself sorts personas by representativeness, so
    picking the first one is deterministic.

    Returns ``None`` when there is no persona report or when the report
    carries no personas at all — the section is then omitted entirely
    to keep the summary clean for first runs before metadata is in
    place.

    The returned dict carries ``label``, ``age_range``, ``job``,
    ``problem``, ``buying_motive``, ``anchor_quote`` and
    ``niche_label`` / ``niche_confidence`` — exactly what the renderer
    needs without re-reading the report.

    The helper is immutable: it copies values out so a caller mutating
    the returned dict cannot affect the source report.
    """

    if persona_report is None:
        return None
    personas = list(persona_report.personas or [])
    if not personas:
        return None
    top = personas[0]
    return {
        "label": top.label,
        "age_range": top.age_range,
        "job": top.job,
        "problem": top.problem,
        "buying_motive": top.buying_motive,
        "anchor_quote": top.anchor_quote,
        "niche_label": persona_report.niche_label,
        "niche_confidence": int(persona_report.niche_confidence),
    }


AMAZON_HTML_PREVIEW_MAX_BULLETS: int = 2
AMAZON_HTML_PREVIEW_MAX_CHARS: int = 320


def _amazon_html_preview_payload(amazon_html: Any) -> dict | None:
    """Compact Amazon-HTML preview for beginner_summary.

    Renders the headline + lead + up to two bullets as the Kindle
    shopper would *read* them — no HTML tags, normalized whitespace.
    This is the surface the author judges by; it's the difference
    between "I wrote good HTML" and "the listing reads well above
    the Mehr-lesen fold".

    Returns ``None`` when the snippet has no content at all so the
    section gets skipped entirely instead of rendering an empty
    block.
    """

    if amazon_html is None:
        return None
    headline = str(getattr(amazon_html, "headline", "") or "").strip()
    lead = str(getattr(amazon_html, "lead", "") or "").strip()
    bullets_raw = getattr(amazon_html, "bullets", ()) or ()
    bullets: list[str] = []
    for raw in bullets_raw:
        text = str(raw or "").strip()
        if text:
            bullets.append(text)
        if len(bullets) >= AMAZON_HTML_PREVIEW_MAX_BULLETS:
            break
    if not headline and not lead and not bullets:
        return None
    char_count = int(getattr(amazon_html, "char_count", 0) or 0)
    keyword_score = int(getattr(amazon_html, "keyword_score", 0) or 0)
    raw_source = str(getattr(amazon_html, "bullets_source", "") or "").strip()
    bullets_source = (
        raw_source if raw_source in BULLETS_SOURCES else BULLETS_SOURCE_TEMPLATE
    )
    return {
        "headline": headline,
        "lead": lead,
        "bullets": tuple(bullets),
        "char_count": char_count,
        "keyword_score": keyword_score,
        "bullets_source": bullets_source,
    }


def _persona_match_payload(persona_match: Any) -> dict | None:
    """Compact persona-match highlight for beginner_summary.

    Returns a small immutable dict with the aggregate score, status and
    the weakest persona — the entry that drags the average down and is
    the most concrete fix lever for the author. Returns ``None`` when
    there is no match report or no entries with measurable tokens at
    all (e.g. all personas only carried stop words).

    The pipeline always computes the match report; the section is only
    skipped at render time when the description is missing — in that
    case the renderer surfaces a "Beschreibung fehlt" hint instead of a
    misleading zero score. The renderer reads ``description_present``
    from this payload.
    """

    if persona_match is None:
        return None
    entries = list(getattr(persona_match, "entries", ()) or ())
    if not entries:
        return None
    measurable = [entry for entry in entries if int(getattr(entry, "total_tokens", 0)) > 0]
    if not measurable:
        return None
    weakest = min(
        measurable,
        key=lambda entry: (int(entry.score), entry.label),
    )
    return {
        "overall_score": int(persona_match.overall_score),
        "status": str(persona_match.status),
        "description_present": bool(persona_match.description_present),
        "lead_lines_present": bool(persona_match.lead_lines_present),
        "total_personas": len(entries),
        "measurable_personas": len(measurable),
        "weakest_label": str(weakest.label),
        "weakest_score": int(weakest.score),
        "weakest_missing": tuple(weakest.missing_tokens),
    }


def _top_collision_risk_payload(
    positioning: PositioningReport | None,
) -> dict | None:
    """Surface the single highest-priority positioning collision risk.

    ``PositioningReport.collision_risks`` is already ordered by severity:
    missing-numbers signal first, voice second, audience third, method
    fourth, hype-title fifth and niche-specific reinforcements last. The
    first non-empty entry is therefore the most impactful warning the
    author should see — anything beyond a single risk would dilute the
    signal in beginner_summary. Niche-specific risks (e.g. KI-Nische
    without numbers) are surfaced via the ``niche_label`` field so the
    author sees the framing without re-reading the full report.

    Returns ``None`` when there is no positioning report, no collision
    risk recorded, or the top risk text is whitespace-only — keeping the
    summary clean when the metadata gives no positioning collision
    signal at all.

    The returned dict is immutable: it copies values out so a caller
    mutating the result cannot affect the source report.
    """

    if positioning is None:
        return None
    risks = list(positioning.collision_risks or [])
    for risk in risks:
        text = str(risk or "").strip()
        if not text:
            continue
        return {
            "risk": text,
            "niche_label": positioning.niche_label,
            "niche_confidence": int(positioning.niche_confidence),
            "total_risks": len(risks),
        }
    return None


TOP_POSITIONING_MAX_LIMIT: int = 3


def _top_positioning_payload(
    positioning: PositioningReport | None,
    *,
    limit: int = 1,
) -> dict | None:
    """Compact positioning highlight for beginner_summary.

    Returns ``None`` when there is no positioning report or when the
    report carries only the ``kein_signal`` fallback angle — without a
    real differentiation signal the summary should stay quiet rather
    than paste a generic pitch into the author's face.

    Otherwise returns a dict the renderer can consume without re-reading
    the full positioning report:

    - ``angle_claim`` / ``angle_evidence`` / ``angle_strength``:
      the single strongest differentiation angle (first entry of
      ``unique_angles`` — already sorted by strength desc).
    - ``additional_angles``: list of secondary differentiation angles
      (angles 2..N where N=``limit`` capped at
      ``TOP_POSITIONING_MAX_LIMIT``). Each entry carries ``angle_key``,
      ``angle_claim``, ``angle_evidence`` and ``angle_strength``. Empty
      list when ``limit <= 1`` or only one real signal exists.
    - ``pitch``: the one-sentence positioning pitch ready to paste.
    - ``niche_label`` / ``niche_confidence``: helps the author judge
      whether the niche detection is plausible.
    - ``audience``: surfaced as a separate field so the renderer can
      build a short "Wer kauft das?" line without re-parsing the pitch.

    ``limit`` controls how many angles the renderer should surface in
    total (1 = top angle only). Values <1 are coerced to 1 so the
    summary always shows at least the strongest angle when a real
    signal exists; values >``TOP_POSITIONING_MAX_LIMIT`` are clamped
    down so we never crowd the summary with low-strength signals.
    ``kein_signal`` and zero-strength angles are skipped at every
    position — the report stays quiet when there is nothing to say.

    The helper is immutable: it copies values out so a caller mutating
    the returned dict cannot affect the source report.
    """

    if positioning is None:
        return None
    real_angles = [
        angle
        for angle in (positioning.unique_angles or [])
        if angle.key != "kein_signal" and angle.strength > 0
    ]
    if not real_angles:
        return None
    cap = max(1, min(TOP_POSITIONING_MAX_LIMIT, int(limit)))
    picked = real_angles[:cap]
    top = picked[0]
    additional = [
        {
            "angle_key": angle.key,
            "angle_claim": angle.claim,
            "angle_evidence": angle.evidence,
            "angle_strength": int(angle.strength),
        }
        for angle in picked[1:]
    ]
    return {
        "angle_key": top.key,
        "angle_claim": top.claim,
        "angle_evidence": top.evidence,
        "angle_strength": int(top.strength),
        "additional_angles": additional,
        "pitch": positioning.positioning_pitch,
        "niche_label": positioning.niche_label,
        "niche_confidence": int(positioning.niche_confidence),
        "audience": positioning.audience,
    }


POSITIONING_SCORE_TOP_N = 3


def _positioning_score(positioning: PositioningReport | None) -> int | None:
    """Aggregate score for the competitive-positioning report.

    Returns the rounded average strength (0–100) of the top
    ``POSITIONING_SCORE_TOP_N`` differentiation angles, skipping the
    ``kein_signal`` fallback. Returns ``None`` when no real angle exists —
    score_history then records "no positioning available" rather than a
    misleading zero.
    """

    if positioning is None:
        return None
    angles = [
        angle
        for angle in (positioning.unique_angles or [])
        if angle.key != "kein_signal" and angle.strength > 0
    ]
    if not angles:
        return None
    top = angles[:POSITIONING_SCORE_TOP_N]
    average = sum(int(angle.strength) for angle in top) / len(top)
    return max(0, min(100, round(average)))


def _top_chapter_balance_payload(chapter_json: dict | None) -> dict | None:
    """Extract the single most extreme word-count outlier for beginner_summary.

    Returns ``None`` when there is no balance data, when both outlier lists
    are empty (chapter balance is healthy), or when the top entry carries
    no usable fix line. When an outlier exists, returns an immutable dict
    with ``kind`` (``"oversized"`` / ``"undersized"``), ``index``,
    ``title``, ``word_count``, ``median``, ``ratio`` and ``fix``.

    Selection rule: compare the most extreme oversized entry (first in the
    pre-sorted oversized list, i.e. longest) against the most extreme
    undersized entry (first in the pre-sorted undersized list, i.e.
    shortest) by absolute deviation from the median ratio of 1.0. Whichever
    sits further from 1.0 wins — that is the structurally most surprising
    chapter. Tie-break: prefer ``oversized`` (a too-long chapter is the
    more disruptive split-or-stay decision for the author).
    """

    if not chapter_json:
        return None
    balance = chapter_json.get("balance")
    if not balance:
        return None
    oversized = balance.get("oversized") or []
    undersized = balance.get("undersized") or []
    candidates: list[tuple[float, int, dict]] = []
    # tie_priority: 0 = oversized wins ties, 1 = undersized
    if oversized:
        top_over = oversized[0]
        ratio_over = float(top_over.get("ratio") or 0.0)
        candidates.append((abs(ratio_over - 1.0), 0, top_over))
    if undersized:
        top_under = undersized[0]
        ratio_under = float(top_under.get("ratio") or 0.0)
        candidates.append((abs(ratio_under - 1.0), 1, top_under))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, _, top = candidates[0]
    fix = str(top.get("fix") or "").strip()
    if not fix:
        return None
    return {
        "kind": str(top.get("kind") or "").strip(),
        "index": top.get("index"),
        "title": str(top.get("title") or "").strip(),
        "word_count": int(top.get("word_count") or 0),
        "median": int(top.get("median") or 0),
        "ratio": float(top.get("ratio") or 0.0),
        "fix": fix,
    }


def _balance_score(chapter_json: dict | None) -> int | None:
    """Compute the 0–100 chapter-balance score for score_history.

    Definition: ``round(100 * (total - outliers) / total)`` where
    ``outliers`` is the count of chapters flagged as oversized OR
    undersized by ``analyze_chapter_balance``. A balanced book scores
    100; a book where half the chapters drift far from the median
    scores ~50.

    Returns ``None`` when:

    - no chapter data is available,
    - the chapter list is empty,
    - no balance analysis ran (e.g. below ``BALANCE_MIN_CHAPTERS``).

    A ``None`` signals "no balance metric possible" so the score-
    history table shows a dash instead of a misleading zero.
    """

    if not chapter_json:
        return None
    chapters = chapter_json.get("chapters") or []
    if not chapters:
        return None
    balance = chapter_json.get("balance")
    if not isinstance(balance, dict):
        return None
    total = len(chapters)
    if total <= 0:
        return None
    oversized = balance.get("oversized") or []
    undersized = balance.get("undersized") or []
    outliers = len(oversized) + len(undersized)
    # Clamp defensively: ratio above 1.0 shouldn't happen but bad data
    # could push outliers > total — score caps at 0 in that case.
    in_range = max(0, total - outliers)
    return max(0, min(100, round(100 * in_range / total)))


def _top_arc_payload(arc_json: dict | None) -> dict | None:
    """Extract the single biggest structural lever from a chapter_arc payload.

    Returns ``None`` when arc data is unavailable, when the arc has no
    fixes (canonical Problem → Lösung → Beweis → Transformation order with
    all phases present), or when there is no actionable ``top_fix`` to
    surface. The author should only see this block when there is a real
    structural lever to pull — anything else is noise.

    When a fix exists, returns an immutable dict with ``arc_score``,
    ``status``, ``top_fix`` (the first fix from the report — fixes are
    already ordered with inversion-fixes first, then missing-phase fixes),
    ``inversion_count`` and ``missing_count``.
    """

    if not arc_json:
        return None
    fixes = arc_json.get("fixes") or []
    if not fixes:
        return None
    top_fix = str(fixes[0] or "").strip()
    if not top_fix:
        return None
    inversions = arc_json.get("inversions") or []
    missing = arc_json.get("missing_phases") or []
    return {
        "arc_score": int(arc_json.get("arc_score") or 0),
        "status": str(arc_json.get("status") or ""),
        "top_fix": top_fix,
        "inversion_count": len(inversions),
        "missing_count": len(missing),
    }


WEAKEST_SAMPLE_MAX_LIMIT = 10


def _sample_section_dict(section: dict) -> dict:
    """Project one sample-scan section into the render-payload shape.

    Carries ``opening_rewrite`` through when the upstream sample-scan
    produced an LLM-generated opening sentence for this section — so the
    beginner_summary can surface the rewrite inline instead of forcing
    the author to open ``sample_scan.md``. The key is omitted entirely
    when no rewrite is present so the absence remains distinguishable
    from an empty string.
    """

    payload: dict[str, Any] = {
        "index": section.get("index"),
        "label": section.get("label") or "",
        "overall": int(section.get("overall") or 0),
        "status": str(section.get("status") or "").upper(),
        "risk": section.get("risk") or "",
        "fix": section.get("fix") or "",
    }
    rewrite = section.get("opening_rewrite")
    if isinstance(rewrite, str):
        cleaned = rewrite.strip()
        if cleaned:
            payload["opening_rewrite"] = cleaned
            source = section.get("rewrite_source")
            if isinstance(source, str):
                source_clean = source.strip()
                if source_clean:
                    payload["rewrite_source"] = source_clean
    return payload


def _weakest_samples_payload(
    sample_json: dict | None,
    *,
    limit: int = 1,
) -> list[dict]:
    """Extract the N highest-risk Kindle-Sample sections.

    Returns an empty list when no sample data is available, no sections
    were scored, or every section is already ``READY`` (no drop-off
    risk worth surfacing in beginner_summary). When risky sections
    exist, returns up to ``limit`` of them as dicts sorted by ``overall``
    ascending (lowest score first), tie-break by ``index`` ascending so
    the order stays stable across runs. ``limit`` is clamped to
    ``[0, WEAKEST_SAMPLE_MAX_LIMIT]`` — ``0`` is honored as an explicit
    mute switch, anything above the cap is reduced to the cap so the
    summary never explodes into the full sample report.
    """

    if not sample_json:
        return []
    sections = sample_json.get("sections") or []
    if not sections:
        return []
    clamped_limit = max(0, min(WEAKEST_SAMPLE_MAX_LIMIT, int(limit)))
    if clamped_limit == 0:
        return []
    risky = [
        section
        for section in sections
        if str(section.get("status") or "").upper() != "READY"
    ]
    if not risky:
        return []
    ordered = sorted(
        risky,
        key=lambda s: (int(s.get("overall") or 0), int(s.get("index") or 0)),
    )
    return [_sample_section_dict(section) for section in ordered[:clamped_limit]]


def _weakest_sample_payload(sample_json: dict | None) -> dict | None:
    """Backwards-compatible thin wrapper around ``_weakest_samples_payload``.

    Returns the single weakest risky section as a dict, or ``None`` when
    no risky section exists. Preserves the old call sites that consume
    one section without iterating.
    """

    payload = _weakest_samples_payload(sample_json, limit=1)
    return payload[0] if payload else None


# Words required before the Amstad FRE score is treated as a measurable
# signal in the beginner summary. Below this the score is mathematically
# defined but not meaningful (a 20-word preface can swing the index by
# 40+ points). We mirror ``MIN_BODY_WORDS_FOR_SIGNAL`` from
# ``modules.readability`` without importing it here to keep this module
# free of upstream coupling.
READABILITY_HIGHLIGHT_MIN_WORDS: int = 60


def _readability_score(readability_json: dict | None) -> int | None:
    """Aggregate readability score (rounded Amstad-FRE) for score_history.

    Returns ``round(overall.fre_score)`` clamped to ``[0, 100]`` when the
    manuscript carries at least ``READABILITY_HIGHLIGHT_MIN_WORDS`` words
    of measurable body text. Returns ``None`` when:

    - no readability JSON was produced (readability_review raised),
    - the overall block is missing,
    - the word count is below the meaningful-signal threshold,
    - ``fre_score`` is missing or not numeric.

    A ``None`` signals "no readability metric possible" so score_history
    records a dash rather than a misleading zero. The score is rounded to
    int because score_history stores all metrics as integers — the half-
    point distinction does not matter for trend visualisation.
    """

    if not readability_json:
        return None
    overall = readability_json.get("overall") or {}
    try:
        word_count = int(overall.get("word_count") or 0)
    except (TypeError, ValueError):
        return None
    if word_count < READABILITY_HIGHLIGHT_MIN_WORDS:
        return None
    fre_raw = overall.get("fre_score")
    if fre_raw is None:
        return None
    try:
        fre = float(fre_raw)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, round(fre)))


def _readability_highlight_payload(
    readability_json: dict | None,
) -> dict | None:
    """Compact German FRE highlight for beginner_summary.

    Surfaces the aggregate Amstad-FRE plus the single weakest chapter so
    the author sees in round 1 whether the prose matches the target
    audience. Returns ``None`` when the manuscript was empty or too
    short for a meaningful measurement — no point telling the author
    "FRE 0.0" before they have written anything.

    The payload carries:

    - ``overall_fre`` (float, rounded to 1 decimal)
    - ``level_label`` (German Amstad band, e.g. "Mittel (B1/B2)")
    - ``target_min`` / ``target_max`` (configurable band, defaults 50/80)
    - ``in_target`` (bool — overall FRE inside the target band)
    - ``overall_fix`` (str — empty when the score sits inside the band)
    - ``weakest_label`` / ``weakest_fre`` / ``weakest_fix`` (the worst
      chapter outside the band, or empty values when every chapter is
      in target).

    Skipping the section entirely when there is nothing measurable keeps
    the summary honest — a hint about readability before chapter text
    exists would be cargo-cult tooling.
    """

    if not readability_json:
        return None
    overall = readability_json.get("overall") or {}
    word_count = int(overall.get("word_count") or 0)
    if word_count < READABILITY_HIGHLIGHT_MIN_WORDS:
        return None
    fre_raw = overall.get("fre_score")
    if fre_raw is None:
        return None
    try:
        overall_fre = float(fre_raw)
    except (TypeError, ValueError):
        return None
    target_min = int(readability_json.get("target_min") or 0)
    target_max = int(readability_json.get("target_max") or 0)
    in_target = bool(
        target_min and target_max and target_min <= overall_fre <= target_max
    )
    overall_fix = str(overall.get("fix") or "").strip()
    weakest_label = ""
    weakest_fre: float | None = None
    weakest_fix = ""
    weakest_index = readability_json.get("weakest_index")
    if weakest_index is not None:
        for chap in readability_json.get("chapters") or []:
            if chap.get("index") == weakest_index:
                weakest_label = str(chap.get("label") or "").strip()
                raw_fre = chap.get("fre_score")
                if raw_fre is not None:
                    try:
                        weakest_fre = float(raw_fre)
                    except (TypeError, ValueError):
                        weakest_fre = None
                weakest_fix = str(chap.get("fix") or "").strip()
                break
    return {
        "overall_fre": round(overall_fre, 1),
        "level_label": str(overall.get("level_label") or "").strip(),
        "target_min": target_min,
        "target_max": target_max,
        "in_target": in_target,
        "overall_fix": overall_fix,
        "weakest_label": weakest_label,
        "weakest_fre": (
            round(weakest_fre, 1) if weakest_fre is not None else None
        ),
        "weakest_fix": weakest_fix,
    }


class PublisherPipeline:
    def __init__(self, config: AppConfig, logger: RunLogger):
        self.config = config
        self.logger = logger
        self.writer = ArtifactWriter(config.project_root / "artifacts", logger)
        self.llm = LLMClient(config, logger)
        skills_dir = config.skills_directory
        if not skills_dir.is_absolute():
            skills_dir = config.project_root / skills_dir
        memory_path = config.memory_path
        if not memory_path.is_absolute():
            memory_path = config.project_root / memory_path
        self.skills = SkillRegistry(skills_dir)
        self.memory = AgentMemory(memory_path)

    def discover(self, input_path: Path) -> list[BookProject]:
        projects = discover_books(
            input_path,
            self.config.skip_directories,
            self.config.supported_files,
            self.config.supplemental_text_directories,
        )
        self.logger.log("discovery_completed", input_path=str(input_path), project_count=len(projects))
        for project in projects:
            self.logger.log("project_discovered", **project.to_json())

        self.writer.write_text("discovery_report.md", render_discovery_markdown(projects, input_path))
        self.writer.write_json("discovery_report.json", {
            "input_path": str(input_path),
            "project_count": len(projects),
            "projects": [project.to_json() for project in projects],
        })
        for project in projects:
            self.memory.remember_project(project)
        self.memory.save()
        return projects

    def _mirror_if_single(self, projects: list[BookProject], filename: str) -> None:
        if self.config.artifact_mirror_single_project and len(projects) == 1:
            self.writer.mirror_single_project_file(projects[0].project_id, filename)

    def _collect_chapter_intros(
        self, project: BookProject
    ) -> list[tuple[str, str]]:
        """Best-effort extraction of (title, first-paragraph) per chapter.

        Reads the manuscript once and returns the clipped intros for the
        LLM bullet-extractor prompt. Returns an empty list when no
        manuscript is configured or extraction raises — the LLM prompt
        falls back to the title-only block and the run continues.
        """

        if not project.manuscript:
            return []
        try:
            chapters = extract_docx_chapters(project.manuscript)
        except Exception as exc:
            self.logger.log(
                "amazon_html_llm_intros_failed",
                project_id=project.project_id,
                error=str(exc),
            )
            return []
        return extract_chapter_intros(chapters)

    def _maybe_apply_chapter_llm_fixes(
        self,
        project: BookProject,
        report: ChapterReport,
    ) -> ChapterReport:
        """Return ``report`` enriched with LLM fix lines for weak chapters.

        Gated by ``AppConfig.chapter_review_llm_fixes_enabled`` AND the
        presence of an ``ANTHROPIC_API_KEY``. Returns the original report
        unchanged when either gate is closed, when there is no weak chapter
        to enrich, when there is no manuscript, or when the LLM call
        produces nothing usable. Any exception inside the LLM extractor is
        logged and converted into a no-op so the deterministic fix lines
        stay intact — never an aborted run.
        """

        if not self.config.chapter_review_llm_fixes_enabled:
            return report
        if not self.llm.api_key:
            self.logger.log(
                "chapter_review_llm_fixes_skipped",
                project_id=project.project_id,
                reason="missing_api_key",
            )
            return report
        risky = [c for c in report.chapters if c.status != "READY"]
        if not risky:
            self.logger.log(
                "chapter_review_llm_fixes_skipped",
                project_id=project.project_id,
                reason="no_weak_chapters",
            )
            return report
        if not project.manuscript:
            return report
        try:
            chapters = extract_docx_chapters(project.manuscript)
            chapter_bodies = {c.index: c.body for c in chapters}
        except Exception as exc:
            self.logger.log(
                "chapter_review_llm_fixes_failed",
                project_id=project.project_id,
                error=str(exc),
                stage="chapter_extraction",
            )
            return report
        try:
            fixes = extract_chapter_fixes_via_llm(
                report,
                chapter_bodies,
                self.llm.complete_json,
            )
        except Exception as exc:
            self.logger.log(
                "chapter_review_llm_fixes_failed",
                project_id=project.project_id,
                error=str(exc),
                stage="llm_call",
            )
            return report
        self.logger.log(
            "chapter_review_llm_fixes_completed",
            project_id=project.project_id,
            fix_count=len(fixes),
            weak_chapter_count=len(risky),
        )
        return apply_chapter_fixes(report, fixes)

    def _maybe_apply_rewrite_variants(
        self,
        project: BookProject,
        report: RewriteReport,
    ) -> RewriteReport:
        """Return ``report`` with LLM rewrite variants appended for weak fields.

        Gated by ``AppConfig.rewrite_llm_variants_enabled`` AND the presence
        of an ``ANTHROPIC_API_KEY``. Works purely on the project's metadata
        (title / subtitle / description) — no manuscript read needed. Returns
        the original report unchanged when either gate is closed, when no
        field has a diagnosis finding, or when the LLM call produces nothing
        usable. Any exception inside the LLM extractor is logged and converted
        into a no-op so the deterministic template variants stay intact —
        never an aborted run.
        """

        if not self.config.rewrite_llm_variants_enabled:
            return report
        if not self.llm.api_key:
            self.logger.log(
                "rewrite_llm_variants_skipped",
                project_id=project.project_id,
                reason="missing_api_key",
            )
            return report
        weak = [bundle for bundle in report.bundles if bundle.diagnosis]
        if not weak:
            self.logger.log(
                "rewrite_llm_variants_skipped",
                project_id=project.project_id,
                reason="no_weak_fields",
            )
            return report
        chapter_intros = self._collect_chapter_intros(project)
        try:
            variants = extract_rewrite_variants_via_llm(
                report,
                self.llm.complete_json,
                chapter_intros=chapter_intros or None,
            )
        except Exception as exc:
            self.logger.log(
                "rewrite_llm_variants_failed",
                project_id=project.project_id,
                error=str(exc),
                stage="llm_call",
            )
            return report
        self.logger.log(
            "rewrite_llm_variants_completed",
            project_id=project.project_id,
            field_count=len(variants),
            weak_field_count=len(weak),
            intro_count=sum(1 for _, intro in chapter_intros if intro),
        )
        return apply_rewrite_variants(report, variants)

    def _maybe_apply_sample_llm_rewrites(
        self,
        project: BookProject,
        sample_scan: SampleScanReport,
    ) -> SampleScanReport:
        """Return ``sample_scan`` enriched with LLM opening-sentence rewrites.

        Gated by ``AppConfig.sample_scan_llm_rewrites_enabled`` AND the
        presence of an ``ANTHROPIC_API_KEY``. Returns the original report
        unchanged when either gate is closed, when there is no risky
        section to rewrite, or when the LLM call produces nothing usable.
        Any exception inside the LLM extractor is logged and converted into
        a no-op so the deterministic fix lines stay intact — never an
        aborted run.
        """

        if not self.config.sample_scan_llm_rewrites_enabled:
            return sample_scan
        if not self.llm.api_key:
            self.logger.log(
                "sample_scan_llm_rewrites_skipped",
                project_id=project.project_id,
                reason="missing_api_key",
            )
            return sample_scan
        risky = [s for s in sample_scan.sections if s.status != "READY"]
        if not risky:
            self.logger.log(
                "sample_scan_llm_rewrites_skipped",
                project_id=project.project_id,
                reason="no_risky_sections",
            )
            return sample_scan
        if not project.manuscript:
            return sample_scan
        try:
            from modules.sample_scan import _docx_paragraph_stream

            paragraphs = _docx_paragraph_stream(project.manuscript)
        except Exception as exc:
            self.logger.log(
                "sample_scan_llm_rewrites_failed",
                project_id=project.project_id,
                error=str(exc),
                stage="paragraph_stream",
            )
            return sample_scan
        section_bodies = section_bodies_from_paragraphs(
            paragraphs,
            config=sample_scan_config_from_app(self.config),
        )
        try:
            rewrites = extract_sample_rewrites_via_llm(
                sample_scan,
                section_bodies,
                self.llm.complete_json,
            )
        except Exception as exc:
            self.logger.log(
                "sample_scan_llm_rewrites_failed",
                project_id=project.project_id,
                error=str(exc),
                stage="llm_call",
            )
            return sample_scan
        self.logger.log(
            "sample_scan_llm_rewrites_completed",
            project_id=project.project_id,
            rewrite_count=len(rewrites),
            risky_section_count=len(risky),
        )
        return apply_sample_rewrites(sample_scan, rewrites)

    def _maybe_extract_amazon_llm_bullets(
        self, project: BookProject, *, chapter_titles: list[str]
    ) -> list[str] | None:
        """Return LLM-extracted Amazon bullets when the toggle + API key are present.

        Returns ``None`` when the LLM-Pass is disabled in config OR no API
        key is configured. Returns an empty list (still ``None``-ish for
        the caller) only when the LLM was called but produced nothing
        usable — both are logged so the run trace shows which path ran.
        Never raises: any exception inside the LLM extractor is converted
        into a logged warning so the deterministic template path takes
        over without aborting the run.
        """

        if not self.config.amazon_html_llm_bullets_enabled:
            return None
        if not self.llm.api_key:
            self.logger.log(
                "amazon_html_llm_bullets_skipped",
                project_id=project.project_id,
                reason="missing_api_key",
            )
            return None
        chapter_intros = self._collect_chapter_intros(project)
        try:
            bullets = extract_amazon_bullets_via_llm(
                project,
                chapter_titles,
                self.llm.complete_json,
                chapter_intros=chapter_intros or None,
            )
        except Exception as exc:
            self.logger.log(
                "amazon_html_llm_bullets_failed",
                project_id=project.project_id,
                error=str(exc),
            )
            return None
        self.logger.log(
            "amazon_html_llm_bullets_completed",
            project_id=project.project_id,
            bullet_count=len(bullets),
            intro_count=sum(1 for _, intro in chapter_intros if intro),
        )
        return bullets or None

    def _maybe_extract_kdp_llm_keywords(
        self, project: BookProject, *, chapter_titles: list[str]
    ) -> list[str] | None:
        """Return LLM-extracted long-tail KDP keyword phrases when enabled.

        Gated by ``AppConfig.kdp_keywords_llm_enabled`` AND a configured API
        key. Returns ``None`` when the pass is disabled, no key is present,
        or the LLM produced nothing usable — each path is logged so the run
        trace shows which branch ran. Never raises: any exception inside the
        extractor is converted into a logged warning so the deterministic
        template path takes over without aborting the run.
        """

        if not self.config.kdp_keywords_llm_enabled:
            return None
        if not self.llm.api_key:
            self.logger.log(
                "kdp_keywords_llm_skipped",
                project_id=project.project_id,
                reason="missing_api_key",
            )
            return None
        chapter_intros = self._collect_chapter_intros(project)
        try:
            phrases = extract_kdp_keywords_via_llm(
                project,
                chapter_titles,
                self.llm.complete_json,
                chapter_intros=chapter_intros or None,
            )
        except Exception as exc:
            self.logger.log(
                "kdp_keywords_llm_failed",
                project_id=project.project_id,
                error=str(exc),
            )
            return None
        self.logger.log(
            "kdp_keywords_llm_completed",
            project_id=project.project_id,
            phrase_count=len(phrases),
            intro_count=sum(1 for _, intro in chapter_intros if intro),
        )
        return phrases or None

    def run_review(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        if not projects:
            return projects
        self.llm.require_api_key()
        for project in projects:
            agent_context = "\n\n".join([
                self.skills.prompt_context(),
                self.memory.prompt_context(project.project_id),
            ])
            self.logger.log("stage_started", project_id=project.project_id, stage="manuscript_review")
            review_md, score_json = manuscript_review(project, self.config, self.llm)
            self.writer.write_text("manuscript_review.md", review_md, project.project_id)
            self.writer.write_json("manuscript_score.json", score_json, project.project_id)

            self.logger.log("stage_started", project_id=project.project_id, stage="voice_preservation")
            voice_md = voice_report(project, self.config, self.llm)
            self.writer.write_text("voice_preservation_report.md", voice_md, project.project_id)

            self.logger.log("stage_started", project_id=project.project_id, stage="amazon_conversion")
            amazon_md = amazon_review(project, self.config, self.llm)
            self.writer.write_text("amazon_conversion_review.md", amazon_md, project.project_id)

            positioning = build_positioning_report(project)
            positioning_md = render_positioning_markdown(project, positioning)
            self.writer.write_text("competitive_positioning.md", positioning_md, project.project_id)
            self.writer.write_json(
                "competitive_positioning.json",
                positioning.to_json(),
                project.project_id,
            )

            context = "\n\n".join([
                agent_context,
                project_metadata(project),
                review_md,
                voice_md,
                amazon_md,
                positioning_md,
            ])
            self.logger.log("stage_started", project_id=project.project_id, stage="publisher_board_review")
            board_md = publisher_board_review(project, context, self.llm)
            self.writer.write_text("publisher_board_review.md", board_md, project.project_id)

            context = "\n\n".join([context, board_md])
            self.logger.log("stage_started", project_id=project.project_id, stage="kdp_checklist")
            checklist_md = checklist(project, context, self.llm)
            self.writer.write_text("kdp_publish_checklist.md", checklist_md, project.project_id)

            summary_context = "\n\n".join([context, checklist_md])
            self.logger.log("stage_started", project_id=project.project_id, stage="executive_summary")
            summary_md = executive_summary(project, summary_context, self.llm)
            self.writer.write_text("final_publisher_summary.md", summary_md, project.project_id)

        for filename in [
            "manuscript_review.md",
            "manuscript_score.json",
            "voice_preservation_report.md",
            "amazon_conversion_review.md",
            "competitive_positioning.md",
            "competitive_positioning.json",
            "publisher_board_review.md",
            "kdp_publish_checklist.md",
            "final_publisher_summary.md",
        ]:
            self._mirror_if_single(projects, filename)
        return projects

    def run_cover(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="cover_review")
            self.writer.write_text("cover_review.md", render_cover_review(project), project.project_id)
        self._mirror_if_single(projects, "cover_review.md")
        return projects

    def run_qa(
        self,
        input_path: Path,
        round_id: str | None = None,
        mode: str = "quick_qa",
    ) -> list[BookProject]:
        projects = self.discover(input_path)
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="industrial_qa")
            agent_context = {
                "skills": self.skills.to_json(),
                "memory": self.memory.snapshot(project.project_id),
            }
            qa = build_industrial_qa(project, agent_context)
            self.memory.remember_qa(project, qa, round_id=round_id)
            self.memory.save()
            self.writer.write_json("industrial_qa_report.json", qa, project.project_id)
            self.writer.write_text("industrial_qa_report.md", render_industrial_qa_markdown(qa), project.project_id)
            chapter_titles: list[str] = []
            chapter_json: dict | None = None
            chapter_md: str | None = None
            try:
                chapter_report = build_chapter_review_report(
                    project,
                    balance_thresholds=balance_thresholds_from_app(self.config),
                )
                chapter_report = self._maybe_apply_chapter_llm_fixes(
                    project, chapter_report
                )
                chapter_md, chapter_json = render_chapter_review(
                    project, chapter_report
                )
                chapter_titles = [
                    str(c.get("title") or "")
                    for c in (chapter_json.get("chapters") or [])
                    if c.get("title")
                ]
            except RuntimeError as exc:
                self.logger.log(
                    "chapter_review_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            weakest_chapters = _weakest_chapter_payload(
                chapter_json,
                limit=self.config.beginner_summary_weakest_limit,
            )
            top_chapter_balance = _top_chapter_balance_payload(chapter_json)
            sample_json: dict | None = None
            try:
                sample_scan = build_sample_scan_report(
                    project,
                    config=sample_scan_config_from_app(self.config),
                )
                sample_scan = self._maybe_apply_sample_llm_rewrites(
                    project, sample_scan
                )
                sample_json = sample_scan.to_json()
            except RuntimeError as exc:
                sample_scan = None
                self.logger.log(
                    "sample_scan_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            weakest_samples = _weakest_samples_payload(
                sample_json,
                limit=self.config.beginner_summary_weakest_sample_limit,
            )
            rewrite_report = build_rewrite_report(project)
            rewrite_report = self._maybe_apply_rewrite_variants(
                project, rewrite_report
            )
            rewrite_json = rewrite_report.to_json()
            top_rewrite = _top_rewrite_payload(rewrite_json)
            delta = self.memory.compare_rounds(project.project_id, current_round_id=round_id)
            round_delta_highlight = _round_delta_payload(delta)
            arc_md: str | None = None
            arc_json: dict | None = None
            try:
                arc_md, arc_json = chapter_arc_review(project)
            except RuntimeError as exc:
                self.logger.log(
                    "chapter_arc_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            top_arc = _top_arc_payload(arc_json)
            arc_score_value: int | None = None
            if arc_json is not None:
                raw_arc = arc_json.get("arc_score")
                if raw_arc is not None:
                    try:
                        arc_score_value = int(raw_arc)
                    except (TypeError, ValueError):
                        arc_score_value = None
            positioning = build_positioning_report(project)
            top_positioning = _top_positioning_payload(
                positioning,
                limit=self.config.beginner_summary_positioning_limit,
            )
            top_collision_risk = _top_collision_risk_payload(positioning)
            positioning_score_value = _positioning_score(positioning)
            balance_score_value = _balance_score(chapter_json)
            readability_md: str | None = None
            readability_json: dict | None = None
            try:
                readability_md, readability_json = readability_review(
                    project,
                    target_min=self.config.readability_target_min,
                    target_max=self.config.readability_target_max,
                )
            except RuntimeError as exc:
                self.logger.log(
                    "readability_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            readability_score_value = _readability_score(readability_json)
            history_path = self.writer.project_dir(project.project_id) / "score_history.json"
            history = load_score_history(history_path, project.project_id)
            history = append_score_history(
                history,
                project,
                qa,
                round_id=round_id,
                mode=mode,
                arc_score=arc_score_value,
                positioning_score=positioning_score_value,
                balance_score=balance_score_value,
                readability_score=readability_score_value,
            )
            score_history_highlight = _score_history_payload(history)
            llm_keywords = self._maybe_extract_kdp_llm_keywords(
                project, chapter_titles=chapter_titles
            )
            kdp_keywords = build_kdp_keywords(project, llm_phrases=llm_keywords)
            top_kdp_keywords = _top_kdp_keywords_payload(
                kdp_keywords,
                limit=self.config.beginner_summary_kdp_keyword_limit,
            )
            persona_report = build_persona_report(project, chapter_titles=chapter_titles)
            top_persona = _top_persona_payload(persona_report)
            persona_match = build_persona_match_report(
                persona_report,
                project.amazon_description,
            )
            persona_match_highlight = _persona_match_payload(persona_match)
            llm_bullets = self._maybe_extract_amazon_llm_bullets(
                project, chapter_titles=chapter_titles
            )
            amazon_html_snippet = build_amazon_description_html(
                project, llm_bullets=llm_bullets
            )
            amazon_html_preview = _amazon_html_preview_payload(amazon_html_snippet)
            readability_highlight = _readability_highlight_payload(readability_json)
            self.writer.write_text(
                "beginner_summary.md",
                render_beginner_summary(
                    project,
                    qa,
                    weakest_chapters=weakest_chapters,
                    weakest_samples=weakest_samples,
                    top_rewrite=top_rewrite,
                    round_delta_highlight=round_delta_highlight,
                    score_history_highlight=score_history_highlight,
                    top_kdp_keywords=top_kdp_keywords,
                    top_positioning=top_positioning,
                    top_collision_risk=top_collision_risk,
                    top_persona=top_persona,
                    top_arc=top_arc,
                    top_chapter_balance=top_chapter_balance,
                    persona_match=persona_match_highlight,
                    llm_fallback=self.llm.fallback_summary(),
                    amazon_html_preview=amazon_html_preview,
                    readability_highlight=readability_highlight,
                ),
                project.project_id,
            )
            if chapter_md is not None and chapter_json is not None:
                self.writer.write_text("chapter_review.md", chapter_md, project.project_id)
                self.writer.write_json("chapter_review.json", chapter_json, project.project_id)
            if readability_md is not None and readability_json is not None:
                self.writer.write_text(
                    "readability.md", readability_md, project.project_id
                )
                self.writer.write_json(
                    "readability.json", readability_json, project.project_id
                )
                self.logger.log(
                    "readability_completed",
                    project_id=project.project_id,
                    fre_score=readability_json.get("overall", {}).get("fre_score"),
                    weakest_index=readability_json.get("weakest_index"),
                )
            if arc_md is not None and arc_json is not None:
                self.writer.write_text("chapter_arc.md", arc_md, project.project_id)
                self.writer.write_json("chapter_arc.json", arc_json, project.project_id)
                self.logger.log(
                    "chapter_arc_completed",
                    project_id=project.project_id,
                    arc_score=arc_json.get("arc_score"),
                    inversions=len(arc_json.get("inversions") or []),
                    missing_phases=arc_json.get("missing_phases") or [],
                )
            self.writer.write_text("kindle_preview_check.md", render_kindle_preview_check(project), project.project_id)
            persona_md = render_persona_report_markdown(project, persona_report)
            match_section = render_persona_match_section(persona_match)
            if match_section:
                persona_md = persona_md.rstrip("\n") + "\n\n" + match_section
            self.writer.write_text(
                "buyer_personas.md",
                persona_md,
                project.project_id,
            )
            persona_json = persona_report.to_json()
            persona_json["match"] = persona_match.to_json()
            self.writer.write_json(
                "buyer_personas.json",
                persona_json,
                project.project_id,
            )
            self.logger.log(
                "buyer_personas_completed",
                project_id=project.project_id,
                niche=persona_report.niche_key,
                persona_count=len(persona_report.personas),
                signal_flags=persona_report.signal_flags,
                match_score=persona_match.overall_score,
                match_status=persona_match.status,
            )
            self.writer.write_text(
                "amazon_research_brief.md",
                render_amazon_research_brief(project, persona_report=persona_report),
                project.project_id,
            )
            self.writer.write_text("competitor_research_template.csv", render_competitor_template_csv(project), project.project_id)
            self.writer.write_text(
                "rewrite_suggestions.md",
                render_rewrite_report_markdown(project, rewrite_report),
                project.project_id,
            )
            self.writer.write_json(
                "rewrite_suggestions.json",
                rewrite_json,
                project.project_id,
            )
            self.writer.write_text(
                "amazon_description.html",
                amazon_html_snippet.html,
                project.project_id,
            )
            self.writer.write_text(
                "amazon_description_report.md",
                render_amazon_description_report_markdown(project, amazon_html_snippet),
                project.project_id,
            )
            self.writer.write_json(
                "amazon_description.json",
                amazon_html_snippet.to_json(),
                project.project_id,
            )
            self.writer.write_text(
                "competitive_positioning.md",
                render_positioning_markdown(project, positioning),
                project.project_id,
            )
            self.writer.write_json(
                "competitive_positioning.json",
                positioning.to_json(),
                project.project_id,
            )
            self.logger.log(
                "competitive_positioning_completed",
                project_id=project.project_id,
                niche=positioning.niche_key,
                niche_confidence=positioning.niche_confidence,
                angle_count=len(positioning.unique_angles),
                risk_count=len(positioning.collision_risks),
            )
            kdp_categories = extract_kdp_categories(project)
            kdp_conflicts = find_keyword_conflicts(kdp_keywords, kdp_categories)
            self.writer.write_text(
                "kdp_keywords.md",
                render_kdp_keywords_report_markdown(
                    project,
                    kdp_keywords,
                    categories=kdp_categories,
                    conflicts=kdp_conflicts,
                ),
                project.project_id,
            )
            self.writer.write_json(
                "kdp_keywords.json",
                {
                    "keywords": [kw.to_json() for kw in kdp_keywords],
                    "categories": list(kdp_categories),
                    "conflicts": [conflict.to_json() for conflict in kdp_conflicts],
                },
                project.project_id,
            )
            if sample_scan is not None and sample_json is not None:
                self.writer.write_text(
                    "sample_scan.md",
                    render_sample_scan_markdown(project, sample_scan),
                    project.project_id,
                )
                self.writer.write_json(
                    "sample_scan.json",
                    sample_json,
                    project.project_id,
                )
            self.writer.write_json("agent_memory_snapshot.json", self.memory.snapshot(project.project_id), project.project_id)

            self.writer.write_json("score_history.json", history, project.project_id)
            self.writer.write_text(
                "score_history.md",
                render_score_history_markdown(project, history),
                project.project_id,
            )
            self.logger.log(
                "score_history_updated",
                project_id=project.project_id,
                entry_count=len(history.get("entries") or []),
                industrial_score=qa.get("industrial_score"),
            )

            if self.config.score_history_graph_enabled:
                chart_dataset = build_chart_dataset(
                    history,
                    project_title=project.title,
                )
                chart_path = (
                    self.writer.project_dir(project.project_id) / "score_history.png"
                )
                result = render_history_chart_png(chart_dataset, chart_path)
                self.logger.log(
                    "score_history_graph",
                    project_id=project.project_id,
                    success=result.success,
                    output_path=str(result.output_path) if result.output_path else None,
                    message=result.message,
                )

            if delta is not None:
                self.writer.write_text(
                    "round_delta.md",
                    render_round_delta_markdown(project, delta),
                    project.project_id,
                )
                self.writer.write_json(
                    "round_delta.json",
                    delta.to_json(),
                    project.project_id,
                )
                self.logger.log(
                    "round_delta_recorded",
                    project_id=project.project_id,
                    has_previous=delta.has_previous,
                    score_delta=delta.score_delta,
                    resolved_count=len(delta.resolved_fixes),
                    persistent_count=len(delta.persistent_fixes),
                    new_count=len(delta.new_fixes),
                )
            self.logger.log(
                "industrial_qa_completed",
                project_id=project.project_id,
                decision=qa["decision"],
                industrial_score=qa["industrial_score"],
            )
        for filename in [
            "industrial_qa_report.json",
            "industrial_qa_report.md",
            "beginner_summary.md",
            "chapter_review.md",
            "chapter_review.json",
            "chapter_arc.md",
            "chapter_arc.json",
            "readability.md",
            "readability.json",
            "kindle_preview_check.md",
            "amazon_research_brief.md",
            "buyer_personas.md",
            "buyer_personas.json",
            "competitor_research_template.csv",
            "rewrite_suggestions.md",
            "rewrite_suggestions.json",
            "amazon_description.html",
            "amazon_description_report.md",
            "amazon_description.json",
            "kdp_keywords.md",
            "kdp_keywords.json",
            "competitive_positioning.md",
            "competitive_positioning.json",
            "sample_scan.md",
            "sample_scan.json",
            "agent_memory_snapshot.json",
            "round_delta.md",
            "round_delta.json",
            "score_history.json",
            "score_history.md",
            "score_history.png",
        ]:
            self._mirror_if_single(projects, filename)
        return projects

    def run_launch(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        if not projects:
            return projects
        self.llm.require_api_key()
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="launch_content")
            launch_md = launch_content(project, self.config, self.llm)
            self.writer.write_text("launch_content.md", launch_md, project.project_id)
        self._mirror_if_single(projects, "launch_content.md")
        return projects

    def run_round(self, input_path: Path, full_review: bool = False) -> dict:
        round_id = make_round_id()
        mode = "full_review" if full_review else "quick_qa"
        self.logger.log("round_started", round_id=round_id, mode=mode, input_path=str(input_path))

        if full_review:
            projects = self.run_all(input_path, round_id=round_id)
        else:
            projects = self.run_qa(input_path, round_id=round_id, mode=mode)
            if projects:
                self.run_cover(input_path)

        summary = snapshot_round(self.writer.artifact_dir, projects, round_id, mode)
        self.writer.write_json("latest_round_summary.json", summary)
        self.logger.log("round_completed", round_id=round_id, mode=mode, project_count=len(projects))
        return summary

    def run_all(self, input_path: Path, round_id: str | None = None) -> list[BookProject]:
        self.run_qa(input_path, round_id=round_id, mode="full_review")
        projects = self.run_review(input_path)
        if not projects:
            return projects
        self.run_cover(input_path)
        self.run_launch(input_path)
        # Final summary already exists from review. Refresh after launch.
        for project in projects:
            parts = [project_metadata(project)]
            project_artifact_dir = self.writer.project_dir(project.project_id)
            for name in [
                "manuscript_review.md",
                "voice_preservation_report.md",
                "amazon_conversion_review.md",
                "publisher_board_review.md",
                "industrial_qa_report.md",
                "cover_review.md",
                "kdp_publish_checklist.md",
                "launch_content.md",
            ]:
                path = project_artifact_dir / name
                if path.exists():
                    parts.append(path.read_text(encoding="utf-8"))
            summary_md = executive_summary(project, "\n\n".join(parts), self.llm)
            self.writer.write_text("final_publisher_summary.md", summary_md, project.project_id)
        self._mirror_if_single(projects, "final_publisher_summary.md")
        return projects
