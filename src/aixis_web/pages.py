"""SSR page routes using Jinja2 templates."""
import logging
import time
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .api.deps import get_current_user
from .db.base import get_db
from .db.models.user import User
from .config import settings
from .i18n import get_translator, detect_language

_page_logger = logging.getLogger(__name__)

try:
    from .services.subscription_service import get_subscription_info
except ImportError:
    get_subscription_info = None

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Register global template functions
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)


def _render(name: str, ctx: dict, status_code: int = 200):
    """Render template compatible with both old and new Starlette APIs."""
    request = ctx.pop("request")
    try:
        # Starlette 0.46+: TemplateResponse(request, name, context)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)
    except TypeError:
        # Fallback for older Starlette
        ctx["request"] = request
        return templates.TemplateResponse(name, ctx, status_code=status_code)

page_router = APIRouter(default_response_class=HTMLResponse)


def _get_template_context(request: Request, user=None, **extra) -> dict:
    """Build template context with i18n support and optional subscription info."""
    lang = detect_language(
        query_param=request.query_params.get("lang"),
        accept_language=request.headers.get("accept-language"),
        user_pref=getattr(user, "preferred_language", None) if user else None,
        cookie_lang=request.cookies.get("aixis_lang"),
    )
    translator = get_translator(lang)
    ctx = {
        "request": request,
        "user": user,
        "_": translator,
        "lang": lang,
        "subscription": None,
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        # Phase C-4: Umami self-host (opt-in via env). When both are set the
        # template emits a privacy-first analytics snippet alongside GA4.
        "umami_url": settings.umami_url,
        "umami_website_id": settings.umami_website_id,
        **extra,
    }
    # Attach subscription info when user is authenticated
    if user and get_subscription_info is not None:
        try:
            ctx["subscription"] = get_subscription_info(user)
        except Exception:
            pass
    return ctx


# Common dependency for optional user on public pages
_OptionalUser = Annotated[User | None, Depends(get_current_user)]


# ──────────── SSR Stats Helper ────────────

async def _get_platform_stats_for_ssr(db: AsyncSession) -> dict:
    """Fetch platform stats for server-side rendering.

    Reuses the API module's cache to avoid duplicate DB queries.
    Returns a plain dict suitable for Jinja2 context.
    """
    try:
        from .api.v1.stats import get_platform_stats, _stats_cache, _STATS_TTL

        # Check cache first (same cache as the API endpoint)
        now_ts = time.time()
        if _stats_cache["data"] is not None and (now_ts - _stats_cache["ts"]) < _STATS_TTL:
            cached = _stats_cache["data"]
            return {
                "audited_tools": cached.audited_tools,
                "categories": cached.categories,
                "last_updated": cached.last_updated or "—",
                "new_this_month": cached.new_this_month,
            }

        # Cache miss — run queries directly (lighter than calling the endpoint)
        from .db.models.tool import Tool
        from .db.models.score import ToolPublishedScore

        tools_with_scores = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id)))
        )
        audited_tools = tools_with_scores.scalar() or 0

        cat_count = await db.execute(
            select(func.count(func.distinct(Tool.category_id)))
            .join(ToolPublishedScore, ToolPublishedScore.tool_id == Tool.id)
            .where(
                Tool.is_public.is_(True),
                Tool.is_active.is_(True),
                Tool.category_id.isnot(None),
            )
        )
        categories = cat_count.scalar() or 0

        last_score = await db.execute(
            select(func.max(ToolPublishedScore.published_at))
        )
        last_updated_dt = last_score.scalar()
        if last_updated_dt:
            # Convert to JST (UTC+9) for Japanese users
            JST = timezone(timedelta(hours=9))
            if last_updated_dt.tzinfo is None:
                last_updated_dt = last_updated_dt.replace(tzinfo=timezone.utc)
            last_updated = last_updated_dt.astimezone(JST).strftime("%Y.%m.%d")
        else:
            last_updated = "—"

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        new_month = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id))).where(
                ToolPublishedScore.published_at >= month_start
            )
        )
        new_this_month = new_month.scalar() or 0

        return {
            "audited_tools": audited_tools,
            "categories": categories,
            "last_updated": last_updated,
            "new_this_month": new_this_month,
        }
    except Exception:
        _page_logger.debug("SSR stats fetch failed, using defaults", exc_info=True)
        return {
            "audited_tools": "--",
            "categories": "--",
            "last_updated": "—",
            "new_this_month": "--",
        }


# ──────────── Legacy /platform redirect ────────────


