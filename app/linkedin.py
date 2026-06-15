"""LinkedIn OAuth token management (Phase 8).

Posting uses the recruiter's own access token. That token lasts ~60 days, so we
cache it (with its expiry) in a small JSON file and use the long-lived refresh
token to renew it automatically — no re-auth every two months.

The initial 3-legged OAuth is a manual one-time step (see CLAUDE.md Phase 8):
the resulting access + refresh tokens are read from .env and seeded into the
store on first use. Publishing itself lands in step 3.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TOKEN_STORE = Path(os.getenv("LINKEDIN_TOKEN_STORE", "linkedin_token.json"))
POSTS_PER_WEEK_LIMIT = 3  # cadence guardrail — LinkedIn favours ~2–3 posts/week
_OAUTH_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_POSTS_URL = "https://api.linkedin.com/rest/posts"
_API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202405")
_REFRESH_BUFFER = 300  # refresh when within 5 minutes of expiry


class LinkedInError(RuntimeError):
    """Raised when LinkedIn is not configured or a token operation fails."""


class LinkedInAuthError(LinkedInError):
    """Raised when LinkedIn rejects the token (401) — triggers one refresh + retry."""


def get_access_token() -> str:
    """Return a valid access token, refreshing it first if it has (nearly) expired."""
    state = _load_state()
    token = state.get("access_token")
    expires_at = state.get("expires_at")
    if token and (expires_at is None or expires_at > time.time() + _REFRESH_BUFFER):
        return token
    return refresh_access_token(state)


def refresh_access_token(state: dict | None = None) -> str:
    """Exchange the refresh token for a fresh access token and persist it."""
    state = _load_state() if state is None else state
    refresh_token = state.get("refresh_token")
    if not refresh_token:
        raise LinkedInError("No LinkedIn refresh token available; run OAuth once.")

    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise LinkedInError("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET not set")

    payload = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    resp = _post_form(_OAUTH_URL, payload)
    if "access_token" not in resp:
        raise LinkedInError(f"Refresh response missing access_token: {resp}")

    new_state = {
        "access_token": resp["access_token"],
        "expires_at": time.time() + int(resp.get("expires_in", 0)),
        # LinkedIn may rotate the refresh token; keep the previous one if it doesn't.
        "refresh_token": resp.get("refresh_token", refresh_token),
    }
    _save_state(new_state)
    return new_state["access_token"]


def is_configured() -> bool:
    """True when we have both an author URN and a token to publish with."""
    return bool(os.getenv("LINKEDIN_PERSON_URN") and _load_state().get("access_token"))


def autopost_enabled() -> bool:
    return os.getenv("LINKEDIN_AUTOPOST", "false").strip().lower() == "true"


def publish_post(text: str) -> str:
    """Publish text to the authenticated member's feed; return the post URN."""
    author = os.getenv("LINKEDIN_PERSON_URN")
    if not author:
        raise LinkedInError("LINKEDIN_PERSON_URN not set")

    body = {
        "author": author,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    try:
        return _publish_request(get_access_token(), body)
    except LinkedInAuthError:
        # Token was rejected — refresh once and retry before giving up.
        return _publish_request(refresh_access_token(), body)


def _publish_request(token: str, body: dict) -> str:
    req = urllib.request.Request(
        _POSTS_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": _API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            urn = resp.headers.get("x-restli-id")
            if not urn:
                raise LinkedInError(f"Published ({resp.status}) but no URN in response headers")
            return urn
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if exc.code == 401:
            raise LinkedInAuthError(f"LinkedIn rejected the token (401): {detail}") from exc
        raise LinkedInError(f"LinkedIn publish failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LinkedInError(f"LinkedIn publish failed: {exc}") from exc


def _load_state() -> dict:
    if TOKEN_STORE.exists():
        try:
            return json.loads(TOKEN_STORE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    # First use: seed from env. Expiry is unknown, so treat as valid until a refresh
    # establishes a real expiry (or a publish 401 forces one).
    return {
        "access_token": os.getenv("LINKEDIN_ACCESS_TOKEN") or None,
        "refresh_token": os.getenv("LINKEDIN_REFRESH_TOKEN") or None,
        "expires_at": None,
    }


def _save_state(state: dict) -> None:
    TOKEN_STORE.write_text(json.dumps(state))


def _post_form(url: str, data: bytes) -> dict:
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise LinkedInError(f"LinkedIn token request failed ({exc.code}): {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LinkedInError(f"LinkedIn token request failed: {exc}") from exc
