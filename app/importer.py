"""Import opted-in candidates from a published Google Form CSV.

Free, no-hosting opt-in path: a Google Form collects responses into a Sheet
(published to the web as CSV via GOOGLE_FORM_CSV_URL). Setu *pulls* that CSV and
inserts each row as an opted-in candidate.

Compliance: the Form must carry a consent question. We only import rows where
consent is given, recording consent + the submission timestamp (DPDP record).
Dedupes by email so re-running the import is safe.
"""

import csv
import io
import os
import urllib.error
import urllib.request
from datetime import datetime

from dotenv import load_dotenv
from sqlmodel import Session, select

from app.db import engine
from app.models import Candidate, CandidateStatus, RoleType

load_dotenv()


class ImporterError(RuntimeError):
    """Raised when the Google Form CSV is unconfigured or unreachable."""


def import_from_google_form() -> dict:
    url = os.getenv("GOOGLE_FORM_CSV_URL")
    if not url:
        raise ImporterError("GOOGLE_FORM_CSV_URL not set")
    return _import_rows(_fetch_csv(url))


def _fetch_csv(url: str) -> list[dict]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ImporterError(f"Could not fetch the Google Form CSV: {exc}") from exc
    return list(csv.DictReader(io.StringIO(text)))


def _import_rows(rows: list[dict]) -> dict:
    created = skipped = 0
    with Session(engine) as session:
        existing = {c.email for c in session.exec(select(Candidate)).all() if c.email}
        for row in rows:
            email = _norm_email(_find(row, "email"))
            name = _find(row, "name")
            if not (name and email and _has_consent(row)) or email in existing:
                skipped += 1
                continue
            session.add(
                Candidate(
                    name=name,
                    email=email,
                    whatsapp=_find(row, "whatsapp", "phone"),
                    skills=_split(_find(row, "skill")),
                    role_type=_role_type(_find(row, "role")),
                    seniority=_find(row, "seniority"),
                    years_experience=_int(_find(row, "year", "experience")),
                    expected_pay=_int(_find(row, "expected", "pay", "rate")),
                    location=_find(row, "location", "city"),
                    availability=_find(row, "availab"),
                    consent=True,
                    consent_date=_timestamp(row),
                    status=CandidateStatus.opted_in,
                )
            )
            existing.add(email)
            created += 1
        session.commit()
    return {"created": created, "skipped": skipped}


# --- column matching (Google Form headers are the question titles) --------

def _find(row: dict, *keys: str) -> str | None:
    for header, value in row.items():
        if header and any(k in header.lower() for k in keys) and value and value.strip():
            return value.strip()
    return None


def _has_consent(row: dict) -> bool:
    value = _find(row, "consent", "agree")
    return value is not None and value.strip().lower() not in {"no", "false", "0"}


def _timestamp(row: dict) -> datetime:
    raw = _find(row, "timestamp")
    if raw:
        for fmt in ("%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return datetime.now()


def _role_type(value: str | None) -> RoleType | None:
    if value and value.strip().lower() in {r.value for r in RoleType}:
        return RoleType(value.strip().lower())
    return None


def _norm_email(value: str | None) -> str | None:
    return value.lower() if value else None


def _split(value: str | None) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()] if value else []


def _int(value: str | None) -> int | None:
    if value and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None
