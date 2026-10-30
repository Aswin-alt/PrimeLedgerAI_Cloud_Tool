"""Optional email notification hooks (Phase 2).

Configure SMTP_* env vars to enable. Safe no-op when unset so existing
workflows are never blocked.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def send_email(to_addr: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host or not to_addr:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("SMTP_FROM", user or "noreply@primeledgerai.com")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except OSError:
        return False


def notify_status_change(to_addr: Optional[str], file_name: str, status: str) -> None:
    if not to_addr:
        return
    send_email(
        to_addr,
        f"PrimeLedgerAI: {file_name} → {status}",
        f"The file '{file_name}' status changed to {status}.\n\n— PrimeLedgerAI Cloud",
    )