@page_router.get("/platform")
async def legacy_platform_landing(
    request: Request,
    user: _OptionalUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Serve landing page at old /platform URL for cached 301 redirects."""
    stats = await _get_platform_stats_for_ssr(db)
    ctx = _get_template_context(request, user=user, title="Aixis（アイクシス） | AIツール独立5軸監査・比較", active_page="home", stats=stats)
    return _render("public/landing.html", ctx)


@page_router.get("/platform/{path:path}")
async def legacy_platform_redirect(path: str):
    """Redirect old /platform/... URLs to new /... URLs.

    Sanitizes the path to prevent open redirect attacks.
    """
    import re
    # Only allow safe path characters (alphanumeric, hyphens, slashes, underscores)
    safe_path = re.sub(r'[^a-zA-Z0-9\-_/]', '', path)
    # Strip leading slashes to prevent //evil.com open redirect
    safe_path = safe_path.lstrip('/')
    if not safe_path:
        return RedirectResponse(url="/", status_code=301)
    return RedirectResponse(url=f"/{safe_path}", status_code=301)


# ──────────── Public Pages ────────────


@page_router.get("/")
async def landing(
    request: Request,
    user: _OptionalUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Landing page."""
    stats = await _get_platform_stats_for_ssr(db)
    ctx = _get_template_context(request, user=user, title="Aixis（アイクシス） | AIツール独立5軸監査・比較", active_page="home", stats=stats)
    return _render("public/landing.html", ctx)


@page_router.get("/tools")
async def tools_page(request: Request, user: _OptionalUser = None):
    """Tool catalog page."""
    ctx = _get_template_context(request, user=user, title="AIツール監査データベース | 独立5軸評価で比較", active_page="tools")
    return _render("public/tools.html", ctx)


@page_router.get("/categories")
async def categories_index(request: Request, user: _OptionalUser = None):
    """Categories index page."""
    ctx = _get_template_context(request, user=user, title="AIツール カテゴリ別比較・ランキング", active_page="categories")
    return _render("public/categories.html", ctx)


@page_router.get("/tools/{slug}")
async def tool_detail_page(request: Request, slug: str, user: _OptionalUser = None, db: AsyncSession = Depends(get_db)):
    """Tool detail page with full SSR for SEO (Googlebot sees real content)."""
    from .db.models.tool import Tool, ToolCategory
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Tool)
        .where(Tool.slug == slug, Tool.is_public == True)  # noqa: E712
        .options(selectinload(Tool.scores), selectinload(Tool.category))
    )
    tool = result.scalar_one_or_none()

    if tool:
        seo_title = tool.seo_title_jp or f"{tool.name_jp or tool.name} レビュー・評価"
        seo_desc = tool.seo_description_jp or tool.description_jp or f"{tool.name_jp or tool.name}の実務適性・費用対効果・日本語能力・安全性・革新性を独立監査で5軸評価。"
        seo_keywords = tool.seo_keywords_jp or []

        # Build SSR data for visible HTML content (Googlebot can read this)
        latest_score = tool.scores[0] if tool.scores else None
        category_name = tool.category.name_jp if tool.category else ""

        tool_data = {
            "name": tool.name,
            "name_jp": tool.name_jp,
            "vendor": tool.vendor,
            "description_jp": tool.description_jp,
            "logo_url": tool.logo_url,
            "url": tool.url,
            "category_id": tool.category_id,
        }

        # Full SSR context for server-rendered HTML
        ssr = {
            "name": tool.name_jp or tool.name,
            "vendor": tool.vendor or "",
            "description": tool.description_jp or "",
            "logo_url": tool.logo_url,
            "category": category_name,
            "pricing_model": tool.pricing_model or "",
            "executive_summary": tool.executive_summary_jp or "",
            "pros": tool.pros_jp or [],
            "cons": tool.cons_jp or [],
            "features": tool.features or [],
            "url": tool.url or "",
        }
        if latest_score:
            ssr["score"] = {
                "overall": round(latest_score.overall_score, 1) if latest_score.overall_score else None,
                "grade": latest_score.overall_grade,
                "practicality": round(latest_score.practicality, 1) if latest_score.practicality else None,
                "cost_performance": round(latest_score.cost_performance, 1) if latest_score.cost_performance else None,
                "localization": round(latest_score.localization, 1) if latest_score.localization else None,
                "safety": round(latest_score.safety, 1) if latest_score.safety else None,
                "uniqueness": round(latest_score.uniqueness, 1) if latest_score.uniqueness else None,
                "version": latest_score.version,
                "published_at": latest_score.published_at.strftime("%Y年%m月") if latest_score.published_at else "",
            }
        else:
            ssr["score"] = None
    else:
        seo_title = "ツール詳細レビュー・評価"
        seo_desc = "AIツールの詳細レビュー・5軸評価スコア。"
        seo_keywords = []
        tool_data = None
        ssr = None

    ctx = _get_template_context(
        request, user=user, title=seo_title, slug=slug, active_page="tools",
        seo_description=seo_desc,
        seo_keywords=seo_keywords,
        tool_data=tool_data,
        ssr=ssr,
    )
    # Return 404 status when tool not found so Google doesn't index empty pages
    status = 404 if tool is None else 200
    return _render("public/tool_detail.html", ctx, status_code=status)


@page_router.get("/compare")
async def compare_page(request: Request, user: _OptionalUser = None):
    """Comparison view page."""
    ctx = _get_template_context(request, user=user, title="AIツール比較 | 5軸スコアで横並び比較", active_page="compare")
    return _render("public/compare.html", ctx)


@page_router.get("/categories/{slug}")
async def category_page(request: Request, slug: str, user: _OptionalUser = None):
    """Category page."""
    ctx = _get_template_context(request, user=user, title="カテゴリ別AIツールランキング", slug=slug, active_page="categories")
    return _render("public/category.html", ctx)


@page_router.get("/company")
async def company_page():
    """Redirect to corporate site company page."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("https://aixis.jp/company", status_code=301)


@page_router.get("/terms")
async def terms_page(request: Request, user: _OptionalUser = None):
    """Redirect to aixis.jp/terms (legal pages consolidated there)."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("https://aixis.jp/terms", status_code=301)


@page_router.get("/tokushoho")
async def tokushoho_page(request: Request, user: _OptionalUser = None):
    """Redirect to aixis.jp/tokushoho (legal pages consolidated there)."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("https://aixis.jp/tokushoho", status_code=301)


@page_router.get("/accessibility")
async def accessibility_page(request: Request, user: _OptionalUser = None):
    """Redirect to aixis.jp/accessibility (legal/policy pages consolidated there)."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("https://aixis.jp/accessibility", status_code=301)


@page_router.get("/pricing")
async def pricing_page(request: Request, user: _OptionalUser = None):
    """Pricing plans page."""
    ctx = _get_template_context(request, user=user, title="料金プラン | Aixis AI監査サービス", active_page="pricing")
    return _render("public/pricing.html", ctx)


@page_router.get("/audit-process")
async def audit_process_page(request: Request, user: _OptionalUser = None):
    """Audit process explanation page."""
    ctx = _get_template_context(request, user=user, title="Aixis監査プロセス | AI評価方法の詳細", active_page="audit-process")
    return _render("public/audit_process.html", ctx)


@page_router.get("/independence")
async def independence_page(request: Request, user: _OptionalUser = None):
    """Independence declaration page."""
    ctx = _get_template_context(request, user=user, title="Aixis独立性宣言 | ベンダー非依存の評価体制", active_page="about")
    return _render("public/independence.html", ctx)


@page_router.get("/transparency")
async def transparency_page(request: Request, user: _OptionalUser = None):
    """Transparency policy page."""
    ctx = _get_template_context(request, user=user, title="透明性ポリシー | 評価基準と利益相反の開示", active_page="transparency")
    return _render("public/transparency.html", ctx)


@page_router.get("/audit-protocol")
async def audit_protocol_page(request: Request, user: _OptionalUser = None):
    """Detailed audit protocol page."""
    ctx = _get_template_context(request, user=user, title="Aixis監査プロトコル | 5軸評価フレームワーク詳細", active_page="audit-protocol")
    return _render("public/audit_protocol.html", ctx)


@page_router.get("/faq")
async def faq_page(request: Request, user: _OptionalUser = None):
    """FAQ page with FAQPage structured data for rich results."""
    ctx = _get_template_context(request, user=user, title="よくある質問（FAQ） | Aixis AI監査について", active_page="faq")
    return _render("public/faq.html", ctx)


@page_router.get("/contact")
async def contact_page(request: Request, user: _OptionalUser = None):
    """Contact form page."""
    ctx = _get_template_context(request, user=user, title="お問い合わせ | Aixis AI監査のご相談・トライアル申請", active_page="contact")
    return _render("public/contact.html", ctx)


@page_router.get("/login")
async def login_page(request: Request, user: _OptionalUser = None):
    """Login page. Redirects to appropriate page if already logged in."""
    if user:
        if user.role in _DASHBOARD_ROLES:
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/tools", status_code=302)
    ctx = _get_template_context(request, title="ログイン", active_page="login")
    return _render("public/login.html", ctx)


