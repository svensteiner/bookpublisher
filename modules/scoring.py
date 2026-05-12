"""Centralized scoring scale and badge helpers.

Single source of truth for the 0-100 score thresholds and the
🟢/🟡/🔴 badge emojis used across every BookPublisher report
(``industrial_qa``, ``chapter_review``, ``sample_scan``,
``chapter_arc``, ``beginner_summary``, ``score_history``, …).

Before this module existed, ``SCORE_READY = 85`` / ``SCORE_REVIEW = 65``
lived in parallel in four modules and drifted independently. Modules
must now ``from modules.scoring import …`` instead of redefining
constants — that way a future scale tweak happens in one place.

The badge function accepts override thresholds for the rare module
that needs a different scale (e.g. ``persona_match`` runs at 70/40
because token-overlap saturates earlier than gate scores).
"""

from __future__ import annotations


SCORE_READY: int = 85
SCORE_REVIEW: int = 65

SCORE_BADGE_READY: str = "🟢"
SCORE_BADGE_REVIEW: str = "🟡"
SCORE_BADGE_FIX: str = "🔴"

STATUS_READY: str = "READY"
STATUS_REVIEW: str = "REVIEW"
STATUS_FIX: str = "FIX"


def score_badge(
    score: int,
    *,
    blocking: bool = False,
    ready_threshold: int = SCORE_READY,
    review_threshold: int = SCORE_REVIEW,
) -> tuple[str, str]:
    """Return (emoji, status) for a 0-100 score under the unified scheme.

    ``blocking=True`` forces the FIX badge regardless of the numeric
    score — used by gates that declared ``status="FIX"`` explicitly
    despite a passing score (e.g. a metadata gate that completed but
    flagged a hard-blocker fix).

    ``ready_threshold`` / ``review_threshold`` allow modules with a
    different scale to reuse this helper (e.g. ``persona_match`` uses
    70/40 because token-overlap saturates earlier than gate scores).
    """

    if blocking:
        return SCORE_BADGE_FIX, STATUS_FIX
    if score >= ready_threshold:
        return SCORE_BADGE_READY, STATUS_READY
    if score >= review_threshold:
        return SCORE_BADGE_REVIEW, STATUS_REVIEW
    return SCORE_BADGE_FIX, STATUS_FIX


def status_for(score: int, *, blocking: bool = False) -> str:
    """Convenience wrapper returning only the status string."""

    _, status = score_badge(score, blocking=blocking)
    return status
