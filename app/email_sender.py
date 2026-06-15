"""SMTP email sending. Configured entirely via env; no third-party dependency."""

import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()


class EmailError(RuntimeError):
    """Raised when email is misconfigured or sending fails."""


def send_email(to: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    if not host:
        raise EmailError("SMTP is not configured (set SMTP_HOST)")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user
    if not sender:
        raise EmailError("Set SMTP_FROM or SMTP_USER for the From address")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                _login_and_send(smtp, user, password, msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                _login_and_send(smtp, user, password, msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Failed to send email to {to}: {exc}") from exc


def _login_and_send(smtp: smtplib.SMTP, user, password, msg: EmailMessage) -> None:
    if user and password:
        smtp.login(user, password)
    smtp.send_message(msg)
