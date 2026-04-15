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
<body style="margin:0;padding:0;background:#f7f7f8;font-family:'Helvetica Neue',Arial,'Noto Serif JP',serif;">
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


# ---------------------------------------------------------------------------
# Free-registration: email verification
# ---------------------------------------------------------------------------


def send_email_verification(user_name: str, user_email: str, verify_url: str) -> None:
    """Send an email verification link to a newly self-registered user."""
    subject = "[Aixis] メールアドレスのご確認をお願いします"

    body_text = f"""{user_name} 様

Aixis AI監査プラットフォームへのご登録ありがとうございます。
本メールは登録時にご入力いただいたメールアドレスの確認のため送信しています。

下記のリンクをクリックしてメールアドレスの確認を完了してください:

{verify_url}

※このリンクは24時間で失効します。
※心当たりのないメールの場合は、このメールを破棄してください。

--
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">メールアドレスのご確認</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 16px;">
Aixis AI監査プラットフォームへのご登録ありがとうございます。<br>
本メールは登録時にご入力いただいたメールアドレスの確認のため送信しています。
</p>
<p style="margin:24px 0;">
  <a href="{verify_url}" style="display:inline-block;padding:12px 28px;background:#0ea5e9;color:#ffffff;text-decoration:none;font-weight:600;border-radius:2px;">
    メールアドレスを確認する
  </a>
</p>
<p style="margin:0 0 8px;font-size:12px;color:#64748b;">
ボタンが機能しない場合は、下記のURLをブラウザに貼り付けてください:<br>
<span style="word-break:break-all;color:#0ea5e9;">{verify_url}</span>
</p>
<p style="margin:16px 0 0;font-size:12px;color:#64748b;">
※このリンクは24時間で失効します。<br>
※心当たりのないメールの場合は、このメールを破棄してください。
</p>
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Email verification sent to %s", user_email)


def send_registration_welcome(user_name: str, user_email: str) -> None:
    """Welcome message sent after the user successfully verifies their email."""
    subject = "[Aixis] ご登録ありがとうございます — ご利用案内"

    body_text = f"""{user_name} 様

Aixis AI監査プラットフォームへのご登録が完了しました。

本日より、以下のコンテンツを無料でご利用いただけます:

■ 全ツールの5軸詳細スコア
■ スコア推移・カテゴリ内ポジショニング
■ 強み・弱み・リスク分析
■ リスクガバナンス詳細
■ 最大10件のツール比較
■ 監査レポートPDFダウンロード

----------
次のステップ: 貴社業務に合うAIツールを見つける
----------

1. ツール一覧を閲覧
   https://platform.aixis.jp/tools

2. カテゴリから探す（資料作成AI / 議事録AI / 翻訳AI）
   https://platform.aixis.jp/categories

3. 複数ツールを比較する
   https://platform.aixis.jp/compare

----------
「結局、うちの会社には何を導入すべきか?」にお答えします
----------

プラットフォームの汎用データは業界横断の参考情報です。
貴社固有の業務要件・運用制約・既存ツール構成を反映した個別評価をご希望の方は、
アドバイザリー監査をご検討ください:

  ■ スポット監査 ¥29,800 / 1ツール
    貴社の業務要件に合わせた単一ツール評価

  ■ ベンチマーク監査 ¥98,000 / 3〜5ツール
    貴社の選定基準でのツール比較

  ■ ガバナンス監査 ¥198,000〜
    組織全体のAI導入ガバナンス評価

詳細・お問い合わせ:
https://aixis.jp/contact?subject=advisory

--
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
info@aixis.jp"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">ご登録ありがとうございます</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 16px;">
Aixis AI監査プラットフォームへのご登録が完了しました。<br>
本日より、以下のコンテンツを無料でご利用いただけます。
</p>
<ul style="margin:0 0 20px;padding-left:20px;line-height:1.8;">
  <li>全ツールの5軸詳細スコア</li>
  <li>スコア推移・カテゴリ内ポジショニング</li>
  <li>強み・弱み・リスク分析</li>
  <li>リスクガバナンス詳細</li>
  <li>最大10件のツール比較</li>
  <li>監査レポートPDFダウンロード</li>
</ul>
<p style="margin:24px 0 8px;font-weight:600;color:#0f172a;">次のステップ</p>
<p style="margin:0 0 16px;">
  <a href="https://platform.aixis.jp/tools" style="display:inline-block;padding:10px 20px;background:#0ea5e9;color:#ffffff;text-decoration:none;font-weight:600;border-radius:2px;">
    ツール一覧を見る
  </a>