@page_router.get("/forgot-password")
async def forgot_password_page(request: Request, user: _OptionalUser = None):
    """Forgot password page."""
    if user:
        if user.role in _DASHBOARD_ROLES:
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/tools", status_code=302)
    ctx = _get_template_context(request, title="パスワード再設定", active_page="forgot-password")
    return _render("public/forgot-password.html", ctx)


@page_router.get("/reset-password")
async def reset_password_page(request: Request, user: _OptionalUser = None):
    """Password reset page (accessed via email link with token)."""
    if user:
        if user.role in _DASHBOARD_ROLES:
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/tools", status_code=302)
    ctx = _get_template_context(request, title="パスワード再設定", active_page="reset-password")
    return _render("public/reset-password.html", ctx)


@page_router.get("/invite/{token}")
async def invite_page(request: Request, token: str):
    """Invite password-setup page (public)."""
    from .db.base import get_db as _get_db
    from .services.client_service import validate_invite_token

    # Validate token to show appropriate page
    async for db in _get_db():
        user = await validate_invite_token(db, token)
        break

    if not user:
        ctx = _get_template_context(
            request,
            title="招待リンクが無効です",
            invite_valid=False,
            invite_user_name="",
            invite_token=token,
        )
    else:
        ctx = _get_template_context(
            request,
            title="パスワード設定",
            invite_valid=True,
            invite_user_name=user.name,
            invite_token=token,
        )
    return _render("public/invite.html", ctx)


# ──────────── Auth-Protected Pages ────────────

# Dashboard role whitelist — only these roles can access /dashboard/* pages
_DASHBOARD_ROLES = frozenset({"admin", "analyst", "auditor"})


def _check_dashboard_access(user: User | None) -> RedirectResponse | None:
    """Return a redirect if the user lacks dashboard access, else None."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in _DASHBOARD_ROLES:
        return RedirectResponse(url="/", status_code=302)
    return None


@page_router.get("/dashboard")
async def dashboard_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin dashboard (requires auth + dashboard role)."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="管理ダッシュボード", active_page="dashboard")
    return _render("dashboard/index.html", ctx)


@page_router.get("/dashboard/tools")
async def tools_management_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Tool management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="ツール管理", active_page="tools")
    return _render("dashboard/tools.html", ctx)


@page_router.get("/dashboard/categories")
async def categories_management_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Category management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="カテゴリ管理", active_page="categories-admin")
    return _render("dashboard/categories.html", ctx)


@page_router.get("/dashboard/manual")
async def manual_list_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Manual evaluation list page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="手動評価一覧", active_page="manual")
    return _render("dashboard/manual_list.html", ctx)


@page_router.get("/dashboard/settings")
async def settings_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Platform settings page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="設定", active_page="settings")
    return _render("dashboard/settings.html", ctx)


@page_router.get("/dashboard/clients")
async def clients_management_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Client management page (admin only)."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="クライアント管理", active_page="clients")
    return _render("dashboard/clients.html", ctx)


@page_router.get("/dashboard/audits/new")
async def new_audit_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """New audit creation page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="新規監査を開始", active_page="audit-new")
    return _render("dashboard/audit_new.html", ctx)


@page_router.get("/dashboard/audits/{session_id}")
async def audit_detail_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit session detail page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="監査セッション詳細", session_id=session_id, active_page="audit-new")
    return _render("dashboard/audit_detail.html", ctx)


@page_router.get("/dashboard/audits/{session_id}/manual")
async def manual_checklist_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Manual checklist evaluation page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    # Resolve profile from session to serve category-specific checklist
    profile_id = ""
    try:
        from sqlalchemy import text as sa_text
        row = await db.execute(
            sa_text(
                "SELECT s.profile_id, tc.slug "
                "FROM audit_sessions s "
                "LEFT JOIN tools t ON s.tool_id = t.id "
                "LEFT JOIN tool_categories tc ON t.category_id = tc.id "
                "WHERE s.id = :sid"
            ),
            {"sid": session_id},
        )
        r = row.fetchone()
        if r:
            profile_id = r[0] or ""
            if not profile_id and r[1]:
                _slug_map = {"meeting-minutes-ai": "meeting_minutes", "slide-creation-ai": "slide_creation"}
                profile_id = _slug_map.get(r[1], "")
    except Exception:
        pass
    ctx = _get_template_context(
        request, user=user, title="手動チェックリスト評価",
        session_id=session_id, active_page="manual",
        profile_id=profile_id,
    )
    return _render("dashboard/manual_checklist.html", ctx)


@page_router.get("/dashboard/comparison")
async def comparison_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Tool score comparison page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="スコア比較", active_page="comparison")
    return _render("dashboard/comparison.html", ctx)


@page_router.get("/dashboard/custom-tests")
async def custom_tests_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Custom test case management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="カスタムテスト管理", active_page="custom-tests")
    return _render("dashboard/custom_tests.html", ctx)


@page_router.get("/dashboard/api-keys")
async def api_keys_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """API key management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="APIキー管理", active_page="api-keys")
    return _render("dashboard/api_keys.html", ctx)


@page_router.get("/dashboard/audits/{session_id}/log")
async def audit_log_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit log detail page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="監査ログ詳細", session_id=session_id, active_page="audit-new")
    return _render("dashboard/audit_log.html", ctx)


@page_router.get("/dashboard/webhooks")
async def webhooks_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Webhook management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="Webhook管理", active_page="webhooks")
    return _render("dashboard/webhooks.html", ctx)


@page_router.get("/dashboard/notifications")
async def notifications_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Notification center page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="通知センター", active_page="notifications")
    return _render("dashboard/notifications.html", ctx)


@page_router.get("/portal")
async def portal_redirect(user: _OptionalUser = None):
    """Legacy portal URL — redirect to /tools (clients) or /dashboard (admin)."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role in _DASHBOARD_ROLES:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/tools", status_code=302)


@page_router.get("/mypage")
async def mypage(
    request: Request,
    user: _OptionalUser = None,
):
    """My Page — account info, subscription status, password change, logout."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    ctx = _get_template_context(request, user=user, title="マイページ", active_page="mypage")
    return _render("public/mypage.html", ctx)


# ──────────── Scheduled Re-audits ────────────


@page_router.get("/dashboard/schedules")
async def schedules_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit schedule management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="スケジュール管理", active_page="schedules")
    return _render("dashboard/schedules.html", ctx)


# ──────────── Vendor Portal ────────────


@page_router.get("/vendor/guide")
async def vendor_guide_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Public vendor guide — tool listing request info."""
    ctx = _get_template_context(request, user=user, title="ベンダーの皆様へ", active_page="vendor")
    return _render("vendor/landing.html", ctx)


@page_router.get("/vendor")
async def vendor_dashboard_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Vendor self-service dashboard (requires auth)."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    ctx = _get_template_context(request, user=user, title="ベンダーポータル", active_page="vendor")
    return _render("vendor/dashboard.html", ctx)


@page_router.get("/vendor/submit")
async def vendor_submit_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Vendor tool submission form."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    ctx = _get_template_context(request, user=user, title="ツール申請", active_page="vendor")
    return _render("vendor/submit_tool.html", ctx)


