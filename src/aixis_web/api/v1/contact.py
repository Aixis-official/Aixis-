"""Contact form API endpoint."""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter

from ...config import settings
from ...schemas.contact import ContactRequest, ContactResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_notification_email(req: ContactRequest) -> MIMEMultipart:
    """Build notification email to Aixis team."""
    subject = f"[Aixis] お問い合わせ: {req.inquiry_type} - {req.company_name}"

    body_lines = [
        "以下のお問い合わせを受け付けました。",
        "",
        "─" * 30,
        f"会社名: {req.company_name}",
        f"部署: {req.department or '未記入'}",
        f"お名前: {req.name}",
        f"メールアドレス: {req.email}",
        f"電話番号: {req.phone or '未記入'}",
        f"お問い合わせ種別: {req.inquiry_type}",
        "─" * 30,
        "",
        "【お問い合わせ内容】",
        req.message,
    ]
    body = "\n".join(body_lines)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = settings.smtp_to
    msg["Reply-To"] = req.email
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_autoreply_email(req: ContactRequest) -> MIMEMultipart:
    """Build auto-reply confirmation email to the customer."""
    subject = "[Aixis] お問い合わせを受け付けました"

    body_lines = [
        f"{req.name} 様",
        "",
        "この度は Aixis にお問い合わせいただき、誠にありがとうございます。",
        "以下の内容でお問い合わせを受け付けました。",
        "",
        "─" * 30,
        f"お問い合わせ種別: {req.inquiry_type}",
        f"お問い合わせ内容:",
        req.message,
        "─" * 30,
        "",
        "担当者より2営業日以内にご連絡いたします。",
        "今しばらくお待ちくださいますようお願い申し上げます。",
        "",
        "─" * 30,
        "Aixis Inc.",
        "独立系AI調査・監査機関",
        "https://aixis.jp",
        "─" * 30,
        "",
        "※ このメールは自動送信されています。",
        "  本メールへの返信はお控えください。",
    ]
    body = "\n".join(body_lines)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = req.email
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _send_email(msg: MIMEMultipart) -> None:
    """Send a single email via SMTP with STARTTLS."""
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


@router.post("/", response_model=ContactResponse)
async def submit_contact(req: ContactRequest):
    """Receive contact form submission and send notification email.

    When SMTP is configured, sends an email to info@aixis.jp.
    When SMTP is not configured, logs the submission and returns success.
    """
    logger.info(
        "Contact form submission from %s (%s): %s",
        req.company_name,
        req.email,
        req.inquiry_type,
    )

    if settings.smtp_host:
        loop = asyncio.get_running_loop()
        notification_sent = False
        # Send notification to Aixis team
        try:
            notification = _build_notification_email(req)
            await loop.run_in_executor(None, _send_email, notification)
            logger.info("Notification email sent for %s (%s)", req.company_name, req.email)
            notification_sent = True
        except Exception:
            logger.exception(
                "Failed to send notification email for %s (%s)",
                req.company_name,
                req.email,
            )
        # Send auto-reply to customer
        try:
            autoreply = _build_autoreply_email(req)
            await loop.run_in_executor(None, _send_email, autoreply)
            logger.info("Auto-reply sent to %s", req.email)
        except Exception:
            logger.exception("Failed to send auto-reply to %s", req.email)

        if not notification_sent:
            logger.error(
                "CRITICAL: Contact form from %s (%s) was NOT delivered to team. "
                "Message: %s",
                req.company_name,
                req.email,
                req.message[:200],
            )
    else:
        logger.warning("SMTP not configured; contact form submission logged only.")

    return ContactResponse(
        success=True,
        message="お問い合わせを受け付けました。担当者より2営業日以内にご連絡いたします。",
    )
