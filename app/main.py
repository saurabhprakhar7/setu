"""Setu — FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import TypeVar

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app import (
    email_sender,
    importer,
    linkedin,
    llm,
    matching,
    publishing,
    scheduler,
    shortlist,
    sourcing,
)
from app.db import get_session, init_db
from app.models import (
    JD,
    Candidate,
    CandidateStatus,
    Message,
    MessageChannel,
    MessageStatus,
    Post,
    PostStatus,
    RoleType,
    Segment,
)
from app.storage import save_resume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("setu")

E = TypeVar("E", bound=Enum)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = None
    if scheduler.is_enabled():
        task = asyncio.create_task(scheduler.run())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Setu", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    q = request.query_params
    notice, notice_kind = None, None
    if added := q.get("added"):
        notice, notice_kind = f"Added {added} sourced candidate(s).", "ok"
    elif (imported := q.get("imported")) is not None:
        notice = f"Imported {imported} opted-in candidate(s) from the Google Form ({q.get('skipped', 0)} skipped)."
        notice_kind = "ok"
    elif q.get("error") == "parse":
        notice, notice_kind = "Couldn't parse that — try pasting cleaner profile text.", "error"
    elif q.get("import_error"):
        notice, notice_kind = "Google Form import failed — check GOOGLE_FORM_CSV_URL.", "error"
    return _render_dashboard(request, session, notice=notice, notice_kind=notice_kind)


@app.post("/candidates/quick-add")
async def candidates_quick_add(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    text = _text(form, "text")
    if not text:
        return RedirectResponse("/dashboard", status_code=303)

    try:
        parsed = llm.parse_candidates(text)
    except llm.LLMError as exc:
        logger.warning("Quick-add parse failed: %s", exc)
        return RedirectResponse("/dashboard?error=parse", status_code=303)

    created = 0
    for c in parsed:
        if not c.get("name"):
            continue
        session.add(
            Candidate(
                name=c["name"],
                email=c.get("email"),
                whatsapp=c.get("whatsapp"),
                skills=c.get("skills") or [],
                role_type=_enum(RoleType, c.get("role_type")),
                seniority=c.get("seniority"),
                years_experience=c.get("years_experience"),
                location=c.get("location"),
                status=CandidateStatus.sourced,  # no consent — automation won't touch them
            )
        )
        created += 1
    session.commit()
    return RedirectResponse(f"/dashboard?added={created}", status_code=303)


@app.post("/candidate")
async def candidate_add(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    name, email = _text(form, "name"), _text(form, "email")
    if not (name and email):
        return RedirectResponse("/dashboard", status_code=303)

    # Pull side: sourced and NOT consented — outreach automation will never touch them.
    candidate = Candidate(
        name=name,
        email=email,
        whatsapp=_text(form, "whatsapp"),
        skills=_skills(form, "skills"),
        role_type=_enum(RoleType, _text(form, "role_type")),
        seniority=_text(form, "seniority"),
        years_experience=_int(form, "years_experience"),
        segment=_enum(Segment, _text(form, "segment")),
        current_pay=_int(form, "current_pay"),
        expected_pay=_int(form, "expected_pay"),
        location=_text(form, "location"),
        remote_ok=_checked(form, "remote_ok"),
        availability=_text(form, "availability"),
        status=CandidateStatus.sourced,
    )
    session.add(candidate)
    session.commit()
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/candidate/{candidate_id}/status")
async def candidate_status(
    candidate_id: int, request: Request, session: Session = Depends(get_session)
):
    candidate = _get_candidate(session, candidate_id)
    form = await request.form()
    new_status = _enum(CandidateStatus, _text(form, "status"))
    if new_status:
        candidate.status = new_status
        session.add(candidate)
        session.commit()
    nxt = _text(form, "next")
    return RedirectResponse(nxt if nxt and nxt.startswith("/") else "/dashboard", status_code=303)


_SOURCING_KEYS = ("role", "skills", "location", "seniority", "segment", "company")


def _sourcing_context(filters: dict, invite=None) -> dict:
    return {
        "filters": filters,
        "searches": sourcing.build_searches(**filters) if any(filters.values()) else None,
        "segments": [s.value for s in Segment],
        "companies": sourcing.COMPANIES,
        "role_presets": sourcing.role_presets(),
        "company_presets": sourcing.company_presets(),
        "invite": invite,
    }


@app.get("/sourcing")
def sourcing_page(request: Request):
    filters = {k: request.query_params.get(k, "") for k in _SOURCING_KEYS}
    return templates.TemplateResponse(request, "sourcing.html", _sourcing_context(filters))


@app.post("/sourcing/invite")
def sourcing_invite(request: Request):
    try:
        invite = llm.draft_form_invite()
    except llm.LLMError as exc:
        invite = f"(Draft unavailable: {exc})"
    filters = {k: "" for k in _SOURCING_KEYS}
    return templates.TemplateResponse(request, "sourcing.html", _sourcing_context(filters, invite))


@app.get("/sourced")
def sourced_list(request: Request, session: Session = Depends(get_session)):
    sourced = session.exec(
        select(Candidate).where(Candidate.status == CandidateStatus.sourced)
    ).all()
    return templates.TemplateResponse(
        request, "sourced.html", {"candidates": sourced, "invites": None}
    )


@app.post("/sourced/invites")
def sourced_invites(request: Request, session: Session = Depends(get_session)):
    sourced = session.exec(
        select(Candidate).where(Candidate.status == CandidateStatus.sourced)
    ).all()
    invites = []
    for c in sourced:
        try:
            body = llm.draft_outreach(c)
        except llm.LLMError as exc:
            body = f"(Draft unavailable: {exc})"
        invites.append({"id": c.id, "name": c.name, "body": body})
    return templates.TemplateResponse(
        request, "sourced.html", {"candidates": sourced, "invites": invites}
    )


@app.post("/candidates/import")
def candidates_import():
    try:
        result = importer.import_from_google_form()
    except importer.ImporterError as exc:
        logger.warning("Google Form import failed: %s", exc)
        return RedirectResponse("/dashboard?import_error=1", status_code=303)
    return RedirectResponse(
        f"/dashboard?imported={result['created']}&skipped={result['skipped']}",
        status_code=303,
    )


@app.post("/candidate/{candidate_id}/draft")
def candidate_draft(
    candidate_id: int, request: Request, session: Session = Depends(get_session)
):
    candidate = _get_candidate(session, candidate_id)
    try:
        draft = llm.draft_outreach(candidate)
    except llm.LLMError as exc:
        draft = f"(Draft unavailable: {exc})"
    return _render_dashboard(request, session, draft=draft, draft_for=candidate.name)


@app.get("/optin")
def optin_form(request: Request):
    return templates.TemplateResponse(request, "optin.html", _form_context())


@app.post("/optin")
async def optin_submit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()

    if not _checked(form, "consent"):
        context = _form_context(error="Consent is required to join the pool.")
        return templates.TemplateResponse(request, "optin.html", context, status_code=400)

    candidate = Candidate(
        name=_text(form, "name"),
        email=_text(form, "email"),
        whatsapp=_text(form, "whatsapp"),
        skills=_skills(form, "skills"),
        role_type=_enum(RoleType, _text(form, "role_type")),
        seniority=_text(form, "seniority"),
        years_experience=_int(form, "years_experience"),
        segment=_enum(Segment, _text(form, "segment")),
        current_pay=_int(form, "current_pay"),
        expected_pay=_int(form, "expected_pay"),
        location=_text(form, "location"),
        remote_ok=_checked(form, "remote_ok"),
        availability=_text(form, "availability"),
        resume_path=await save_resume(form.get("resume")),
        consent=True,
        consent_date=datetime.now(),
        status=CandidateStatus.opted_in,
    )
    session.add(candidate)
    session.commit()

    return templates.TemplateResponse(request, "optin_success.html", {"name": candidate.name})


@app.get("/jd/new")
def jd_form(request: Request):
    return templates.TemplateResponse(request, "jd_new.html", {"error": None})


@app.post("/jd")
async def jd_create(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    jd_text = _text(form, "jd_text")
    if not jd_text:
        context = {"error": "Paste a job description first."}
        return templates.TemplateResponse(request, "jd_new.html", context, status_code=400)

    try:
        parsed = llm.parse_jd(jd_text)
    except llm.LLMError as exc:
        context = {"error": f"Could not parse the JD: {exc}"}
        return templates.TemplateResponse(request, "jd_new.html", context, status_code=502)

    jd = JD(**parsed)
    # Let the human override client/source, which a JD often omits.
    jd.client = _text(form, "client") or jd.client
    jd.source = _text(form, "source") or jd.source
    session.add(jd)
    session.commit()
    session.refresh(jd)

    return templates.TemplateResponse(request, "jd_detail.html", {"jd": jd})


@app.get("/jd/{jd_id}/matches")
def jd_matches(jd_id: int, request: Request, session: Session = Depends(get_session)):
    jd = _get_jd(session, jd_id)
    results = matching.rank_candidates(_opted_in_candidates(session), jd)

    return templates.TemplateResponse(request, "matches.html", {"jd": jd, "results": results})


@app.post("/jd/{jd_id}/outreach")
def jd_outreach(jd_id: int, request: Request, session: Session = Depends(get_session)):
    jd = _get_jd(session, jd_id)
    opted_in = _opted_in_candidates(session)
    by_id = {c.id: c for c in opted_in}
    results = matching.rank_candidates(opted_in, jd)

    outcomes = []
    for result in results:
        if not (result.experience_ok and result.score >= matching.MATCH_THRESHOLD):
            continue
        candidate = by_id[result.candidate_id]
        # Compliance hard guard: never message anyone without recorded consent.
        if not candidate.consent:
            continue
        outcomes.append(_run_outreach(session, candidate, jd, result.score))

    session.commit()
    return templates.TemplateResponse(request, "outreach.html", {"jd": jd, "outcomes": outcomes})


def _run_outreach(session: Session, candidate: Candidate, jd: JD, score: int) -> dict:
    """Draft email + WhatsApp, send the email, log both. Returns a view dict."""
    outcome = {"name": candidate.name, "score": score, "whatsapp_body": None}
    try:
        email_draft = llm.draft_message(candidate, jd, "email")
        whatsapp_draft = llm.draft_message(candidate, jd, "whatsapp")
    except llm.LLMError as exc:
        logger.warning("Drafting failed for %s: %s", candidate.email, exc)
        outcome["email_status"] = "draft failed"
        return outcome

    subject = email_draft["subject"] or f"Contract role: {jd.role}"
    email_msg = Message(
        candidate_id=candidate.id,
        jd_id=jd.id,
        channel=MessageChannel.email,
        subject=subject,
        body=email_draft["body"],
    )
    try:
        email_sender.send_email(candidate.email, subject, email_draft["body"])
        email_msg.status = MessageStatus.sent
        email_msg.sent_at = datetime.now()
    except email_sender.EmailError as exc:
        logger.warning("Email send failed for %s: %s", candidate.email, exc)
        email_msg.status = MessageStatus.failed
    session.add(email_msg)

    # WhatsApp is never auto-sent — log the draft for the user to send manually.
    whatsapp_msg = Message(
        candidate_id=candidate.id,
        jd_id=jd.id,
        channel=MessageChannel.whatsapp,
        body=whatsapp_draft["body"],
    )
    session.add(whatsapp_msg)

    outcome["email_status"] = email_msg.status.value
    outcome["whatsapp_body"] = whatsapp_draft["body"]
    return outcome


@app.get("/jd/{jd_id}/shortlist")
def jd_shortlist(jd_id: int, request: Request, session: Session = Depends(get_session)):
    jd = _get_jd(session, jd_id)
    rows = shortlist.select(_opted_in_candidates(session), jd)
    context = {"jd": jd, "rows": rows, "text": shortlist.to_text(jd, rows)}
    return templates.TemplateResponse(request, "shortlist.html", context)


@app.get("/jd/{jd_id}/shortlist.csv")
def jd_shortlist_csv(jd_id: int, session: Session = Depends(get_session)):
    jd = _get_jd(session, jd_id)
    rows = shortlist.select(_opted_in_candidates(session), jd)
    return Response(
        content=shortlist.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="shortlist-jd-{jd_id}.csv"'},
    )


@app.get("/posts")
def posts_list(request: Request, session: Session = Depends(get_session)):
    posts = session.exec(select(Post).order_by(Post.created_at.desc())).all()
    jds = session.exec(select(JD)).all()
    context = {
        "posts": posts,
        "jds": jds,
        "linkedin_ready": linkedin.is_configured(),
        "cadence_warning": _cadence_warning(posts),
    }
    return templates.TemplateResponse(request, "posts.html", context)


def _cadence_warning(posts: list[Post]) -> str | None:
    cutoff = datetime.now() - timedelta(days=7)
    recent = sum(
        1
        for p in posts
        if p.status == PostStatus.published and p.published_at and p.published_at >= cutoff
    )
    if recent >= linkedin.POSTS_PER_WEEK_LIMIT:
        return (
            f"You've published {recent} posts in the last 7 days. "
            "LinkedIn favours ~2–3/week — consider spacing the next one out."
        )
    return None


@app.post("/posts")
async def post_create(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    jd_id = _int(form, "jd_id")
    prompt = _text(form, "prompt")

    if jd_id is not None:
        source = _get_jd(session, jd_id)
    elif prompt:
        source = prompt
    else:
        return RedirectResponse("/posts", status_code=303)

    try:
        post = Post(jd_id=jd_id, body=llm.draft_post(source), status=PostStatus.draft)
    except llm.LLMError as exc:
        logger.warning("Post drafting failed: %s", exc)
        post = Post(jd_id=jd_id, body="", status=PostStatus.failed, error=str(exc))
    session.add(post)
    session.commit()

    # Auto-publish only when explicitly enabled and LinkedIn is connected.
    if post.status == PostStatus.draft and linkedin.autopost_enabled() and linkedin.is_configured():
        session.refresh(post)
        publishing.publish(session, post)
    return RedirectResponse("/posts", status_code=303)


@app.post("/posts/{post_id}/edit")
async def post_edit(post_id: int, request: Request, session: Session = Depends(get_session)):
    post = _get_post(session, post_id)
    form = await request.form()
    body = _text(form, "body")
    if body and post.status in (PostStatus.draft, PostStatus.failed):
        post.body = body
        post.status = PostStatus.draft  # a re-edited failed post is a fresh draft
        post.error = None
        session.add(post)
        session.commit()
    return RedirectResponse("/posts", status_code=303)


@app.post("/posts/{post_id}/publish")
def post_publish(post_id: int, session: Session = Depends(get_session)):
    post = _get_post(session, post_id)
    if post.status in (PostStatus.draft, PostStatus.failed):
        publishing.publish(session, post)
    return RedirectResponse("/posts", status_code=303)


@app.post("/posts/{post_id}/discard")
def post_discard(post_id: int, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if post:
        session.delete(post)
        session.commit()
    return RedirectResponse("/posts", status_code=303)


# --- shared helpers -------------------------------------------------------

def _get_jd(session: Session, jd_id: int) -> JD:
    jd = session.get(JD, jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


def _get_candidate(session: Session, candidate_id: int) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


def _get_post(session: Session, post_id: int) -> Post:
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def _render_dashboard(request: Request, session: Session, draft=None, draft_for=None,
                      notice=None, notice_kind=None):
    candidates = session.exec(select(Candidate)).all()
    jds = session.exec(select(JD)).all()
    context = {
        "candidates": candidates,
        "jds": jds,
        "stats": {
            "pool": len(candidates),
            "opted_in": sum(c.status == CandidateStatus.opted_in for c in candidates),
            "jds": len(jds),
        },
        "statuses": [s.value for s in CandidateStatus],
        "role_types": [r.value for r in RoleType],
        "segments": [s.value for s in Segment],
        "draft": draft,
        "draft_for": draft_for,
        "notice": notice,
        "notice_kind": notice_kind,
        "google_form_ready": bool(os.getenv("GOOGLE_FORM_CSV_URL")),
        "optin_url": os.getenv("OPTIN_URL"),
    }
    return templates.TemplateResponse(request, "dashboard.html", context)


def _opted_in_candidates(session: Session) -> list[Candidate]:
    return session.exec(
        select(Candidate).where(Candidate.status == CandidateStatus.opted_in)
    ).all()


# --- form parsing helpers -------------------------------------------------

def _text(form, key: str) -> str | None:
    value = form.get(key)
    value = value.strip() if isinstance(value, str) else None
    return value or None


def _int(form, key: str) -> int | None:
    value = _text(form, key)
    return int(value) if value and value.lstrip("-").isdigit() else None


def _checked(form, key: str) -> bool:
    # An unchecked checkbox is simply absent from the form data.
    return key in form


def _skills(form, key: str) -> list[str]:
    raw = _text(form, key) or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _enum(enum_cls: type[E], value: str | None) -> E | None:
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


def _form_context(error: str | None = None) -> dict:
    return {
        "role_types": [r.value for r in RoleType],
        "segments": [s.value for s in Segment],
        "error": error,
    }