@page_router.get("/dashboard/submissions")
async def admin_submissions_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin submission review queue."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="申請審査", active_page="submissions")
    return _render("dashboard/submissions.html", ctx)


# ──────────── Benchmarks & Leaderboard ────────────


@page_router.get("/benchmarks/{slug}/leaderboard")
async def leaderboard_page(
    request: Request,
    slug: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Public benchmark leaderboard page."""
    ctx = _get_template_context(request, user=user, title="リーダーボード", slug=slug, active_page="benchmarks")
    return _render("public/leaderboard.html", ctx)


@page_router.get("/dashboard/benchmarks")
async def benchmark_manage_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin benchmark management page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="ベンチマーク管理", active_page="benchmarks")
    return _render("dashboard/benchmark_manage.html", ctx)


# ---------------------------------------------------------------------------
# SEO: sitemap.xml & robots.txt
# ---------------------------------------------------------------------------

SITE_ORIGIN = settings.site_origin

# Public static pages to include in sitemap (path, changefreq, priority)
_STATIC_PAGES = [
    ("/", "weekly", "1.0"),
    ("/tools", "daily", "0.9"),
    ("/categories", "weekly", "0.8"),
    ("/compare", "weekly", "0.7"),
    ("/pricing", "monthly", "0.5"),
    ("/audit-process", "monthly", "0.5"),
    ("/independence", "monthly", "0.4"),
    ("/transparency", "monthly", "0.4"),
    ("/audit-protocol", "monthly", "0.4"),
    ("/faq", "monthly", "0.5"),
    ("/contact", "monthly", "0.3"),
    ("/company", "monthly", "0.4"),
    # terms and tokushoho now redirect to aixis.jp (excluded from sitemap)
]


_sitemap_cache: dict = {"xml": None, "ts": 0}
_SITEMAP_TTL = 3600  # 1 hour


@page_router.get("/sitemap.xml")
async def sitemap_xml(db: AsyncSession = Depends(get_db)):
    """Dynamic sitemap.xml for search engine crawlers."""
    now = time.time()
    if _sitemap_cache["xml"] and (now - _sitemap_cache["ts"]) < _SITEMAP_TTL:
        return Response(content=_sitemap_cache["xml"], media_type="application/xml")

    from .db.models.tool import Tool, ToolCategory

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">',
    ]

    # Static pages — all reference the brand OGP so Google Image Search indexes it
    _PLATFORM_OGP = f"{SITE_ORIGIN}/static/img/og/aixis-platform-ogp.png"
    _PLATFORM_OGP_TITLE = (
        "Aixis AI監査プラットフォーム — 独立した5軸でAIを監査する"
    )
    _PLATFORM_OGP_CAPTION = (
        "AIツールを独立した第三者の立場から5軸（実務適性・費用対効果・"
        "日本語能力・信頼性・安全性・革新性）で監査・評価する独立系AI監査プラットフォーム"
    )
    for path, freq, prio in _STATIC_PAGES:
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}{path}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{prio}</priority>"
            f"<image:image>"
            f"<image:loc>{_PLATFORM_OGP}</image:loc>"
            f"<image:title>{html_escape(_PLATFORM_OGP_TITLE)}</image:title>"
            f"<image:caption>{html_escape(_PLATFORM_OGP_CAPTION)}</image:caption>"
            f"</image:image>"
            f"</url>"
        )

    # Tool detail pages (only public + active) with image entries
    result = await db.execute(
        select(Tool.slug, Tool.updated_at, Tool.name_jp, Tool.name, Tool.vendor).where(
            Tool.is_active == True, Tool.is_public == True  # noqa: E712
        )
    )
    for t_slug, updated_at, name_jp, name, vendor in result.all():
        lastmod = ""
        if updated_at:
            lastmod = f"<lastmod>{updated_at.strftime('%Y-%m-%d')}</lastmod>"
        t_name = html_escape(name_jp or name or t_slug)
        t_vendor = html_escape(vendor or "")
        caption = html_escape(f"{t_name} - Aixis AI監査スコアカード")
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}/tools/{t_slug}</loc>"
            f"{lastmod}<changefreq>weekly</changefreq>"
            f"<priority>0.8</priority>"
            f"<image:image>"
            f"<image:loc>{SITE_ORIGIN}/card/{t_slug}.png</image:loc>"
            f"<image:title>{t_name} AI監査スコア - Aixis</image:title>"
            f"<image:caption>{caption}</image:caption>"
            f"</image:image>"
            f"</url>"
        )

    # Category pages
    cat_result = await db.execute(
        select(ToolCategory.slug, ToolCategory.name_jp, ToolCategory.name)
    )
    for (cat_slug, cat_name_jp, cat_name) in cat_result.all():
        cat_display = html_escape(cat_name_jp or cat_name or cat_slug)
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}/categories/{cat_slug}</loc>"
            f"<changefreq>weekly</changefreq>"
            f"<priority>0.6</priority>"
            f"<image:image>"
            f"<image:loc>{_PLATFORM_OGP}</image:loc>"
            f"<image:title>{cat_display} - Aixis AI監査ランキング</image:title>"
            f"</image:image>"
            f"</url>"
        )

    # Benchmark leaderboard pages
    from .db.models.benchmark import BenchmarkSuite
    bench_result = await db.execute(select(BenchmarkSuite.slug))
    for (bench_slug,) in bench_result.all():
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}/benchmarks/{bench_slug}/leaderboard</loc>"
            f"<changefreq>weekly</changefreq>"
            f"<priority>0.5</priority></url>"
        )

    lines.append("</urlset>")
    xml = "\n".join(lines)
    _sitemap_cache["xml"] = xml
    _sitemap_cache["ts"] = now
    return Response(content=xml, media_type="application/xml")


@page_router.get("/manifest.json")
async def manifest_json():
    """Web App Manifest for PWA and browser integration."""
    import json
    from pathlib import Path
    manifest_path = Path(__file__).parent / "static" / "manifest.json"
    return Response(
        content=manifest_path.read_text(),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@page_router.get("/offline")
async def offline_page(request: Request):
    """Offline fallback page for PWA/service worker."""
    return templates.TemplateResponse(
        "public/offline.html",
        {"request": request, "csp_nonce": getattr(request.state, "csp_nonce", "")},
    )


@page_router.get("/.well-known/security.txt")
async def security_txt():
    """RFC 9116 security.txt for vulnerability disclosure."""
    return PlainTextResponse(
        "Contact: mailto:security@aixis.jp\n"
        "Preferred-Languages: ja, en\n"
        "Canonical: https://platform.aixis.jp/.well-known/security.txt\n"
        "Expires: 2027-04-01T00:00:00Z\n",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@page_router.get("/robots.txt")
async def robots_txt():
    """robots.txt for search engine crawlers."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /dashboard/\n"
        "Disallow: /api/\n"
        "Disallow: /api/v1/debug/\n"
        "Disallow: /login\n"
        "Disallow: /forgot-password\n"
        "Disallow: /reset-password\n"
        "Disallow: /invite/\n"
        "Disallow: /portal\n"
        "Disallow: /mypage\n"
        "Disallow: /platform/\n"
        "\n"
        f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n"
    )
    return PlainTextResponse(content=content)


