"""Unit tests for modules.scoring — the central scoring scale."""

from __future__ import annotations

from modules.scoring import (
    SCORE_BADGE_FIX,
    SCORE_BADGE_READY,
    SCORE_BADGE_REVIEW,
    SCORE_READY,
    SCORE_REVIEW,
    STATUS_FIX,
    STATUS_READY,
    STATUS_REVIEW,
    score_badge,
    status_for,
)


def test_thresholds_are_eighty_five_and_sixty_five():
    """The unified 85/65 scale is the single source of truth."""
    assert SCORE_READY == 85
    assert SCORE_REVIEW == 65


def test_badge_emojis_match_unified_scheme():
    assert SCORE_BADGE_READY == "🟢"
    assert SCORE_BADGE_REVIEW == "🟡"
    assert SCORE_BADGE_FIX == "🔴"


def test_score_badge_ready_at_threshold():
    badge, status = score_badge(85)
    assert badge == SCORE_BADGE_READY
    assert status == STATUS_READY


def test_score_badge_review_at_threshold():
    badge, status = score_badge(65)
    assert badge == SCORE_BADGE_REVIEW
    assert status == STATUS_REVIEW


def test_score_badge_fix_below_review_threshold():
    badge, status = score_badge(64)
    assert badge == SCORE_BADGE_FIX
    assert status == STATUS_FIX


def test_score_badge_high_score_is_ready():
    assert score_badge(100) == (SCORE_BADGE_READY, STATUS_READY)


def test_score_badge_blocking_forces_fix_regardless_of_score():
    badge, status = score_badge(95, blocking=True)
    assert badge == SCORE_BADGE_FIX
    assert status == STATUS_FIX


def test_score_badge_custom_thresholds_for_alternate_scale():
    # persona_match uses 70/40 because token overlap saturates earlier
    badge, status = score_badge(70, ready_threshold=70, review_threshold=40)
    assert (badge, status) == (SCORE_BADGE_READY, STATUS_READY)

    badge, status = score_badge(40, ready_threshold=70, review_threshold=40)
    assert (badge, status) == (SCORE_BADGE_REVIEW, STATUS_REVIEW)

    badge, status = score_badge(39, ready_threshold=70, review_threshold=40)
    assert (badge, status) == (SCORE_BADGE_FIX, STATUS_FIX)


def test_status_for_returns_only_status_string():
    assert status_for(90) == STATUS_READY
    assert status_for(70) == STATUS_REVIEW
    assert status_for(30) == STATUS_FIX
    assert status_for(90, blocking=True) == STATUS_FIX


def test_score_badge_zero_is_fix():
    assert score_badge(0) == (SCORE_BADGE_FIX, STATUS_FIX)


def test_industrial_module_re_exports_unified_constants():
    """Refactor safety: industrial.py must continue to re-export the same
    SCORE_READY_THRESHOLD / SCORE_REVIEW_THRESHOLD numbers so downstream
    code that imports them keeps working."""
    from modules import industrial

    assert industrial.SCORE_READY_THRESHOLD == SCORE_READY
    assert industrial.SCORE_REVIEW_THRESHOLD == SCORE_REVIEW
    assert industrial.score_badge is score_badge


def test_chapters_module_re_exports_unified_constants():
    from modules import chapters

    assert chapters.SCORE_READY == SCORE_READY
    assert chapters.SCORE_REVIEW == SCORE_REVIEW


def test_chapter_arc_module_re_exports_unified_constants():
    from modules import chapter_arc

    assert chapter_arc.SCORE_READY == SCORE_READY
    assert chapter_arc.SCORE_REVIEW == SCORE_REVIEW


def test_sample_scan_module_re_exports_unified_constants():
    from modules import sample_scan

    assert sample_scan.SCORE_READY == SCORE_READY
    assert sample_scan.SCORE_REVIEW == SCORE_REVIEW
