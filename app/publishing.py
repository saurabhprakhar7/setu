"""Publish a Post row to LinkedIn and record the outcome. Shared by the web
routes (manual Approve) and the scheduler (auto cadence)."""

import logging
from datetime import datetime

from sqlmodel import Session

from app import linkedin
from app.models import Post, PostStatus

logger = logging.getLogger("setu")


def publish(session: Session, post: Post) -> None:
    if not post.body.strip():
        post.status = PostStatus.failed
        post.error = "Cannot publish an empty post."
    else:
        try:
            post.linkedin_urn = linkedin.publish_post(post.body)
            post.status = PostStatus.published
            post.published_at = datetime.now()
            post.error = None
        except linkedin.LinkedInError as exc:
            logger.warning("LinkedIn publish failed for post %s: %s", post.id, exc)
            post.status = PostStatus.failed
            post.error = str(exc)
    session.add(post)
    session.commit()