@page_router.get("/{key}.txt")
async def indexnow_key_file(key: str):
    """Serve IndexNow key verification file (any slug used as key returns itself)."""
    import re
    if not re.match(r'^[a-z0-9_-]+$', key, re.IGNORECASE) or len(key) > 128:
        from fastapi import HTTPException
        raise HTTPException(404)
    return PlainTextResponse(content=key)


# ── Shared OGP card constants ────────────────────────────────────────
# Dark neon theme to match Aixis brand (pentagon motif + teal glow)
_OG_W, _OG_H = 1200, 630
_OG_SS = 2  # supersample factor for smooth edges

# Dark gradient background
_BG_TOP = (8, 14, 28)       # #080e1c
_BG_BOT = (20, 28, 46)      # #141c2e

# Neon accent (teal — matches Aixis logo)
_TEAL = (94, 234, 212)      # #5eead4
_TEAL_SOFT = (45, 158, 140) # #2d9e8c

# Foreground text
_FG = (248, 250, 252)       # #f8fafc  (near white)
_FG_DIM = (148, 163, 184)   # #94a3b8  (slate-400)
_FG_FAINT = (71, 85, 105)   # #475569  (slate-600)
_PANEL = (22, 31, 51)       # #161f33  (subtle panel bg)

# Grade badge 3-stop gradients — EXACT match to .grade-S/A/B/C/D in style.css
# (linear-gradient 135deg start → middle → end)
_GRADE_GRADIENT = {
    "S": [(230, 209, 139), (221, 198, 125), (230, 209, 139)],  # #E6D18B/#DDC67D
    "A": [(156, 195, 215), (139, 178, 202), (123, 162, 189)],  # #9CC3D7/#8BB2CA/#7BA2BD
    "B": [(170, 198, 185), (157, 185, 173), (145, 173, 160)],  # #AAC6B9/#9DB9AD/#91ADA0
    "C": [(198, 184, 173), (185, 171, 160), (173, 158, 147)],  # #C6B8AD/#B9ABA0/#AD9E93
    "D": [(198, 153, 153), (185, 141, 141), (173, 128, 128)],  # #C69999/#B98D8D/#AD8080
}
# Mid-tone for score number color
_GRADE_MID = {k: v[1] for k, v in _GRADE_GRADIENT.items()}


def _og_score_color(v: float) -> tuple[int, int, int]:
    """Accent color for the hero score number (matches site grade thresholds)."""
    if v >= 4.5:
        return _GRADE_MID["S"]
    if v >= 3.8:
        return _GRADE_MID["A"]
    if v >= 3.0:
        return _GRADE_MID["B"]
    if v >= 2.0:
        return _GRADE_MID["C"]
    return _GRADE_MID["D"]


def _og_build_background(w: int, h: int):
    """Build a vertical dark gradient background with a subtle radial glow.

    Returns a PIL RGBA image.
    """
    import numpy as np
    from PIL import Image, ImageFilter

    # Vertical gradient (#080e1c -> #141c2e)
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]  # (H, 1)
    top = np.array(_BG_TOP, dtype=np.float32)
    bot = np.array(_BG_BOT, dtype=np.float32)
    grad = top * (1.0 - t) + bot * t  # (H, 3)
    arr = np.tile(grad[:, None, :], (1, w, 1)).astype(np.uint8)
    bg = Image.fromarray(arr, "RGB").convert("RGBA")

    # Soft radial glow (teal) behind the center — gives the "neon" feel
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    from PIL import ImageDraw as _ID
    gd = _ID.Draw(glow)
    cx, cy = w // 2, int(h * 0.58)
    for rr, a in [(520, 22), (380, 28), (260, 32), (160, 34)]:
        gd.ellipse(
            [cx - rr, cy - rr, cx + rr, cy + rr],
            fill=(_TEAL[0], _TEAL[1], _TEAL[2], a),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=60))
    bg = Image.alpha_composite(bg, glow)
    return bg


def _og_fit_font(draw, text: str, font_path: str, max_w: int, sizes: list[int]):
    """Return (font, fitted_text) — largest font that fits, or ellipsized
    smallest."""
    from PIL import ImageFont
    for sz in sizes:
        try:
            f = ImageFont.truetype(font_path, sz)
        except (IOError, OSError):
            return ImageFont.load_default(), text
        if draw.textlength(text, font=f) <= max_w:
            return f, text
    # Still too wide at smallest — truncate char-by-char with ellipsis
    f = ImageFont.truetype(font_path, sizes[-1])
    ell = "…"
    ell_w = draw.textlength(ell, font=f)
    t = text
    while t and draw.textlength(t, font=f) + ell_w > max_w:
        t = t[:-1]
    return f, (t + ell) if t else ell


def _og_draw_pentagon_frame(draw, cx: int, cy: int, r: int):
    """Draw an empty pentagon outline (frame only, no fills)."""
    import math
    pts = []
    for i in range(5):
        ang = math.radians(-90 + i * 72)
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    # Outer soft glow (wider teal with low opacity)
    draw.line(pts + [pts[0]], fill=(_TEAL[0], _TEAL[1], _TEAL[2], 60), width=18)
    # Mid glow
    draw.line(pts + [pts[0]], fill=(_TEAL[0], _TEAL[1], _TEAL[2], 110), width=8)
    # Bright core outline
    draw.line(pts + [pts[0]], fill=(_TEAL[0], _TEAL[1], _TEAL[2], 220), width=3)
    # Vertex dots
    for (x, y) in pts:
        d = 6
        draw.ellipse([x - d, y - d, x + d, y + d], fill=_TEAL + (255,))
    return pts


