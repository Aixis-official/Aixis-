"""Notification dispatch service.

Handles creating in-app notifications, sending emails via SMTP,
and posting to Slack/Discord webhooks based on user preferences.
"""

import asyncio
import html as html_mod
import json
import logging
import smtplib
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.notification import Notification, NotificationPreference

logger = logging.getLogger(__name__)


def _safe_create_task(coro):
    """Create an asyncio task with automatic exception logging."""
    task = asyncio.create_task(coro)

    def _on_done(t):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("Background task failed: %s: %s", type(exc).__name__, exc)

    task.add_done_callback(_on_done)
    return task


async def create_notification(
    db: AsyncSession,
    user_id: str,
    type: str,
    title: str,
    title_jp: str,
    body: str = "",
    body_jp: str = "",
    link: str | None = None,
) -> Notification:
    """Insert a new in-app notification record."""
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        title_jp=title_jp,
        body=body,
        body_jp=body_jp,
        link=link,
    )
    db.add(notification)
    await db.flush()
    return notification


async def get_or_create_preferences(
    db: AsyncSession,
    user_id: str,
) -> NotificationPreference:
    """Get user's notification preferences, creating defaults if needed."""
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        pref = NotificationPreference(user_id=user_id)
        db.add(pref)
        await db.flush()
    return pref


async def dispatch_notification(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    title: str,
    title_jp: str,
    body: str = "",
    body_jp: str = "",
    link: str | None = None,
    user_email: str | None = None,
) -> None:
    """Dispatch notification across all enabled channels for a user.

    Checks user preferences and sends via:
    - In-app notification
    - Email (SMTP)
    - Slack webhook
    - Discord webhook
    """
    pref = await get_or_create_preferences(db, user_id)

    subscribed = pref.subscribed_events or []
    if event_type not in subscribed and "*" not in subscribed:
        logger.debug("User %s not subscribed to event %s, skipping", user_id, event_type)
        return

    # In-app notification
    if pref.in_app_enabled:
        await create_notification(
            db=db,
            user_id=user_id,
            type=event_type,
            title=title,
            title_jp=title_jp,
            body=body,
            body_jp=body_jp,
            link=link,
        )

    # Email notification
    if pref.email_enabled and user_email and settings.smtp_host:
        subject = f"[Aixis] {title_jp}"
        body_html = _build_email_html(title_jp, body_jp, link)
        _safe_create_task(
            asyncio.to_thread(
                send_email_notification, user_email, subject, body_html
            )
        )

    # Slack notification
    if pref.slack_webhook_url:
        message = f"*{title_jp}*\n{body_jp}"
        if link:
            message += f"\n<{link}|詳細を見る>"
        _safe_create_task(send_slack_notification(pref.slack_webhook_url, message))

    # Discord notification
    if pref.discord_webhook_url:
        message = f"**{title_jp}**\n{body_jp}"
        if link:
            message += f"\n[詳細を見る]({link})"
        _safe_create_task(send_discord_notification(pref.discord_webhook_url, message))

    await db.commit()


def _build_email_html(title: str, body: str, link: str | None = None) -> str:
    """Build a simple HTML email body."""
    safe_title = html_mod.escape(title)
    safe_body = html_mod.escape(body)
    link_html = ""
    if link:
        safe_link = html_mod.escape(link, quote=True)
        link_html = f'<p><a href="{safe_link}" style="color:#2563eb;">詳細を確認する</a></p>'

    return f"""
    <div style="font-family:'Noto Serif JP',serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#1a365d;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h1 style="margin:0;font-size:18px;">Aixis</h1>
        </div>
        <div style="background:white;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
            <h2 style="margin:0 0 12px;font-size:16px;color:#111827;">{safe_title}</h2>
            <p style="margin:0 0 16px;color:#4b5563;font-size:14px;line-height:1.6;">{safe_body}</p>
            {link_html}
        </div>
        <p style="margin-top:16px;font-size:12px;color:#9ca3af;text-align:center;">
            この通知はAixis AI監査プラットフォームから送信されました。
        </p>
    </div>
    """


def send_email_notification(to_email: str, subject: str, body_html: str) -> None:
    """Send email via SMTP (synchronous, intended to run in thread)."""
    if not settings.smtp_host:
        logger.warning("SMTP not configured, skipping email to %s", to_email)
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to_email
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        if settings.smtp_port == 465:
            # SSL
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        else:
            # STARTTLS
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)

        logger.info("Email sent to %s: %s", to_email, subject)
    except Exception:
        logger.exception("Failed to send email to %s", to_email)


async def send_slack_notification(webhook_url: str, message: str) -> None:
    """POST a message to a Slack incoming webhook."""
    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        await asyncio.to_thread(_post_webhook, webhook_url, payload)
        logger.info("Slack notification sent")
    except Exception:
        logger.exception("Failed to send Slack notification")


async def send_discord_notification(webhook_url: str, message: str) -> None:
    """POST a message to a Discord webhook."""
    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        await asyncio.to_thread(_post_webhook, webhook_url, payload)
        logger.info("Discord notification sent")
    except Exception:
        logger.exception("Failed to send Discord notification")


def _post_webhook(url: str, payload_bytes: bytes) -> None:
    """Synchronous webhook POST (runs in thread)."""
    from .webhook_service import validate_webhook_url

    validate_webhook_url(url)

    req = urllib.request.Request(
        url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Aixis-Notification/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
