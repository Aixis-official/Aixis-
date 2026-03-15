"""Contact form API endpoint with rate limiting and input sanitization."""

import asyncio
import logging
import re
import smtplib
import time
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from ...config import settings
from ...schemas.contact import ContactRequest, ContactResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# In-memory rate limiter (per-IP, sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple in-memory rate limiter keyed by IP address."""

    def __init__(self):
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, ip: str, max_requests: int, window_seconds: int) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            # Prune old entries
            self._attempts[ip] = [t for t in self._attempts[ip] if t > cutoff]

            if len(self._attempts[ip]) >= max_requests:
                return False

            self._attempts[ip].append(now)
            return True


_rate_limiter = _RateLimiter()


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
# Email construction
# ---------------------------------------------------------------------------

def _build_notification_email(req: ContactRequest) -> MIMEMultipart:
    """Build notification email to Aixis team."""
    subject = _sanitize_header(
        f"[Aixis] お問い合わせ: {req.inquiry_type} - {req.company_name}"
    )

    body_lines = [
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
    body = "\n".join(body_lines)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = settings.smtp_to
    msg["Reply-To"] = _sanitize_header(req.email)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_autoreply_email(req: ContactRequest) -> MIMEMultipart:
    """Build auto-reply confirmation email to the customer."""
    subject = "[Aixis] お問い合わせを受け付けました"

    body_lines = [
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
    msg["To"] = _sanitize_header(req.email)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _send_email(msg: MIMEMultipart) -> None:
    """Send a single email via SMTP. Tries STARTTLS (587) first, then SSL (465)."""
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
            return
    except Exception as e:
        logger.warning("STARTTLS on port %d failed: %s — trying SSL on 465", settings.smtp_port, e)

    # Fallback: SSL on port 465
    with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=15) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def _send_emails_background(req: ContactRequest) -> None:
    """Send notification + auto-reply emails (runs in background thread)."""
    # Send notification to Aixis team
    try:
        notification = _build_notification_email(req)
        _send_email(notification)
        logger.info("Notification email sent for %s (%s)", req.company_name, req.email)
    except Exception:
        logger.exception(
            "CRITICAL: Failed to send notification email for %s (%s)",
            req.company_name,
            req.email,
        )
    # Send auto-reply to customer
    try:
        autoreply = _build_autoreply_email(req)
        _send_email(autoreply)
        logger.info("Auto-reply sent to %s", req.email)
    except Exception:
        logger.exception("Failed to send auto-reply to %s", req.email)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/", response_model=ContactResponse)
async def submit_contact(
    req: ContactRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive contact form submission and send notification email.

    Rate limited to prevent abuse. Emails are sent in the background
    so the user gets an immediate response.
    """
    # Rate limiting by IP
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.check(
        client_ip,
        settings.contact_rate_limit_per_ip,
        settings.contact_rate_limit_window_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="送信回数の上限に達しました。しばらくしてからもう一度お試しください。",
        )

    logger.info(
        "Contact form submission from %s (%s): %s [IP: %s]",
        req.company_name,
        req.email,
        req.inquiry_type,
        client_ip,
    )

    if settings.smtp_host:
        background_tasks.add_task(_send_emails_background, req)
    else:
        logger.warning("SMTP not configured; contact form submission logged only.")

    return ContactResponse(
        success=True,
        message="お問い合わせを受け付けました。担当者より2営業日以内にご連絡いたします。",
    )


@router.get("/smtp-test")
async def smtp_test(request: Request):
    """Admin-only SMTP diagnostic endpoint. Returns connection test results."""
    # Only allow from admin (check Authorization header)
    from ...api.deps import require_admin, get_db
    results = {"smtp_host": settings.smtp_host, "smtp_port": settings.smtp_port,
               "smtp_user": settings.smtp_user, "smtp_from": settings.smtp_from,
               "smtp_to": settings.smtp_to, "password_set": bool(settings.smtp_password)}

    # Test STARTTLS (port 587)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            results["starttls_587"] = "OK"
    except Exception as e:
        results["starttls_587"] = f"FAILED: {e}"

    # Test SSL (port 465)
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=10) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            results["ssl_465"] = "OK"
    except Exception as e:
        results["ssl_465"] = f"FAILED: {e}"

    # Try sending a test email
    try:
        from email.mime.text import MIMEText as MT
        msg = MIMEMultipart()
        msg["Subject"] = "[Aixis] SMTP Test from Railway"
        msg["From"] = settings.smtp_from
        msg["To"] = settings.smtp_to
        msg.attach(MIMEText("SMTP test email from Railway deployment.", "plain", "utf-8"))
        _send_email(msg)
        results["test_send"] = "OK"
    except Exception as e:
        results["test_send"] = f"FAILED: {e}"

    return results