</p>
<hr style="border:0;border-top:1px solid #e5e7eb;margin:32px 0;">
<p style="margin:0 0 8px;font-size:14px;font-weight:600;color:#0f172a;">
「結局、うちの会社には何を導入すべきか?」にお答えします
</p>
<p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:#475569;">
プラットフォームの汎用データは業界横断の参考情報です。
貴社固有の業務要件・運用制約・既存ツール構成を反映した個別評価をご希望の方は、アドバイザリー監査をご検討ください。
</p>
<ul style="margin:0 0 16px;padding-left:20px;font-size:13px;line-height:1.7;color:#475569;">
  <li>スポット監査 ¥29,800 / 1ツール</li>
  <li>ベンチマーク監査 ¥98,000 / 3〜5ツール</li>
  <li>ガバナンス監査 ¥198,000〜</li>
</ul>
<p style="margin:0;">
  <a href="https://aixis.jp/contact?subject=advisory" style="color:#0ea5e9;font-size:13px;">アドバイザリー監査のお問い合わせはこちら →</a>
</p>
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Welcome (post-verification) email sent to %s", user_email)


def send_admin_new_registration_notification(
    user_name: str,
    user_email: str,
    company_name: str,
    job_title: str,
    industry: str,
    employee_count: str,
) -> None:
    """Notify admin (info@aixis.jp) when a new free-registered user appears."""
    subject = f"[Aixisリード] 新規登録: {user_name} / {company_name}"

    body_text = f"""新しい無料登録ユーザーがいます。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
お名前        {user_name}
メール        {user_email}
会社名        {company_name}
役職          {job_title}
業種          {industry}
会社規模      {employee_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

リード管理画面:
https://platform.aixis.jp/dashboard/leads

このユーザーの詳細・閲覧履歴・リードスコアはダッシュボードから確認できます。
アドバイザリー監査の営業タイミングを確認してください。"""

    send_email(settings.smtp_to, subject, body_text)
    logger.info("Admin new-registration notification sent for %s", user_email)


# ---------------------------------------------------------------------------
# Drip campaign emails (Phase 5: day 3 / 7 / 14 / 30 after verification)
# ---------------------------------------------------------------------------
#
# Day 0 is handled by `send_registration_welcome` (fired on verification).
# The scheduler's _check_due_drip_emails() calls these in order based on
# how many days have elapsed since user.email_verified_at.
# Every drip email includes an unsubscribe/preferences line pointing at
# mypage — when we add an opt-out endpoint in Phase 6 it will link here.


def _drip_footer_text(unsubscribe_url: str | None = None) -> str:
    unsub = unsubscribe_url or "https://platform.aixis.jp/mypage"
    return (
        "\n\n--\n"
        "Aixis | 独立系AI調査・監査機関\n"
        f"ワンクリック配信停止: {unsub}\n"
        "その他の設定変更: https://platform.aixis.jp/mypage"
    )


def _drip_footer_html(unsubscribe_url: str | None = None) -> str:
    unsub = unsubscribe_url or "https://platform.aixis.jp/mypage"
    return (
        '<p style="margin:24px 0 0;padding-top:16px;border-top:1px solid #e5e7eb;'
        'font-size:11px;color:#94a3b8;line-height:1.6;">'
        'このメールの配信を停止するには '
        f'<a href="{unsub}" style="color:#94a3b8;text-decoration:underline;">'
        'こちら（ワンクリック）</a>。'
        'その他の設定変更は '
        '<a href="https://platform.aixis.jp/mypage" style="color:#94a3b8;">'
        'マイページ</a> からお手続きいただけます。</p>'
    )


