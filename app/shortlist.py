"""Compile qualifying matches into client-ready artifacts: a paste-able text
block for the client WhatsApp group, and a CSV download."""

import csv
import io

from app import matching
from app.models import JD, Candidate

Row = tuple[Candidate, matching.MatchResult]


def select(candidates: list[Candidate], jd: JD) -> list[Row]:
    """Ranked (candidate, result) pairs that clear the outreach bar."""
    results = matching.rank_candidates(candidates, jd)
    by_id = {c.id: c for c in candidates}
    return [
        (by_id[r.candidate_id], r)
        for r in results
        if r.experience_ok and r.score >= matching.MATCH_THRESHOLD
    ]


def to_text(jd: JD, rows: list[Row]) -> str:
    header = f"Shortlist — {jd.role}" + (f" ({jd.client})" if jd.client else "")
    lines = [header, ""]
    for i, (c, r) in enumerate(rows, 1):
        bits = []
        if c.seniority:
            bits.append(c.seniority)
        elif c.role_type:
            bits.append(_enum_value(c.role_type))
        if c.years_experience is not None:
            bits.append(f"{c.years_experience}y")
        lines.append(f"{i}. {c.name}" + (" — " + ", ".join(bits) if bits else ""))

        skills = r.matched_skills or (c.skills or [])
        if skills:
            lines.append(f"   Skills: {', '.join(skills)}")

        meta = []
        if c.location:
            meta.append(c.location)
        meta.append("remote" if c.remote_ok else "onsite")
        if c.availability:
            meta.append(f"avail {c.availability}")
        if c.expected_pay is not None:
            meta.append(f"expects {c.expected_pay}")
        lines.append("   " + " · ".join(meta))
        if c.resume_path:
            lines.append(f"   Resume: {c.resume_path}")
        lines.append("")
    return "\n".join(lines).strip()


def to_csv(rows: list[Row]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "name", "email", "whatsapp", "role_type", "seniority", "years_experience",
            "skills", "expected_pay", "availability", "location", "remote_ok",
            "resume_path", "score",
        ]
    )
    for c, r in rows:
        writer.writerow(
            [
                c.name, c.email, c.whatsapp or "", _enum_value(c.role_type) or "",
                c.seniority or "",
                "" if c.years_experience is None else c.years_experience,
                ", ".join(c.skills or []),
                "" if c.expected_pay is None else c.expected_pay,
                c.availability or "", c.location or "",
                "yes" if c.remote_ok else "no", c.resume_path or "", r.score,
            ]
        )
    return buf.getvalue()


def _enum_value(value):
    return getattr(value, "value", value)
