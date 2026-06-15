"""LLM provider abstraction: JD parsing (and message drafting, later phases).

The provider is chosen by the LLM_PROVIDER env var:
  - "ollama" (default): local, free, self-hosted. Needs `ollama serve`.
  - "gemini": Google Gemini cloud API. Needs GEMINI_API_KEY.

Both are called over plain HTTP (stdlib urllib) — no SDK dependency. Both are
asked to return JSON so parsing is robust.
"""

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

from app.models import JD, Candidate

load_dotenv()


class LLMError(RuntimeError):
    """Raised when the LLM is unreachable or returns something unusable."""


# --- public API -----------------------------------------------------------

def parse_jd(text: str) -> dict:
    """Parse a raw job description into the structured fields of the JD model."""
    raw = complete(_JD_PROMPT.format(jd=text.strip()))
    return _coerce_jd(_loads(raw))


def parse_candidates(text: str) -> list[dict]:
    """Extract one or more candidate profiles from pasted text (sourcing helper)."""
    data = _loads(complete(_CANDIDATES_PROMPT.format(text=text.strip())))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("candidates"), list):
        items = data["candidates"]
    elif isinstance(data, dict):
        items = [data]
    else:
        items = []
    return [_coerce_candidate(item) for item in items if isinstance(item, dict)]


def draft_message(candidate: Candidate, jd: JD, channel: str) -> dict:
    """Draft a segment- and channel-aware outreach message.

    Returns {"subject": str | None, "body": str}. WhatsApp has no subject.
    """
    rules = _CHANNEL_RULES.get(channel)
    if rules is None:
        raise LLMError(f"Unknown channel: {channel!r}")
    # candidate.segment may be a Segment enum or a plain string depending on source.
    segment = getattr(candidate.segment, "value", candidate.segment)
    hook = _SEGMENT_HOOKS.get(segment, "Keep it friendly, honest, and professional.")

    prompt = _DRAFT_PROMPT.format(
        name=candidate.name,
        role=jd.role,
        stack=", ".join(jd.stack) if jd.stack else "the listed stack",
        rate=jd.rate if jd.rate is not None else "competitive",
        duration=jd.contract_duration or "a few months",
        hook=hook,
        rules=rules,
    )
    data = _loads(complete(prompt))
    return {"subject": _str(data.get("subject")), "body": _str(data.get("body")) or ""}


def draft_outreach(candidate: Candidate) -> str:
    """Draft a manual (LinkedIn/email) invite for a sourced candidate to opt in.

    Pull side: Setu only drafts — the user sends this themselves, human-paced.
    """
    segment = getattr(candidate.segment, "value", candidate.segment)
    hook = _SEGMENT_HOOKS.get(segment, "Keep it friendly, honest, and professional.")
    role = candidate.seniority or getattr(candidate.role_type, "value", None) or "engineer"
    skills = ", ".join(candidate.skills) if candidate.skills else "their stack"

    prompt = _INVITE_PROMPT.format(name=candidate.name, role=role, skills=skills, hook=hook)
    data = _loads(complete(prompt))
    return _str(data.get("body")) or ""


def draft_post(jd_or_prompt: "JD | str") -> str:
    """Draft a LinkedIn post in the recruiter's brand voice, ending with the opt-in link."""
    if isinstance(jd_or_prompt, JD):
        jd = jd_or_prompt
        parts = [jd.role]
        if jd.client:
            parts.append(f"at {jd.client}")
        if jd.stack:
            parts.append(f"({', '.join(jd.stack)})")
        topic = "a remote contract role: " + " ".join(parts)
    else:
        topic = str(jd_or_prompt).strip() or "the senior remote contract roles we staff"

    body = _str(_loads(complete(_POST_PROMPT.format(topic=topic))).get("body")) or ""
    return _append_optin(body)


def _append_optin(body: str) -> str:
    url = os.getenv("OPTIN_URL", "http://localhost:8000/optin")
    if url and url not in body:
        body = f"{body}\n\nInterested? Opt in here: {url}"
    return body


def complete(prompt: str) -> str:
    """Send a prompt to the configured provider and return the raw text reply."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "ollama":
        return _ollama_complete(prompt)
    if provider == "gemini":
        return _gemini_complete(prompt)
    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r} (expected 'ollama' or 'gemini')")


# --- providers ------------------------------------------------------------

def _ollama_complete(prompt: str) -> str:
    base = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("LLM_MODEL", "qwen2.5")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    data = _post_json(f"{base}/api/generate", payload)
    return data.get("response", "")


def _gemini_complete(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY is not set")
    model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0},
    }
    data = _post_json(url, payload)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Unexpected Gemini response shape: {data}") from exc


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"LLM request to {url.split('?')[0]} failed: {exc}") from exc


# --- prompt + output handling ---------------------------------------------

_JD_PROMPT = """You are parsing a software engineering job description into structured data.
Return ONLY a JSON object with exactly these keys:
- role: job title (string)
- stack: array of required technologies/skills (strings)
- min_years_experience: minimum years required (integer) or null
- max_years_experience: maximum years (integer) or null
- seniority: e.g. "Senior", "Staff" (string) or null
- location: (string) or null
- remote: whether the role is remote (boolean)
- contract_duration: e.g. "3-6 months" (string) or null
- rate: pay rate as a plain integer (no currency symbols) or null
- client: hiring company (string) or null
- source: where the JD came from (string) or null

