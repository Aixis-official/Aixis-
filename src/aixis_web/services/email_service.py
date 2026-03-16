"""Unified email service for Aixis platform.

Supports Resend API (production) and SMTP (development).
Provides templated emails for invitations, trial reminders, etc.
"""

import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_HEADER_INJECT_RE = re.compile(r"[\r\n]")


def _sanitize_header(value: str) -> str:
    return _HEADER_INJECT_RE.sub(" ", value).strip()


# ---------------------------------------------------------------------------
# Core send functions
# ---------------------------------------------------------------------------


def _send_via_resend(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
) -> None:
    payload: dict = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html
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


def _send_via_smtp(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = _sanitize_header(reply_to)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
            return
    except Exception as e:
        logger.warning("STARTTLS failed: %s — trying SSL on 465", e)

    with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=10) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
) -> None:
    """Send email using best available method: Resend API > SMTP."""
    subject = _sanitize_header(subject)
    if settings.resend_api_key:
        _send_via_resend(to, subject, body_text, body_html, reply_to)
    elif settings.smtp_host:
        _send_via_smtp(to, subject, body_text, body_html, reply_to)
    else:
        logger.warning("No email backend configured (neither Resend nor SMTP)")


# ---------------------------------------------------------------------------
# HTML email wrapper
# ---------------------------------------------------------------------------

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f7f7f8;font-family:'Helvetica Neue',Arial,'Noto Sans JP',sans-serif;">
<table width="100%%" cellpadding="0" cellspacing="0" style="background:#f7f7f8;padding:32px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e5e7eb;">
<tr><td style="padding:32px 40px 24px;">
<img src="https://platform.aixis.jp/static/img/Aixis-logo-final.png" alt="Aixis" height="28" style="display:block;margin-bottom:24px;">
</td></tr>
<tr><td style="padding:0 40px 32px;font-size:15px;line-height:1.8;color:#1e293b;">
{content}
</td></tr>
<tr><td style="padding:24px 40px;border-top:1px solid #e5e7eb;font-size:11px;color:#94a3b8;line-height:1.6;">
Aixis | 独立系AI調査・監査機関<br>
<a href="https://platform.aixis.jp" style="color:#94a3b8;">platform.aixis.jp</a>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _wrap_html(content: str) -> str:
    return _HTML_WRAPPER.format(content=content)


# ---------------------------------------------------------------------------
# Invite email
# ---------------------------------------------------------------------------


def send_invite_email(user_name: str, user_email: str, invite_url: str) -> None:
    """Send account invitation email with password setup link."""
    subject = "[Aixis] アカウント招待 — パスワード設定のお願い"

    body_text = f"""{user_name} 様

Aixis プラットフォームへのアカウントが作成されました。
以下のリンクからパスワードを設定してログインしてください。

パスワード設定リンク（24時間有効）:
{invite_url}

リンクの有効期限が切れた場合は、管理者にご連絡ください。

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}

※ このメールは自動送信されています。
  本メールへの返信はお控えください。"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>Aixis プラットフォームへのアカウントが作成されました。<br>
以下のボタンからパスワードを設定してログインしてください。</p>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td style="background:#0f172a;padding:14px 32px;">
<a href="{invite_url}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;letter-spacing:0.02em;">パスワードを設定する →</a>
</td></tr>
</table>
<p style="font-size:13px;color:#64748b;">このリンクは24時間有効です。<br>
有効期限が切れた場合は管理者にご連絡ください。</p>"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Invite email sent to %s", user_email)


# ---------------------------------------------------------------------------
# Trial reminder email (3 days before expiry)
# ---------------------------------------------------------------------------


def send_trial_reminder_email(
    user_name: str, user_email: str, days_remaining: int
) -> None:
    """Send trial expiration reminder."""
    subject = f"[Aixis] 無料トライアル残り{days_remaining}日のお知らせ"

    body_text = f"""{user_name} 様

ご利用中の Aixis 無料トライアルの有効期限が残り{days_remaining}日となりました。

トライアル終了後は監査データベースへのアクセスが制限されます。
継続利用をご希望の場合は、お早めにプランのご契約をお願いいたします。

プラン・料金: https://platform.aixis.jp/pricing
お問い合わせ: https://platform.aixis.jp/contact

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>ご利用中の Aixis 無料トライアルの有効期限が<strong>残り{days_remaining}日</strong>となりました。</p>
<p>トライアル終了後は監査データベースへのアクセスが制限されます。<br>
継続利用をご希望の場合は、お早めにプランのご契約をお願いいたします。</p>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr>
<td style="background:#0f172a;padding:14px 32px;">
<a href="https://platform.aixis.jp/contact?type=subscription" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">プランを契約する →</a>
</td>
<td style="width:12px;"></td>
<td style="border:1px solid #d1d5db;padding:14px 24px;">
<a href="https://platform.aixis.jp/pricing" style="color:#374151;text-decoration:none;font-size:14px;font-weight:600;">料金を見る</a>
</td>
</tr>
</table>"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Trial reminder sent to %s (%d days remaining)", user_email, days_remaining)


# ---------------------------------------------------------------------------
# Trial expired email
# ---------------------------------------------------------------------------


def send_trial_expired_email(user_name: str, user_email: str) -> None:
    """Send trial expiration notification."""
    subject = "[Aixis] 無料トライアル終了のお知らせ"

    body_text = f"""{user_name} 様

Aixis 無料トライアルの有効期限が終了しました。
現在、監査データベースへのアクセスは制限されています。

引き続きご利用いただくには、プランのご契約をお願いいたします。

プラン・料金: https://platform.aixis.jp/pricing
お問い合わせ: https://platform.aixis.jp/contact

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>Aixis 無料トライアルの有効期限が終了しました。<br>
現在、監査データベースへのアクセスは制限されています。</p>
<p>引き続きご利用いただくには、プランのご契約をお願いいたします。</p>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td style="background:#0f172a;padding:14px 32px;">
<a href="https://platform.aixis.jp/contact?type=subscription" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">プランを契約する →</a>
</td></tr>
</table>"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Trial expired notification sent to %s", user_email)


# ---------------------------------------------------------------------------
# Admin notification: new client created
# ---------------------------------------------------------------------------


def send_admin_new_client_notification(client_name: str, client_email: str, org_name: str) -> None:
    """Notify admin when a new client account is created."""
    subject = f"[Aixis] 新規クライアント作成: {client_name}"
    body_text = f"""新規クライアントアカウントが作成されました。

お名前: {client_name}
メール: {client_email}
組織: {org_name}
ステータス: 招待メール送信済み（パスワード設定待ち）

管理画面: https://platform.aixis.jp/dashboard/clients"""

    send_email(settings.smtp_to, subject, body_text)
