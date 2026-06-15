"""Background scheduler (inbound engine).

Two hands-off jobs, each independently gated by env:
  - Post drafting (SCHEDULE_POSTS=true): drafts a LinkedIn post on a sane cadence.
    Always a *draft*; only auto-publishes when LINKEDIN_AUTOPOST=true and LinkedIn is
    connected (approve-before-publish stays the default otherwise).
  - Google Form import (GOOGLE_FORM_CSV_URL set): pulls new opt-ins into the pool.

The loop runs only if at least one job is enabled.
"""

import asyncio
import logging
import os
from collections import Counter
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app import importer, linkedin, llm, publishing
from app.db import engine
from app.models import JD, Post, PostStatus

logger = logging.getLogger("setu")

_CHECK_INTERVAL_SECONDS = 3600  # re-check hourly; per-job gates handle real spacing


def is_enabled() -> bool:
    return _posts_enabled() or bool(os.getenv("GOOGLE_FORM_CSV_URL"))


def _posts_enabled() -> bool:
    return os.getenv("SCHEDULE_POSTS", "false").strip().lower() == "true"


async def run() -> None:
    logger.info(
        "Scheduler started (posts=%s, import=%s)",
        _posts_enabled(),
        bool(os.getenv("GOOGLE_FORM_CSV_URL")),
    )
    while True:
        try:
            if _posts_enabled():
                _post_tick(float(os.getenv("POST_INTERVAL_DAYS", "3")))
            if os.getenv("GOOGLE_FORM_CSV_URL"):
                _import_tick()
        except Exception as exc:  # never let a bad tick kill the loop
            logger.warning("Scheduler tick failed: %s", exc)
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


def _import_tick() -> None:
    try:
        result = importer.import_from_google_form()
    except importer.ImporterError as exc:
        logger.warning("Scheduled import failed: %s", exc)
        return
    if result["created"]:
        logger.info("Scheduled import: %s new opt-in(s)", result["created"])


def _post_tick(interval_days: float) -> None:
    with Session(engine) as session:
        if not _due(session, interval_days):
            return
        source = _next_topic(session)
        try:
            body = llm.draft_post(source)
        except llm.LLMError as exc:
            logger.warning("Scheduled draft failed: %s", exc)
            return

        post = Post(
            jd_id=source.id if isinstance(source, JD) else None,
            body=body,
            status=PostStatus.draft,
        )
        session.add(post)
        session.commit()
        session.refresh(post)
        logger.info("Scheduled draft post #%s created", post.id)

        if linkedin.autopost_enabled() and linkedin.is_configured():
            publishing.publish(session, post)


def _due(session: Session, interval_days: float) -> bool:
    latest = session.exec(select(Post).order_by(Post.created_at.desc())).first()
    if not latest:
        return True
    return latest.created_at <= datetime.now() - timedelta(days=interval_days)


def _next_topic(session: Session):
    """Rotate across open JDs (least-posted first); fall back to a generic topic."""
    jds = session.exec(select(JD)).all()
    if not jds:
        return "the senior remote contract roles we typically staff"
    counts = Counter(p.jd_id for p in session.exec(select(Post)).all() if p.jd_id)
    return min(jds, key=lambda jd: (counts.get(jd.id, 0), jd.id))
