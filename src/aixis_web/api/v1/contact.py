"""Contact form API endpoint with rate limiting and input sanitization."""

import asyncio
import logging
import re
import smtplib
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from typing import Annotated
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.base import get_db
from ...db.models.user import User
from ...schemas.contact import ContactRequest, ContactResponse
from ...services.rate_limit_service import check_rate_limit
from ..deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Email sanitization helpers
# ---------------------------------------------------------------------------

_HEADER_INJECT_RE = re.compile(r'[\r\n]')


def _sanitize_header(value: str) -> str:
    """Strip CR/LF characters to prevent email header injection."""
    return _HEADER_INJECT_RE.sub(' ', value).strip()


def _sanitize_body(value: str) -> str:
    """Basic body sanitization — strip control characters except newlines/tabs."""
    return ''.join(c for c in value if c in ('\n', '\t') or (ord(c) >= 32))


# ---------------------------------------------------------------------------
# Email content builders (plain text)
# ---------------------------------------------------------------------------

def _notification_subject(req: ContactRequest) -> str:
    return _sanitize_header(
        f"[Aixis-platform] お問い合わせ: {req.inquiry_type} - {req.company_name}"
    )


def _notification_body(req: ContactRequest) -> str:
    lines = [
        "以下のお問い合わせを受け付けました。",
        "",
        "─" * 30,
        f"会社名: {_sanitize_body(req.company_name)}",
        f"部署: {_sanitize_body(req.department or '未記入')}",
        f"お名前: {_sanitize_body(req.name)}",
        f"メールアドレス: {req.email}",
        f"電話番号: {_sanitize_body(req.phone or '未記入')}",
        f"お問い合わせ種別: {_sanitize_body(req.inquiry_type)}",
        "─" * 30,
        "",
        "【お問い合わせ内容】",
        _sanitize_body(req.message),
    ]
    return "\n".join(lines)


def _autoreply_body(req: ContactRequest) -> str:
    lines = [
        f"{_sanitize_body(req.name)} 様",
        "",
        "この度は Aixis にお問い合わせいただき、誠にありがとうございます。",
        "以下の内容でお問い合わせを受け付けました。",
        "",
        "─" * 30,
        f"お問い合わせ種別: {_sanitize_body(req.inquiry_type)}",
        f"お問い合わせ内容:",
        _sanitize_body(req.message),
        "─" * 30,
        "",
        "担当者より2営業日以内にご連絡いたします。",
        "今しばらくお待ちくださいますようお願い申し上げます。",
        "",
        "─" * 30,
        "Aixis",
        "独立系AI調査・監査機関",
        "https://aixis.jp",
        "https://platform.aixis.jp",
        "─" * 30,
        "",
        "※ このメールは自動送信されています。",
        "  本メールへの返信はお控えください。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Resend API (HTTPS — works on PaaS where SMTP ports are blocked)
# ---------------------------------------------------------------------------

def _send_via_resend(
    to: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
) -> None:
    """Send email using Resend HTTP API (https://resend.com/docs/api-reference)."""
    payload: dict = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")
    logger.info("Email sent via Resend to %s", to)


# ---------------------------------------------------------------------------
# SMTP fallback (for local development)
# ---------------------------------------------------------------------------

def _build_mime(to: str, subject: str, body: str, reply_to: str | None = None) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = _sanitize_header(reply_to)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _send_via_smtp(to: str, subject: str, body: str, reply_to: str | None = None) -> None:
    """Send email via SMTP. Tries STARTTLS first, then SSL."""
    msg = _build_mime(to, subject, body, reply_to)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
            return
    except Exception as e:
        logger.warning("STARTTLS failed: %s — trying SSL on 465", e)

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=10) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except Exception as e:
        logger.error("SSL fallback also failed: %s", e)
        raise RuntimeError(f"Both STARTTLS and SSL failed for {settings.smtp_host}") from e


# ---------------------------------------------------------------------------
# Unified send function
# ---------------------------------------------------------------------------