def send_drip_industry_top5(
    user_name: str,
    user_email: str,
    industry_label_jp: str | None,
    top_tools: list[dict] | None = None,
    unsubscribe_url: str | None = None,
) -> None:
    """Day 3: industry-specific top-tools digest.

    ``top_tools`` is a list of dicts with keys: name_jp, vendor, slug, overall_grade.
    If empty, falls back to a generic directory link.
    """
    scope = f"{industry_label_jp}業界" if industry_label_jp else "注目"
    subject = f"[Aixis] {scope}で評価の高いAIツール"

    if top_tools:
        tools_txt = "\n".join(
            f"■ {t['name_jp']}（{t.get('vendor', '') or '-'}）— 総合評価 "
            f"{t.get('overall_grade', '-')}\n"
            f"  https://platform.aixis.jp/tools/{t['slug']}"
            for t in top_tools[:5]
        )
        tools_html = "".join(
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #f1f5f9;">'
            f'<a href="https://platform.aixis.jp/tools/{t["slug"]}" '
            f'style="color:#0f172a;text-decoration:none;font-weight:600;font-size:14px;">{t["name_jp"]}</a>'
            f'<span style="color:#94a3b8;font-size:12px;"> — {t.get("vendor", "") or ""}</span>'
            f'<div style="color:#64748b;font-size:12px;margin-top:2px;">総合評価 '
            f'{t.get("overall_grade", "-")}</div>'
            f"</td></tr>"
            for t in top_tools[:5]
        )
    else:
        tools_txt = "https://platform.aixis.jp/tools"
        tools_html = (
            '<tr><td style="padding:10px 0;">'
            '<a href="https://platform.aixis.jp/tools" '
            'style="color:#0ea5e9;font-size:14px;">ツール一覧を見る →</a>'
            "</td></tr>"
        )

    body_text = f"""{user_name} 様

Aixisへのご登録ありがとうございます。
ご登録時に選択いただいた{scope}において、Aixisの独立監査で高評価を獲得しているAIツールをご紹介します。

{tools_txt}

──────────
各ツールの詳細ページでは、実務適性・費用対効果・ローカライゼーション・安全性・革新性の5軸評価と、リスクガバナンス情報をご確認いただけます。

業務要件に合わせた個別評価をご希望の方は、アドバイザリー監査もご検討ください:
https://aixis.jp/contact?subject=advisory{_drip_footer_text(unsubscribe_url)}"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">{scope}で注目のAIツール</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 20px;">
ご登録時に選択いただいた{scope}において、Aixisの独立監査で高評価を獲得しているツールをご紹介します。
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
{tools_html}
</table>
<p style="margin:0 0 8px;font-size:13px;color:#64748b;line-height:1.7;">
各ツールの詳細ページでは、実務適性・費用対効果・ローカライゼーション・安全性・革新性の5軸評価と、
リスクガバナンス情報をご確認いただけます。
</p>
<p style="margin:20px 0 0;font-size:13px;">
  <a href="https://aixis.jp/contact?subject=advisory" style="color:#0ea5e9;">
  業務要件に合わせた個別評価はアドバイザリー監査へ →</a>
</p>
{_drip_footer_html(unsubscribe_url)}
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Drip day-3 (industry top 5) sent to %s", user_email)


def send_drip_advisory_intro(
    user_name: str,
    user_email: str,
    unsubscribe_url: str | None = None,
) -> None:
    """Day 7: introduce the advisory-audit service in more depth."""
    subject = "[Aixis] アドバイザリー監査 — 貴社固有の判断のために"

    body_text = f"""{user_name} 様

Aixisをご利用いただきありがとうございます。

プラットフォームに公開している監査データは、独立第三者の立場で作成された業界横断の参考情報です。
ただし、実際の導入判断には「貴社固有の業務フロー」「取り扱うデータの機微度」「既存システムとの適合」
といった個別要素が大きく影響します。

Aixisの「アドバイザリー監査」は、これらの個別要素を反映した独立評価サービスです。

■ スポット監査 ¥29,800 / 1ツール
  貴社の業務要件に合わせた単一ツールの適合性評価

■ ベンチマーク監査 ¥98,000 / 3〜5ツール
  貴社の選定基準に沿った複数ツールの比較評価

■ ガバナンス監査 ¥198,000〜
  組織全体のAI導入ガバナンス評価

──────────
Aixisは特定のAIベンダーと資本・業務提携関係を持たない独立機関です。
評価結果は中立性を担保したうえでご報告いたします。

