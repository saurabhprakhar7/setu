"""Candidate ↔ JD matching.

Scoring weights follow the spec's order of importance: skill overlap is by far
the heaviest, then years of experience (a near-hard filter), then the softer
fit signals. A candidate below the JD's minimum experience is heavily penalised
so they sink to the bottom — but they stay visible with the experience check,
rather than vanishing silently.
"""

from dataclasses import dataclass

from app.models import Candidate, JD

W_SKILLS = 0.60
W_EXPERIENCE = 0.20
W_SENIORITY = 0.10
W_REMOTE = 0.05
W_PAY = 0.05

# Applied to the whole score when the candidate is below min years experience.
EXPERIENCE_FAIL_PENALTY = 0.4

# Minimum score (and a passing experience check) for a candidate to be worth outreach.
MATCH_THRESHOLD = 50


@dataclass
class MatchResult:
    candidate_id: int
    name: str
    score: int  # 0–100
    matched_skills: list[str]
    missing_skills: list[str]
    experience_ok: bool
    years_experience: int | None
    required_min: int | None
    seniority_ok: bool
    remote_ok: bool
    pay_ok: bool


def score_candidate(candidate: Candidate, jd: JD) -> MatchResult:
    matched, missing, skill_score = _skill_overlap(candidate, jd)
    experience_ok = _experience_ok(candidate, jd)
    seniority_ok = _seniority_ok(candidate, jd)
    remote_ok = _remote_ok(candidate, jd)
    pay_ok = _pay_ok(candidate, jd)

    base = (
        W_SKILLS * skill_score
        + W_EXPERIENCE * float(experience_ok)
        + W_SENIORITY * float(seniority_ok)
        + W_REMOTE * float(remote_ok)
        + W_PAY * float(pay_ok)
    )
    if not experience_ok:
        base *= EXPERIENCE_FAIL_PENALTY

    return MatchResult(
        candidate_id=candidate.id,
        name=candidate.name,
        score=round(base * 100),
        matched_skills=matched,
        missing_skills=missing,
        experience_ok=experience_ok,
        years_experience=candidate.years_experience,
        required_min=jd.min_years_experience,
        seniority_ok=seniority_ok,
        remote_ok=remote_ok,
        pay_ok=pay_ok,
    )


def rank_candidates(candidates: list[Candidate], jd: JD) -> list[MatchResult]:
    results = [score_candidate(c, jd) for c in candidates]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


# --- individual signals ---------------------------------------------------

def _norm(value: str) -> str:
    return value.strip().lower()


def _skill_overlap(candidate: Candidate, jd: JD) -> tuple[list[str], list[str], float]:
    stack = jd.stack or []
    have = {_norm(s) for s in (candidate.skills or [])}
    matched = [s for s in stack if _norm(s) in have]
    missing = [s for s in stack if _norm(s) not in have]
    score = len(matched) / len(stack) if stack else 0.0
    return matched, missing, score


def _experience_ok(candidate: Candidate, jd: JD) -> bool:
    if jd.min_years_experience is None:
        return True
    return (
        candidate.years_experience is not None
        and candidate.years_experience >= jd.min_years_experience
    )


def _seniority_ok(candidate: Candidate, jd: JD) -> bool:
    if not jd.seniority:
        return True
    return bool(candidate.seniority) and _norm(candidate.seniority) == _norm(jd.seniority)


def _remote_ok(candidate: Candidate, jd: JD) -> bool:
    if jd.remote:
        return candidate.remote_ok
    # Onsite role: fall back to a loose location match when both are known.
    if jd.location and candidate.location:
        a, b = _norm(jd.location), _norm(candidate.location)
        return a in b or b in a
    return True


def _pay_ok(candidate: Candidate, jd: JD) -> bool:
    if jd.rate is None or candidate.expected_pay is None:
        return True
    return candidate.expected_pay <= jd.rate
