"""SMTP delivery of an approved brief.

Free tier by design: any SMTP server works, including Gmail with an app
password. Delivery is *never* attempted for a brief the human did not approve —
that check lives in the graph, and this module simply refuses to run without
configuration rather than failing a run.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.settings import Settings, get_settings
from app.models.brief import Brief
from app.services.render import render_html, render_markdown

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of an attempted send."""

    sent: bool
    channel: str
    detail: str
    recipients: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "sent": self.sent,
            "channel": self.channel,
            "detail": self.detail,
            "recipients": self.recipients,
        }


def _build_message(brief: Brief, settings: Settings) -> EmailMessage:
    message = EmailMessage()
    subject = f"AlphaBrief — {brief.generated_for}"
    if brief.partial:
        subject += " (partial coverage)"
    message["Subject"] = subject
    message["From"] = settings.smtp_from or ""
    recipients = [r.strip() for r in (settings.smtp_to or "").split(",") if r.strip()]
    message["To"] = ", ".join(recipients)
    message.set_content(render_markdown(brief))
    message.add_alternative(render_html(brief), subtype="html")
    return message


def _send_blocking(brief: Brief, settings: Settings) -> DeliveryResult:
    recipients = [r.strip() for r in (settings.smtp_to or "").split(",") if r.strip()]
    message = _build_message(brief, settings)
    context = ssl.create_default_context()
    host = settings.smtp_host or ""
    with smtplib.SMTP(host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as server:
        server.ehlo()
        if server.has_extn("starttls"):
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
    return DeliveryResult(
        sent=True,
        channel="smtp",
        detail=f"delivered to {len(recipients)} recipient(s) via {host}",
        recipients=recipients,
    )


async def send_brief(brief: Brief, settings: Settings | None = None) -> DeliveryResult:
    """Send an approved brief by email, degrading to a no-op when unconfigured."""
    cfg = settings or get_settings()
    if not cfg.smtp_enabled:
        return DeliveryResult(
            sent=False,
            channel="none",
            detail="SMTP is not configured; the brief was archived but not emailed",
            recipients=[],
        )
    try:
        return await asyncio.to_thread(_send_blocking, brief, cfg)
    except Exception as exc:  # noqa: BLE001 - a mail outage must not fail an approved run
        logger.warning("SMTP delivery failed: %s", exc)
        return DeliveryResult(
            sent=False,
            channel="smtp",
            detail=f"delivery failed: {type(exc).__name__}: {exc}",
            recipients=[r.strip() for r in (cfg.smtp_to or "").split(",") if r.strip()],
        )