お問い合わせ:
https://aixis.jp/contact?subject=advisory{_drip_footer_text(unsubscribe_url)}"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">貴社固有の判断のために</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 16px;">
プラットフォームに公開している監査データは、独立第三者の立場で作成された業界横断の参考情報です。
ただし、実際の導入判断には「貴社固有の業務フロー」「取り扱うデータの機微度」「既存システムとの適合」
といった個別要素が大きく影響します。
</p>
<p style="margin:0 0 20px;">
Aixisの<strong>アドバイザリー監査</strong>は、これらの個別要素を反映した独立評価サービスです。
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;background:#f8fafc;padding:16px;">
<tr><td style="padding:8px 0;border-bottom:1px solid #e2e8f0;">
<div style="font-weight:600;color:#0f172a;font-size:14px;">スポット監査 ¥29,800 / 1ツール</div>
<div style="font-size:12px;color:#64748b;margin-top:2px;">貴社の業務要件に合わせた単一ツールの適合性評価</div>
</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #e2e8f0;">
<div style="font-weight:600;color:#0f172a;font-size:14px;">ベンチマーク監査 ¥98,000 / 3〜5ツール</div>
<div style="font-size:12px;color:#64748b;margin-top:2px;">貴社の選定基準に沿った複数ツールの比較評価</div>
</td></tr>
<tr><td style="padding:8px 0;">
<div style="font-weight:600;color:#0f172a;font-size:14px;">ガバナンス監査 ¥198,000〜</div>
<div style="font-size:12px;color:#64748b;margin-top:2px;">組織全体のAI導入ガバナンス評価</div>
</td></tr>
</table>
<p style="margin:0 0 20px;font-size:12px;color:#94a3b8;line-height:1.6;">
Aixisは特定のAIベンダーと資本・業務提携関係を持たない独立機関です。
評価結果は中立性を担保したうえでご報告いたします。
</p>
<p style="margin:0;">
  <a href="https://aixis.jp/contact?subject=advisory" style="display:inline-block;padding:12px 24px;background:#0f172a;color:#ffffff;text-decoration:none;font-weight:600;font-size:13px;">
    アドバイザリー監査のご相談 →
  </a>
</p>
{_drip_footer_html(unsubscribe_url)}
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Drip day-7 (advisory intro) sent to %s", user_email)


def send_drip_free_consult(
    user_name: str,
    user_email: str,
    unsubscribe_url: str | None = None,
) -> None:
    """Day 14: offer a free 30-minute consultation."""
    subject = "[Aixis] 30分無料相談 — AI導入の優先順位を整理しませんか"

    body_text = f"""{user_name} 様

Aixisのご登録から2週間が経過しました。AIツールの比較はお進みでしょうか。

「AIを入れたいが、どこから始めるべきかわからない」
「導入済みのツールが本当に最適なのか判断がつかない」
「社内でAI活用の優先順位を整理する必要がある」

このようなご状況であれば、Aixisのアナリストが30分の無料相談を承っております。

■ お話しできる内容
  ・貴社の業務フローに対して優先度の高いAI活用領域の整理
  ・Aixisの監査データに基づく候補ツールの絞り込み
  ・アドバイザリー監査の進め方・費用のご案内

営業色のない、あくまで「判断を整理するための会話」としてご利用ください。
ご希望の日時でご予約いただけます。

お申し込み:
https://aixis.jp/contact?subject=advisory{_drip_footer_text(unsubscribe_url)}"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">30分無料相談のご案内</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 16px;">
Aixisのご登録から2週間が経過しました。AIツールの比較はお進みでしょうか。
</p>
<p style="margin:0 0 16px;padding:16px;background:#f8fafc;border-left:3px solid #0f172a;font-size:13px;color:#475569;line-height:1.8;">
「AIを入れたいが、どこから始めるべきかわからない」<br>
「導入済みのツールが本当に最適なのか判断がつかない」<br>
「社内でAI活用の優先順位を整理する必要がある」
</p>
<p style="margin:0 0 16px;">
このようなご状況であれば、Aixisのアナリストが<strong>30分の無料相談</strong>を承っております。
</p>
<h3 style="font-size:13px;margin:20px 0 8px;color:#0f172a;">お話しできる内容</h3>
<ul style="margin:0 0 20px;padding-left:20px;font-size:13px;line-height:1.8;color:#475569;">
  <li>貴社の業務フローに対して優先度の高いAI活用領域の整理</li>
  <li>Aixisの監査データに基づく候補ツールの絞り込み</li>
  <li>アドバイザリー監査の進め方・費用のご案内</li>
</ul>
<p style="margin:0 0 20px;font-size:12px;color:#94a3b8;line-height:1.6;">
営業色のない、あくまで「判断を整理するための会話」としてご利用ください。
</p>
<p style="margin:0;">
  <a href="https://aixis.jp/contact?subject=advisory" style="display:inline-block;padding:12px 24px;background:#0f172a;color:#ffffff;text-decoration:none;font-weight:600;font-size:13px;">
    無料相談を予約する →
  </a>
</p>
{_drip_footer_html(unsubscribe_url)}
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Drip day-14 (free consult) sent to %s", user_email)


def send_drip_benchmark_pitch(
    user_name: str,
    user_email: str,
    unsubscribe_url: str | None = None,
) -> None:
    """Day 30: benchmark-audit case-study pitch."""
    subject = "[Aixis] ベンチマーク監査 — 社内選定の納得感を定量化する"

    body_text = f"""{user_name} 様

