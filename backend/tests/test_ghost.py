"""Tests for ghost-job detection."""

from app.pipeline.ghost import compute_ghost_score, tier_penalty


def _score(**kw):
    base = {"posted_at": None, "times_seen": 1, "description": "", "salary_text": "", "location": ""}
    base.update(kw)
    return compute_ghost_score(**base)


def test_fresh_normal_job_low_score():
    desc = "We are hiring a QA engineer. " * 20  # >300 chars, real content
    score, signals = _score(posted_at="2026-10-01", description=desc, salary_text="$3000-4000 monthly")
    assert score < 30


def test_evergreen_phrasing_flagged():
    score, signals = _score(
        description="Join our talent pool for future opportunities in QA. " * 10,
    )
    assert score >= 30
    assert any("evergreen" in s for s in signals)


def test_old_posting_flagged():
    score, signals = _score(posted_at="2026-06-01", description="x" * 400)
    assert score >= 35
    assert any("still open" in s for s in signals)


def test_repost_history_flagged():
    score, signals = _score(times_seen=4, description="x" * 400)
    assert score >= 25
    assert any("reposted" in s for s in signals)


def test_thin_vague_posting_flagged():
    score, _ = _score(description="Various roles. Competitive salary.")
    assert score >= 15


def test_penalty_ladder():
    assert tier_penalty(75) == 12.0
    assert tier_penalty(55) == 7.0
    assert tier_penalty(35) == 3.0
    assert tier_penalty(10) == 0.0