Job description:
\"\"\"
{jd}
\"\"\"
"""


_SEGMENT_HOOKS = {
    "active": "They are open to work (possibly recently laid off). Be direct and "
    "encouraging; lead with the opportunity.",
    "passive": "They are employed but likely underpaid. Be discreet and private; "
    "emphasise clearly higher pay and remote flexibility. Do not imply they are job-hunting.",
    "freelance": "They are an active freelancer. Keep it minimal and zero-friction; "
    "just present the role and the rate.",
}

_CHANNEL_RULES = {
    "email": "Channel: email. Include a short subject line. Body 4-6 sentences, warm "
    "and professional.",
    "whatsapp": "Channel: WhatsApp. Use an empty subject. Body 2-3 short sentences, "
    "casual and concise.",
}

_DRAFT_PROMPT = """You are a recruiter writing a short outreach message to {name}, an
engineer who OPTED IN to hear about contract roles.

Role: {role}
Stack: {stack}
Rate: {rate}
Duration: {duration}

Candidate context: {hook}
{rules}

Be honest, specific, and respectful. No spam, no false urgency. Do not invent facts
beyond what is given above.
Return ONLY JSON: {{"subject": "<string, empty for whatsapp>", "body": "<message>"}}
"""


_INVITE_PROMPT = """You are a recruiter writing a SHORT, friendly message to {name}, a
{role} skilled in {skills}. You place senior engineers in remote contract roles
(3-6 months). Invite them to opt in to hear about matching roles — they can share their
details and consent via a short form.

Candidate context: {hook}
Keep it to 2-3 sentences, no hard sell, respectful.
Return ONLY JSON: {{"body": "<message>"}}
"""


_POST_PROMPT = """Write a LinkedIn post in a warm, professional recruiter brand voice
(80-120 words) about {topic}. You place senior engineers into remote contract roles
(3-6 months). Invite interested engineers to get in touch. Use at most 2-3 relevant
hashtags. Do NOT include any URL or link.
Return ONLY JSON: {{"body": "<post>"}}
"""


def _loads(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"LLM did not return valid JSON: {raw[:200]!r}")


_CANDIDATES_PROMPT = """Extract software-engineering candidate profiles from the text below.
It may contain one person or several (often separated by blank lines). For EACH person
return an object with these keys:
- name: full name (string)
- email: (string) or null
- whatsapp: phone number (string) or null
- skills: array of technologies/skills (strings)
- role_type: one of "frontend", "backend", "fullstack", or null
- seniority: e.g. "Senior", "Lead" (string) or null
- years_experience: integer or null
- location: (string) or null

Return ONLY JSON: {{"candidates": [ ... ]}}. If no real person is present, return
{{"candidates": []}}. Do not invent people or details not in the text.

Text:
\"\"\"
{text}
\"\"\"
"""


def _coerce_candidate(data: dict) -> dict:
    role_type = _str(data.get("role_type"))
    return {
        "name": _str(data.get("name")),
        "email": _str(data.get("email")),
        "whatsapp": _str(data.get("whatsapp")),
        "skills": _str_list(data.get("skills")),
        "role_type": role_type.lower() if role_type else None,
        "seniority": _str(data.get("seniority")),
        "years_experience": _int(data.get("years_experience")),
        "location": _str(data.get("location")),
    }


def _coerce_jd(data: dict) -> dict:
    """Map a loosely-typed LLM dict onto clean JD field types."""
    return {
        "role": _str(data.get("role")) or "Untitled role",
        "stack": _str_list(data.get("stack")),
        "min_years_experience": _int(data.get("min_years_experience")),
        "max_years_experience": _int(data.get("max_years_experience")),
        "seniority": _str(data.get("seniority")),
        "location": _str(data.get("location")),
        "remote": _bool(data.get("remote")),
        "contract_duration": _str(data.get("contract_duration")),
        "rate": _int(data.get("rate")),
        "client": _str(data.get("client")),
        "source": _str(data.get("source")),
    }


def _str(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "remote", "1"}
    return True  # JD.remote defaults to True when unstated
