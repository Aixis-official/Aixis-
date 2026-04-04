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
<body style="margin:0;padding:0;background:#f7f7f8;font-family:'Helvetica Neue',Arial,'Noto Serif JP',sans-serif;">
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
# Welcome email series (3-part onboarding)
# ---------------------------------------------------------------------------


def send_welcome_email_day1(user_name: str, user_email: str) -> None:
    """Day 1: Welcome + Quick Start guide."""
    subject = "[Aixis] ようこそ！14日間無料トライアルの始め方"

    body_text = f"""{user_name} 様

Aixis プラットフォームへようこそ！
14日間の無料トライアルが始まりました。

まずは以下の3ステップで、Aixis を使い始めましょう。

1. 監査データベースを見る
   主要なAIツールの監査レポートを閲覧できます。

2. ツールを比較する
   複数のAIツールを並べて比較し、最適なものを選べます。

3. レポートを活用する
   監査結果をもとに、組織のAIガバナンスに役立てましょう。

ツール一覧はこちら: https://platform.aixis.jp/tools

ご不明な点がありましたら、お気軽にお問い合わせください。

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}

※ このメールは自動送信されています。"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>Aixis プラットフォームへようこそ！<br>
14日間の無料トライアルが始まりました。</p>
<p style="font-weight:600;">まずは以下の3ステップで始めましょう：</p>
<table cellpadding="0" cellspacing="0" style="margin:16px 0 24px;">
<tr><td style="padding:12px 16px;border-left:3px solid #0f172a;">
<strong>1. 監査データベースを見る</strong><br>
<span style="font-size:13px;color:#64748b;">主要なAIツールの監査レポートを閲覧できます。</span>
</td></tr>
<tr><td style="padding:12px 16px;border-left:3px solid #0f172a;">
<strong>2. ツールを比較する</strong><br>
<span style="font-size:13px;color:#64748b;">複数のAIツールを並べて比較し、最適なものを選べます。</span>
</td></tr>
<tr><td style="padding:12px 16px;border-left:3px solid #0f172a;">
<strong>3. レポートを活用する</strong><br>
<span style="font-size:13px;color:#64748b;">監査結果をもとに、組織のAIガバナンスに役立てましょう。</span>
</td></tr>
</table>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td style="background:#0f172a;padding:14px 32px;">
<a href="https://platform.aixis.jp/tools" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;letter-spacing:0.02em;">ツール一覧を見る →</a>
</td></tr>
</table>
<p style="font-size:13px;color:#64748b;">ご不明な点がありましたら、お気軽にお問い合わせください。</p>"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Welcome day-1 email sent to %s", user_email)


def send_welcome_email_day3(user_name: str, user_email: str) -> None:
    """Day 3: Feature Highlight — 5-axis scoring and comparison."""
    subject = "[Aixis] 活用ヒント：5軸スコアの読み方と比較機能"

    body_text = f"""{user_name} 様

Aixis をご利用いただきありがとうございます。
今日は、プラットフォームの主要機能をご紹介します。

■ 5軸スコア（Five-Axis Score）
Aixis の監査レポートでは、AIツールを以下の5軸で評価しています。
各スコアを確認することで、ツールの強みと課題を把握できます。

■ 比較機能
複数のAIツールを並べて比較し、用途に最適なツールを選べます。
比較画面: https://platform.aixis.jp/compare

■ リスク・ガバナンス情報
AIツール導入時のリスク評価やガバナンス対応状況も確認できます。

ツール一覧: https://platform.aixis.jp/tools

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}

※ このメールは自動送信されています。"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>Aixis をご利用いただきありがとうございます。<br>
今日は、プラットフォームの主要機能をご紹介します。</p>
<table cellpadding="0" cellspacing="0" style="margin:16px 0 24px;width:100%%;">
<tr><td style="padding:16px;background:#f8fafc;border-left:3px solid #0f172a;margin-bottom:8px;">
<strong style="font-size:14px;">5軸スコア（Five-Axis Score）</strong><br>
<span style="font-size:13px;color:#64748b;">AIツールを5つの軸で評価。各スコアでツールの強みと課題を把握できます。</span>
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:16px;background:#f8fafc;border-left:3px solid #0f172a;">
<strong style="font-size:14px;">比較機能</strong><br>
<span style="font-size:13px;color:#64748b;">複数のAIツールを並べて比較し、用途に最適なツールを選べます。</span>
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:16px;background:#f8fafc;border-left:3px solid #0f172a;">
<strong style="font-size:14px;">リスク・ガバナンス情報</strong><br>
<span style="font-size:13px;color:#64748b;">AIツール導入時のリスク評価やガバナンス対応状況も確認できます。</span>
</td></tr>
</table>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr>
<td style="background:#0f172a;padding:14px 32px;">
<a href="https://platform.aixis.jp/tools" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">ツール一覧 →</a>
</td>
<td style="width:12px;"></td>
<td style="border:1px solid #d1d5db;padding:14px 24px;">
<a href="https://platform.aixis.jp/compare" style="color:#374151;text-decoration:none;font-size:14px;font-weight:600;">比較する</a>
</td>
</tr>
</table>"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Welcome day-3 email sent to %s", user_email)


def send_welcome_email_day7(user_name: str, user_email: str) -> None:
    """Day 7: Value reminder — trial midpoint, encourage upgrade."""
    subject = "[Aixis] トライアル残り7日 — 監査データの活用法"

    body_text = f"""{user_name} 様

Aixis 無料トライアルの折り返し地点です。残り7日となりました。

■ Aixis でできること
- 主要AIツールの独立監査レポートを閲覧
- 5軸スコアで客観的にツールを評価・比較
- 組織のAIガバナンス・リスク管理に活用

■ トライアル終了後
トライアル終了後は、監査データベースへのアクセスが制限されます。
継続してご利用いただくには、プランのご契約をお願いいたします。

プラン・料金: https://platform.aixis.jp/pricing
お問い合わせ: https://platform.aixis.jp/contact

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}

※ このメールは自動送信されています。"""

    html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user_name} 様</p>
<p>Aixis 無料トライアルの折り返し地点です。<strong>残り7日</strong>となりました。</p>
<p style="font-weight:600;margin:24px 0 12px;">Aixis でできること：</p>
<table cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
<tr><td style="padding:8px 16px;">
・主要AIツールの独立監査レポートを閲覧
</td></tr>
<tr><td style="padding:8px 16px;">
・5軸スコアで客観的にツールを評価・比較
</td></tr>
<tr><td style="padding:8px 16px;">
・組織のAIガバナンス・リスク管理に活用
</td></tr>
</table>
<p>トライアル終了後は、監査データベースへのアクセスが制限されます。<br>
継続してご利用いただくには、プランのご契約をお願いいたします。</p>
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
    logger.info("Welcome day-7 email sent to %s", user_email)


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
