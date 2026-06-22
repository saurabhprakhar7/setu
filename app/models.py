"""Database models for Setu.

Candidate and JD are the two core entities. List fields (skills, stack) are
stored as JSON columns since SQLite has no native array type.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class RoleType(str, Enum):
    frontend = "frontend"
    backend = "backend"
    fullstack = "fullstack"


class Segment(str, Enum):
    active = "active"
    passive = "passive"
    freelance = "freelance"


class CandidateStatus(str, Enum):
    sourced = "sourced"
    contacted = "contacted"
    opted_in = "opted_in"
    resume = "resume"
    sent_to_client = "sent_to_client"


class Candidate(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str | None = Field(default=None, index=True)  # unknown until they opt in
    whatsapp: str | None = None
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    role_type: RoleType | None = None
    seniority: str | None = None
    years_experience: int | None = None
    segment: Segment | None = None
    current_pay: int | None = None
    expected_pay: int | None = None
    location: str | None = None
    remote_ok: bool = True
    availability: str | None = None
    resume_path: str | None = None
    consent: bool = False
    consent_date: datetime | None = None
    status: CandidateStatus = CandidateStatus.sourced


class JD(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    role: str
    stack: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    min_years_experience: int | None = None
    max_years_experience: int | None = None
    seniority: str | None = None
    location: str | None = None
    remote: bool = True
    contract_duration: str | None = None
    rate: int | None = None
    client: str | None = None
    source: str | None = None


class MessageChannel(str, Enum):
    email = "email"
    whatsapp = "whatsapp"


class MessageStatus(str, Enum):
    drafted = "drafted"  # WhatsApp drafts stay here — the user sends them manually
    sent = "sent"
    failed = "failed"


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    candidate_id: int = Field(foreign_key="candidate.id")
    jd_id: int = Field(foreign_key="jd.id")
    channel: MessageChannel
    subject: str | None = None  # email only; null for WhatsApp
    body: str
    status: MessageStatus = MessageStatus.drafted
    sent_at: datetime | None = None


class SavedSearch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    label: str
    role: str = ""
    skills: str = ""
    location: str = ""
    seniority: str = ""
    segment: str = ""
    company: str = ""
    min_years: str = ""


class PostStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    published = "published"
    failed = "failed"


class Post(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    jd_id: int | None = Field(default=None, foreign_key="jd.id")  # posts can be non-JD too
    body: str
    status: PostStatus = PostStatus.draft
    linkedin_urn: str | None = None  # set after publish
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    published_at: datetime | None = None
