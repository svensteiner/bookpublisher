from __future__ import annotations

import json
from pathlib import Path

from modules.agent_core import AgentMemory, SkillRegistry
from modules.amazon_html import (
    build_amazon_description_html,
    render_amazon_description_report_markdown,
)
from modules.artifacts import ArtifactWriter
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
    find_keyword_conflicts,
    render_kdp_keywords_report_markdown,
)
from modules.llm import LLMClient
from modules.persona_match import build_persona_match_report, render_persona_match_section
from modules.personas import PersonaReport, build_persona_report, render_persona_report_markdown
from modules.review import (
    amazon_review,
    chapter_arc_review,
    chapter_review,
    checklist,
    executive_summary,
    launch_content,
    manuscript_review,
    publisher_board_review,
    project_metadata,
    voice_report,
)
from modules.release_assets import (
    render_amazon_research_brief,
    render_competitor_template_csv,
    render_kindle_preview_check,
)
from modules.rewrites import build_rewrite_report, render_rewrite_report_markdown
from modules.round_delta import RoundDelta, render_round_delta_markdown
from modules.rounds import make_round_id, snapshot_round
from modules.run_logger import RunLogger
from modules.sample_scan import (
    build_sample_scan_report,
    render_sample_scan_markdown,
    sample_scan_config_from_app,
)
from modules.score_history import (
    append_score_history,
    load_score_history,
    render_score_history_markdown,
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
    cap = max(0, limit)
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
    return {
        "headline": headline,
        "lead": lead,
        "bullets": tuple(bullets),
        "char_count": char_count,
        "keyword_score": keyword_score,
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


def _top_positioning_payload(
    positioning: PositioningReport | None,
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
    - ``pitch``: the one-sentence positioning pitch ready to paste.
    - ``niche_label`` / ``niche_confidence``: helps the author judge
      whether the niche detection is plausible.
    - ``audience``: surfaced as a separate field so the renderer can
      build a short "Wer kauft das?" line without re-parsing the pitch.

    The helper is immutable: it copies values out so a caller mutating
    the returned dict cannot affect the source report.
    """

    if positioning is None:
        return None
    angles = list(positioning.unique_angles or [])
    if not angles:
        return None
    top = angles[0]
    if top.key == "kein_signal" or top.strength <= 0:
        return None
    return {
        "angle_key": top.key,
        "angle_claim": top.claim,
        "angle_evidence": top.evidence,
        "angle_strength": int(top.strength),
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


def _weakest_sample_payload(sample_json: dict | None) -> dict | None:
    """Extract the highest-risk Kindle-Sample section from a sample-scan payload.

    Returns ``None`` when no sample data is available, no sections were
    scored, or the weakest section is already ``READY`` (no drop-off
    risk worth surfacing in beginner_summary). When a risky section
    exists, returns a dict with ``index``, ``label``, ``overall``,
    ``status``, ``risk`` and ``fix`` — the fields
    ``render_beginner_summary`` needs.
    """

    if not sample_json:
        return None
    sections = sample_json.get("sections") or []
    if not sections:
        return None
    weakest = min(sections, key=lambda s: int(s.get("overall") or 0))
    status = str(weakest.get("status") or "").upper()
    if status == "READY":
        return None
    return {
        "index": weakest.get("index"),
        "label": weakest.get("label") or "",
        "overall": int(weakest.get("overall") or 0),
        "status": status,
        "risk": weakest.get("risk") or "",
        "fix": weakest.get("fix") or "",
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
                chapter_md, chapter_json = chapter_review(project)
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
            weakest_chapters = _weakest_chapter_payload(chapter_json, limit=3)
            top_chapter_balance = _top_chapter_balance_payload(chapter_json)
            sample_json: dict | None = None
            try:
                sample_scan = build_sample_scan_report(
                    project,
                    config=sample_scan_config_from_app(self.config),
                )
                sample_json = sample_scan.to_json()
            except RuntimeError as exc:
                sample_scan = None
                self.logger.log(
                    "sample_scan_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            weakest_sample = _weakest_sample_payload(sample_json)
            rewrite_report = build_rewrite_report(project)
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
            top_positioning = _top_positioning_payload(positioning)
            top_collision_risk = _top_collision_risk_payload(positioning)
            positioning_score_value = _positioning_score(positioning)
            balance_score_value = _balance_score(chapter_json)
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
            )
            score_history_highlight = _score_history_payload(history)
            kdp_keywords = build_kdp_keywords(project)
            top_kdp_keywords = _top_kdp_keywords_payload(kdp_keywords)
            persona_report = build_persona_report(project, chapter_titles=chapter_titles)
            top_persona = _top_persona_payload(persona_report)
            persona_match = build_persona_match_report(
                persona_report,
                project.amazon_description,
            )
            persona_match_highlight = _persona_match_payload(persona_match)
            amazon_html_snippet = build_amazon_description_html(project)
            amazon_html_preview = _amazon_html_preview_payload(amazon_html_snippet)
            self.writer.write_text(
                "beginner_summary.md",
                render_beginner_summary(
                    project,
                    qa,
                    weakest_chapters=weakest_chapters,
                    weakest_sample=weakest_sample,
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
                ),
                project.project_id,
            )
            if chapter_md is not None and chapter_json is not None:
                self.writer.write_text("chapter_review.md", chapter_md, project.project_id)
                self.writer.write_json("chapter_review.json", chapter_json, project.project_id)
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