def _og_make_grade_badge(size: int, grade: str):
    """Build a rounded-square grade badge as an RGBA image.

    Matches the site's .grade-badge-lg CSS exactly:
    - 3-stop linear gradient (135deg)
    - White letter with drop shadow
    - Subtle inner highlight
    - Soft outer shadow
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    stops = _GRADE_GRADIENT.get(grade, [(176, 190, 197)] * 3)
    N = size

    # 135deg gradient: top-left → bottom-right (normalized position)
    x = np.linspace(0.0, 1.0, N, dtype=np.float32)
    y = np.linspace(0.0, 1.0, N, dtype=np.float32)
    X, Y = np.meshgrid(x, y)
    pos = (X + Y) / 2.0  # 0 at TL, 1 at BR

    c1 = np.array(stops[0], dtype=np.float32)
    c2 = np.array(stops[1], dtype=np.float32)
    c3 = np.array(stops[2], dtype=np.float32)

    # 3-stop interpolation: 0→0.5 uses c1→c2, 0.5→1.0 uses c2→c3
    t_low = np.clip(pos * 2.0, 0.0, 1.0)[..., None]
    t_high = np.clip((pos - 0.5) * 2.0, 0.0, 1.0)[..., None]
    low = c1 + (c2 - c1) * t_low
    high = c2 + (c3 - c2) * t_high
    mask = (pos < 0.5)[..., None]
    rgb = np.where(mask, low, high).astype(np.uint8)

    alpha = np.full((N, N, 1), 255, dtype=np.uint8)
    rgba = np.concatenate([rgb, alpha], axis=-1)
    body = Image.fromarray(rgba, "RGBA")

    # Rounded corner mask (radius ~N/6 = matches 10px radius on 64px)
    corner_r = max(4, N // 6)
    mask_img = Image.new("L", (N, N), 0)
    md = ImageDraw.Draw(mask_img)
    md.rounded_rectangle([0, 0, N - 1, N - 1], radius=corner_r, fill=255)
    body.putalpha(mask_img)

    # Add subtle inner highlight (top edge)
    hl = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.rounded_rectangle(
        [2, 2, N - 3, N // 2],
        radius=corner_r - 2,
        fill=(255, 255, 255, 30),
    )
    body = Image.alpha_composite(body, hl)

    # Add thin dark border
    border = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle(
        [1, 1, N - 2, N - 2],
        radius=corner_r - 1, outline=(0, 0, 0, 28), width=2,
    )
    body = Image.alpha_composite(body, border)

    return body


def _og_paste_grade_badge(img, x: int, y: int, size: int, grade: str, font):
    """Paste a grade badge with drop shadow and white letter onto img."""
    from PIL import Image, ImageDraw, ImageFilter

    badge = _og_make_grade_badge(size, grade)

    # Drop shadow (soft blur beneath the badge)
    shadow_pad = max(12, size // 6)
    shadow = Image.new("RGBA", (size + shadow_pad * 2, size + shadow_pad * 2), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    corner_r = max(4, size // 6)
    sd.rounded_rectangle(
        [shadow_pad, shadow_pad + shadow_pad // 2,
         shadow_pad + size, shadow_pad + size + shadow_pad // 2],
        radius=corner_r, fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_pad // 2))
    img.paste(shadow, (x - shadow_pad, y - shadow_pad), shadow)
    img.paste(badge, (x, y), badge)

    # White letter + drop shadow (matches CSS text-shadow)
    draw = ImageDraw.Draw(img, "RGBA")
    bb = draw.textbbox((0, 0), grade, font=font)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    # Nudge letter up slightly for optical balance (text hangs below baseline)
    nudge = int(size * 0.02)
    tx = x + (size - bw) // 2 - bb[0]
    ty = y + (size - bh) // 2 - bb[1] - nudge
    # Text shadow (2 layers for softness)
    draw.text((tx + 2, ty + 4), grade, fill=(0, 0, 0, 110), font=font)
    draw.text((tx + 1, ty + 2), grade, fill=(0, 0, 0, 60), font=font)
    # Main letter
    draw.text((tx, ty), grade, fill=(255, 255, 255, 255), font=font)
    return draw


async def _og_fetch_logo(logo_url: str, target_size: int, logger_):
    """Fetch a tool logo and return a resized RGBA PIL image (or None)."""
    if not logo_url:
        return None
    try:
        import httpx
        import io as _io
        from PIL import Image
        # Upgrade Google favicon size
        if "google.com/s2/favicons" in logo_url and "sz=" in logo_url:
            logo_url = logo_url.rsplit("sz=", 1)[0] + "sz=256"
        async with httpx.AsyncClient(
            timeout=6.0,
            follow_redirects=True,
            headers={"User-Agent": "Aixis-CardGen/2.0"},
        ) as client:
            resp = await client.get(logo_url)
            if resp.status_code != 200 or len(resp.content) < 100:
                return None
            logo = Image.open(_io.BytesIO(resp.content)).convert("RGBA")
            # Resize with aspect preserve, then center in a square
            logo.thumbnail((target_size, target_size), Image.LANCZOS)
            sq = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
            ox = (target_size - logo.width) // 2
            oy = (target_size - logo.height) // 2
            sq.paste(logo, (ox, oy), logo)
            return sq
    except Exception as e:
        if logger_:
            logger_.warning("Logo fetch failed: %s", e)
        return None


@page_router.get("/card/{slug}.png")
async def card_image(slug: str, db: AsyncSession = Depends(get_db)):
    """Dynamic OGP card for a tool (1200×630, dark neon theme).

    Design principles:
    - Pentagon frame (no fills) with the 5 axis LABELS only (no per-axis
      scores — those are login-gated).
    - Central total score fits inside the pentagon's inscribed circle.
    - Header row: logo + name/vendor + grade badge. No divider lines that
      could collide with pentagon labels.
    - Grade badge matches site CSS exactly (3-stop gradient + white letter
      + drop shadow + inner highlight).
    - Supersampled at 2× and downscaled with LANCZOS for smooth edges.
    """
    from .db.models.tool import Tool
    from sqlalchemy.orm import selectinload
    import math

    result = await db.execute(
        select(Tool).where(Tool.slug == slug, Tool.is_public == True)  # noqa: E712
        .options(selectinload(Tool.scores), selectinload(Tool.category))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        return Response(status_code=404)

    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io
    except ImportError:
        _page_logger.error("Pillow not installed — card image generation unavailable")
        return Response(status_code=503)

    latest_score = tool.scores[0] if tool.scores else None
    category_name = tool.category.name_jp if tool.category else ""
    tool_name = (tool.name_jp or tool.name or slug).strip()

    try:
        S = _OG_SS  # supersample
        W, H = _OG_W * S, _OG_H * S

        # Build background (teal radial glow on dark gradient)
        bg = _og_build_background(W, H)
        img = bg.copy()
        draw = ImageDraw.Draw(img, "RGBA")

        # ── Fonts (scaled by S) ──
        _FD = Path(__file__).resolve().parent / "static" / "fonts"
        _FB = str(_FD / "NotoSansJP-Bold.ttf")
        _FM = str(_FD / "NotoSansJP-Medium.ttf")

        def _tt(path: str, sz: int):
            try:
                return ImageFont.truetype(path, sz * S)
            except (IOError, OSError):
                return ImageFont.load_default()

        fc = _tt(_FM, 15)          # category pill
        fg_letter = _tt(_FB, 50)   # grade badge letter
        fs = _tt(_FB, 100)         # hero score — sized to fit inside pentagon
        fu = _tt(_FM, 28)          # "/ 5.0"
        fl = _tt(_FM, 16)          # "総合スコア" label
        fax = _tt(_FM, 19)         # pentagon axis labels
        fbr = _tt(_FB, 16)         # footer brand "Aixis"
        fbs = _tt(_FM, 13)         # footer subtitle / url

        PL, PR = 60 * S, 60 * S

        # ── Layout zones (carefully allocated so labels never collide) ──
        # Header:      y=40..150  (height 110)
        # Pentagon:    labels need y=160..540, pentagon r=150
        # Footer:      y=560..600 (height 40)
        header_top = 40 * S
        footer_top = 560 * S

        # ── Header: logo + name + vendor + grade badge ──
        logo_size = 80 * S
        logo_x = PL
        logo_y = header_top
        logo_cy = logo_y + logo_size // 2

        # Grade badge (top right, same row as logo)
        badge_size = 80 * S
        badge_x = W - PR - badge_size
        badge_y = logo_cy - badge_size // 2

        # Load tool logo
        logo_img = await _og_fetch_logo(tool.logo_url, int(logo_size * 0.85), _page_logger)

        # Logo container (white rounded square with subtle shadow)
        from PIL import ImageFilter as _IF
        # Drop shadow for logo
        lshadow = Image.new("RGBA", (logo_size + 24 * S, logo_size + 24 * S), (0, 0, 0, 0))
        lsd = ImageDraw.Draw(lshadow)
        lsd.rounded_rectangle(
            [12 * S, 14 * S, 12 * S + logo_size, 14 * S + logo_size],
            radius=16 * S, fill=(0, 0, 0, 90),
        )
        lshadow = lshadow.filter(_IF.GaussianBlur(radius=8 * S))
        img.paste(lshadow, (logo_x - 12 * S, logo_y - 12 * S), lshadow)

        # Container body
        draw = ImageDraw.Draw(img, "RGBA")
        draw.rounded_rectangle(
            [logo_x, logo_y, logo_x + logo_size, logo_y + logo_size],
            radius=16 * S, fill=(255, 255, 255, 250),
            outline=(255, 255, 255, 80), width=1,
        )
        if logo_img:
            inset = (logo_size - logo_img.width) // 2
            img.paste(logo_img, (logo_x + inset, logo_y + inset), logo_img)
            draw = ImageDraw.Draw(img, "RGBA")
        else:
            # Initial letter fallback — styled like site's empty-state
            init_char = (tool_name[:1] or "?").upper()
            fi = _tt(_FB, 36)
            ib = draw.textbbox((0, 0), init_char, font=fi)
            iw, ih = ib[2] - ib[0], ib[3] - ib[1]
            draw.text(
                (logo_x + (logo_size - iw) // 2 - ib[0],
                 logo_y + (logo_size - ih) // 2 - ib[1]),
                init_char, fill=(71, 85, 105, 255), font=fi,
            )

        # Name text area bounds (between logo and badge)
        name_x = logo_x + logo_size + 22 * S
        name_max_w = badge_x - name_x - 24 * S

        # Auto-fit tool name
        name_font, name_display = _og_fit_font(
            draw, tool_name, _FB, name_max_w,
            [42 * S, 36 * S, 32 * S, 28 * S, 24 * S],
        )
        nb = draw.textbbox((0, 0), name_display, font=name_font)
        name_h = nb[3] - nb[1]

        # Vendor (auto-fit)
        has_vendor = bool(tool.vendor)
        vendor_display = ""
        vendor_h = 0
        vendor_font = None
        if has_vendor:
            vendor_font, vendor_display = _og_fit_font(
                draw, tool.vendor.strip(), _FM, name_max_w, [20 * S, 18 * S, 16 * S],
            )
            vb = draw.textbbox((0, 0), vendor_display, font=vendor_font)
            vendor_h = vb[3] - vb[1]

        # Category pill (small, above the tool name)
        has_category = bool(category_name)
        cat_h = 0
        if has_category:
            cb = draw.textbbox((0, 0), category_name, font=fc)
            cat_h = (cb[3] - cb[1]) + 14 * S  # pill internal padding

        gap_name_vendor = 8 * S if has_vendor else 0
        gap_cat_name = 10 * S if has_category else 0
        block_h = cat_h + gap_cat_name + name_h + gap_name_vendor + vendor_h
        block_top = logo_cy - block_h // 2

        # Draw category pill above name
        cursor_y = block_top
        if has_category:
            cb = draw.textbbox((0, 0), category_name, font=fc)
            ctw = cb[2] - cb[0]
            pill_h = cat_h
            pill_pad = 14 * S
            draw.rounded_rectangle(
                [name_x, cursor_y, name_x + ctw + pill_pad * 2, cursor_y + pill_h],
                radius=7 * S, fill=(255, 255, 255, 20),
                outline=(_TEAL[0], _TEAL[1], _TEAL[2], 80), width=1,
            )
            draw.text(
                (name_x + pill_pad,
                 cursor_y + (pill_h - (cb[3] - cb[1])) // 2 - cb[1]),
                category_name, fill=(_TEAL[0], _TEAL[1], _TEAL[2], 230), font=fc,
            )
            cursor_y += pill_h + gap_cat_name

        # Draw name
        draw.text((name_x, cursor_y - nb[1]), name_display, fill=_FG + (255,), font=name_font)
        cursor_y += name_h + gap_name_vendor

        # Draw vendor
        if has_vendor:
            draw.text(
                (name_x, cursor_y - vb[1]),
                vendor_display, fill=_FG_DIM + (255,), font=vendor_font,
            )

        # Grade badge (with gradient, white letter, shadow)
        if latest_score and getattr(latest_score, "overall_grade", None):
            _og_paste_grade_badge(
                img, badge_x, badge_y, badge_size,
                latest_score.overall_grade, fg_letter,
            )
            draw = ImageDraw.Draw(img, "RGBA")

        # ── Pentagon + centered total score (NO divider lines above/below) ──
        pent_cx = W // 2
        # Center pentagon in the area between header and footer
        main_top = max(logo_y + logo_size, block_top + block_h) + 24 * S
        main_bot = footer_top - 24 * S
        pent_cy = (main_top + main_bot) // 2
        pent_r = 150 * S  # smaller — ensures labels stay inside main zone

        _og_draw_pentagon_frame(draw, pent_cx, pent_cy, pent_r)

        # Axis labels (exact names from aixis_agent/core/enums.py)
        AXIS_NAMES = [
            "実務適性",        # top
            "費用対効果",      # top-right
            "日本語能力",      # bottom-right
            "信頼性・安全性",  # bottom-left
            "革新性",          # top-left
        ]
        label_r = pent_r + 30 * S
        # Label anchor offsets — how to place text relative to the computed
        # point (0=centered, -1=above/left, +1=below/right on that axis).
        label_offsets = [
            (0, -1),           # top: center, above vertex
            (0.85, -0.2),      # top-right: to the right, slightly above center
            (0.55, 1),         # bottom-right: right, below vertex
            (-0.55, 1),        # bottom-left: left, below vertex
            (-0.85, -0.2),     # top-left: to the left, slightly above center
        ]
        for i, name in enumerate(AXIS_NAMES):
            ang = math.radians(-90 + i * 72)
            lx = pent_cx + label_r * math.cos(ang)
            ly = pent_cy + label_r * math.sin(ang)
            lb = draw.textbbox((0, 0), name, font=fax)
            lw_, lh_ = lb[2] - lb[0], lb[3] - lb[1]
            ox, oy = label_offsets[i]
            tx = lx - lw_ / 2 + (ox * lw_ / 2) - lb[0]
            ty = ly - lh_ / 2 + (oy * lh_ / 2) - lb[1]
            draw.text((tx, ty), name, fill=_FG_DIM + (255,), font=fax)

        # "総合スコア" label (above the score, inside pentagon)
        label_text = "総合スコア"
        lb = draw.textbbox((0, 0), label_text, font=fl)
        lw_ = lb[2] - lb[0]
        draw.text(
            (pent_cx - lw_ // 2 - lb[0], pent_cy - 70 * S - lb[1]),
            label_text, fill=_FG_DIM + (255,), font=fl,
        )

        # Hero score number (sized to fit inside pentagon's inscribed circle)
        # Pentagon inscribed radius = pent_r * cos(36°) ≈ 0.809 * pent_r
        # Inscribed width ≈ 2 * 0.809 * pent_r ≈ 243 at 1× (pent_r=150)
        if latest_score and latest_score.overall_score is not None:
            overall = float(latest_score.overall_score)
            s_str = f"{overall:.1f}"
            sb = draw.textbbox((0, 0), s_str, font=fs)
            sw_, sh_ = sb[2] - sb[0], sb[3] - sb[1]
            ub = draw.textbbox((0, 0), "/ 5.0", font=fu)
            uw_, uh_ = ub[2] - ub[0], ub[3] - ub[1]
            gap_su = 10 * S
            total_w = sw_ + gap_su + uw_
            sx = pent_cx - total_w // 2 - sb[0]
            # Vertical center, with slight optical compensation
            sy = pent_cy - sh_ // 2 - sb[1] + 8 * S
            draw.text((sx, sy), s_str, fill=_og_score_color(overall) + (255,), font=fs)
            ux = sx + sw_ + gap_su + (sb[0] - ub[0])
            # Align "/ 5.0" baseline with bottom of score
            uy = sy + (sh_ - uh_) + (sb[1] - ub[1])
            draw.text((ux, uy), "/ 5.0", fill=_FG_DIM + (255,), font=fu)
        else:
            placeholder = "—"
            pb = draw.textbbox((0, 0), placeholder, font=fs)
            pw_, ph_ = pb[2] - pb[0], pb[3] - pb[1]
            draw.text(
                (pent_cx - pw_ // 2 - pb[0], pent_cy - ph_ // 2 - pb[1] + 8 * S),
                placeholder, fill=_FG_DIM + (255,), font=fs,
            )

        # ── Footer (no divider line — keeps pentagon labels uncluttered) ──
        fy = footer_top + 20 * S
        draw.text((PL, fy), "Aixis", fill=_TEAL + (255,), font=fbr)
        brand_bb = draw.textbbox((0, 0), "Aixis", font=fbr)
        brand_w = brand_bb[2] - brand_bb[0]
        draw.text(
            (PL + brand_w + 10 * S, fy + 5 * S),
            "独立AI監査プラットフォーム",
            fill=_FG_DIM + (255,), font=fbs,
        )
        url = "platform.aixis.jp"
        urb = draw.textbbox((0, 0), url, font=fbs)
        draw.text(
            (W - PR - (urb[2] - urb[0]), fy + 5 * S),
            url, fill=_FG_DIM + (255,), font=fbs,
        )

        # Downscale to target dimensions for smooth edges
        final = img.convert("RGB").resize((_OG_W, _OG_H), Image.LANCZOS)

        buf = _io.BytesIO()
        final.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",
                "Content-Type": "image/png",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception:
        _page_logger.exception("Failed to generate card image for %s", slug)
        return Response(status_code=500)


@page_router.get("/og/{slug}.svg")
async def og_image(slug: str, db: AsyncSession = Depends(get_db)):
    """Dynamic OGP image (SVG) for each tool — used in og:image meta tags."""
    from .db.models.tool import Tool
    from .db.models.score import ToolPublishedScore

    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        return Response(status_code=404)

    # Sanitize for SVG/XML context: escape XML special chars AND strip any
    # SVG injection vectors (event handlers, scripts, CDATA).
    import re as _re
    def _svg_safe(text: str, max_len: int = 40) -> str:
        """Escape text for safe inclusion in SVG <text> elements."""
        safe = html_escape(text or "")
        # Strip any remaining XML/SVG injection patterns
        safe = _re.sub(r'<[^>]*>', '', safe)
        safe = _re.sub(r'on\w+\s*=', '', safe, flags=_re.IGNORECASE)
        if len(safe) > max_len:
            safe = safe[:max_len - 1] + "…"
        return safe

    tool_name = _svg_safe(tool.name_jp or tool.name, max_len=20)
    vendor = _svg_safe(tool.vendor or "", max_len=30)

    # Get overall score if available
    score_result = await db.execute(
        select(ToolPublishedScore.score).where(
            ToolPublishedScore.tool_id == tool.id,
            ToolPublishedScore.axis_key == "overall",
        )
    )
    score_row = score_result.first()
    # Score is numeric — format safely (no user input)
    overall_score = f"{float(score_row[0]):.1f}" if score_row and score_row[0] is not None else "—"

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#1e293b"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <rect x="40" y="40" width="1120" height="550" rx="16" fill="none" stroke="#334155" stroke-width="1"/>
  <text x="100" y="120" font-family="Noto Serif JP, serif" font-size="22" fill="#94a3b8" font-weight="600">Aixis AI Audit Platform</text>
  <text x="100" y="280" font-family="Noto Serif JP, serif" font-size="72" fill="#f8fafc" font-weight="800">{tool_name}</text>
  <text x="100" y="340" font-family="Noto Serif JP, serif" font-size="28" fill="#94a3b8">{vendor}</text>
  <text x="100" y="520" font-family="Noto Serif JP, serif" font-size="20" fill="#64748b">独立監査スコア（5軸評価）</text>
  <circle cx="1000" cy="300" r="100" fill="none" stroke="#6366f1" stroke-width="6"/>
  <text x="1000" y="290" font-family="Noto Serif JP, serif" font-size="64" fill="#f8fafc" font-weight="800" text-anchor="middle">{overall_score}</text>
  <text x="1000" y="330" font-family="Noto Serif JP, serif" font-size="18" fill="#94a3b8" text-anchor="middle">/ 5.0</text>
  <text x="1000" y="520" font-family="Noto Serif JP, serif" font-size="18" fill="#475569" text-anchor="middle">platform.aixis.jp</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={
        "Cache-Control": "public, max-age=86400",
    })