Aixisのご登録から1ヶ月が経過しました。

AIツール選定の現場でよく伺うお悩みがあります:
「部門ごとに好みのツールがあり、全社統一の判断ができない」
「稟議の際に定量的な比較根拠が求められる」
「ベンダーの提案資料だけでは中立性に欠ける」

Aixisの「ベンチマーク監査（¥98,000 / 3〜5ツール）」は、これらの場面で活用いただいているサービスです。

■ ベンチマーク監査で納品するもの
  ・貴社の選定基準に基づく評価マトリクス（独立第三者作成）
  ・5軸評価 × 3〜5ツールの比較レポート
  ・リスクガバナンス観点でのチェックリスト
  ・社内稟議・役員会で利用できる整形済みPDF

■ 実際の利用シーン
  ・「議事録AIを全社導入するにあたって3ツール比較したい」
  ・「営業部が使っているAIツールの代替候補を検討したい」
  ・「ガバナンス部門の稟議のために中立評価が必要」

プラットフォームの汎用データでは拾えない「貴社の判断基準での比較」を、
2週間程度で納品いたします。

お問い合わせ:
https://aixis.jp/contact?subject=advisory{_drip_footer_text(unsubscribe_url)}"""

    html_content = f"""
<h2 style="font-size:18px;margin:0 0 16px;color:#0f172a;">社内選定の納得感を定量化する</h2>
<p style="margin:0 0 16px;">{_sanitize_header(user_name)} 様</p>
<p style="margin:0 0 16px;">
Aixisのご登録から1ヶ月が経過しました。AIツール選定の現場でよく伺うお悩みがあります。
</p>
<p style="margin:0 0 16px;padding:16px;background:#f8fafc;border-left:3px solid #0f172a;font-size:13px;color:#475569;line-height:1.8;">
「部門ごとに好みのツールがあり、全社統一の判断ができない」<br>
「稟議の際に定量的な比較根拠が求められる」<br>
「ベンダーの提案資料だけでは中立性に欠ける」
</p>
<p style="margin:0 0 20px;">
Aixisの<strong>ベンチマーク監査（¥98,000 / 3〜5ツール）</strong>は、これらの場面で活用いただいているサービスです。
</p>
<h3 style="font-size:13px;margin:20px 0 8px;color:#0f172a;">納品物</h3>
<ul style="margin:0 0 20px;padding-left:20px;font-size:13px;line-height:1.8;color:#475569;">
  <li>貴社の選定基準に基づく評価マトリクス（独立第三者作成）</li>
  <li>5軸評価 × 3〜5ツールの比較レポート</li>
  <li>リスクガバナンス観点でのチェックリスト</li>
  <li>社内稟議・役員会で利用できる整形済みPDF</li>
</ul>
<h3 style="font-size:13px;margin:20px 0 8px;color:#0f172a;">利用シーン</h3>
<ul style="margin:0 0 20px;padding-left:20px;font-size:13px;line-height:1.8;color:#475569;">
  <li>議事録AIの全社導入にあたり3ツール比較したい</li>
  <li>営業部で使っているAIツールの代替候補を検討したい</li>
  <li>ガバナンス部門の稟議のために中立評価が必要</li>
</ul>
<p style="margin:0 0 20px;font-size:12px;color:#94a3b8;line-height:1.6;">
プラットフォームの汎用データでは拾えない「貴社の判断基準での比較」を、2週間程度で納品いたします。
</p>
<p style="margin:0;">
  <a href="https://aixis.jp/contact?subject=advisory" style="display:inline-block;padding:12px 24px;background:#0f172a;color:#ffffff;text-decoration:none;font-weight:600;font-size:13px;">
    ベンチマーク監査のご相談 →
  </a>
</p>
{_drip_footer_html(unsubscribe_url)}
"""

    send_email(user_email, subject, body_text, _wrap_html(html_content))
    logger.info("Drip day-30 (benchmark pitch) sent to %s", user_email)