def _send_email(to: str, subject: str, body: str, reply_to: str | None = None) -> None:
    """Send email using best available method: Resend API > SMTP."""
    if settings.resend_api_key:
        _send_via_resend(to, subject, body, reply_to)
    elif settings.smtp_host:
        _send_via_smtp(to, subject, body, reply_to)
    else:
        logger.warning("No email backend configured (neither Resend nor SMTP)")


def _send_emails_background(req: ContactRequest) -> None:
    """Send notification + auto-reply emails (runs in background thread)."""
    # Notification to Aixis team
    try:
        _send_email(
            to=settings.smtp_to,
            subject=_notification_subject(req),
            body=_notification_body(req),
            reply_to=req.email,
        )
        logger.info("Notification email sent for %s (%s)", req.company_name, req.email)
    except Exception:
        logger.exception(
            "CRITICAL: Failed to send notification for %s (%s)",
            req.company_name, req.email,
        )
    # Auto-reply to customer
    try:
        _send_email(
            to=req.email,
            subject="[Aixis-platform] お問い合わせを受け付けました",
            body=_autoreply_body(req),
        )
        logger.info("Auto-reply sent to %s", req.email)
    except Exception:
        logger.exception("Failed to send auto-reply to %s", req.email)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ContactResponse)
async def submit_contact(
    req: ContactRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Receive contact form submission and send notification email."""
    client_ip = request.client.host if request.client else "unknown"
    allowed, _retry = await check_rate_limit(
        db,
        f"contact:{client_ip}",
        settings.contact_rate_limit_per_ip,
        settings.contact_rate_limit_window_seconds,
    )
    if not allowed:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="送信回数の上限に達しました。しばらくしてからもう一度お試しください。",
        )

    logger.info(
        "Contact form from %s (%s): %s [IP: %s]",
        req.company_name, req.email, req.inquiry_type, client_ip,
    )

    await db.commit()

    if settings.resend_api_key or settings.smtp_host:
        background_tasks.add_task(_send_emails_background, req)
    else:
        logger.warning("No email backend configured; submission logged only.")

    return ContactResponse(
        success=True,
        message="お問い合わせを受け付けました。担当者より2営業日以内にご連絡いたします。",
    )


@router.get("/smtp-test")
async def smtp_test(
    _admin: Annotated[User, Depends(require_admin)],
):
    """Email diagnostic endpoint (admin only). Tests all available backends.

    Only reports connectivity status — no sensitive config values are exposed.
    """
    loop = asyncio.get_running_loop()
    results: dict = {
        "resend_configured": bool(settings.resend_api_key),
        "smtp_configured": bool(settings.smtp_host),
        "smtp_credentials_set": bool(settings.smtp_user and settings.smtp_password),
    }

    # Test Resend API connectivity (send a test email)
    if settings.resend_api_key:
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.resend_from,
                    "to": [settings.smtp_to],
                    "subject": "[Aixis] Email Test from Railway",
                    "text": "This is a test email sent via Resend API from Railway.",
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                results["resend_test"] = "OK"
            else:
                results["resend_test"] = f"FAILED (status {resp.status_code})"
        except Exception:
            results["resend_test"] = "FAILED (connection error)"
    else:
        results["resend_test"] = "SKIPPED (not configured)"

    # Test TCP connectivity to SMTP ports (no credentials exposed)
    if settings.smtp_host:
        for port in [587, 465]:
            try:
                def _tcp(p=port):
                    sock = socket.create_connection((settings.smtp_host, p), timeout=5)
                    sock.close()
                    return "OK"
                r = await asyncio.wait_for(loop.run_in_executor(None, _tcp), timeout=6)
                results[f"tcp_{port}"] = r
            except asyncio.TimeoutError:
                results[f"tcp_{port}"] = "BLOCKED (timeout)"
            except Exception:
                results[f"tcp_{port}"] = "FAILED"

    return results
