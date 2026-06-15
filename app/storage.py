"""Resume file storage on local disk."""

import os
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

RESUMES_DIR = Path(os.getenv("RESUMES_DIR", "resumes"))


async def save_resume(upload: UploadFile | None) -> str | None:
    """Persist an uploaded resume and return its path, or None if no file."""
    if not upload or not upload.filename:
        return None
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the original name but prefix a unique token to avoid collisions.
    safe_name = Path(upload.filename).name
    dest = RESUMES_DIR / f"{uuid4().hex}_{safe_name}"
    dest.write_bytes(await upload.read())
    return str(dest)
