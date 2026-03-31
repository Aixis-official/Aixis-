"""SSR page routes using Jinja2 templates."""
import logging
import time
from datetime import datetime, timezone
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


def _render(name: str, ctx: dict):
    """Render template compatible with both old and new Starlette APIs."""
    request = ctx.pop("request")
    try:
        # Starlette 0.46+: TemplateResponse(request, name, context)
        return templates.TemplateResponse(request, name, ctx)
    except TypeError:
        # Fallback for older Starlette
        ctx["request"] = request
        return templates.TemplateResponse(name, ctx)

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
        last_updated = last_updated_dt.strftime("%Y.%m.%d") if last_updated_dt else "—"

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
    ctx = _get_template_context(request, user=user, title="AIツール比較・一覧 | 独立監査で選ぶ", active_page="home", stats=stats)
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
    ctx = _get_template_context(request, user=user, title="AIツール比較・一覧 | 独立監査で選ぶ", active_page="home", stats=stats)
    return _render("public/landing.html", ctx)


@page_router.get("/tools")
async def tools_page(request: Request, user: _OptionalUser = None):
    """Tool catalog page."""
    ctx = _get_template_context(request, user=user, title="AIツール一覧・比較データベース | カテゴリ別評価", active_page="tools")
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
        .where(Tool.slug == slug)
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
    return _render("public/tool_detail.html", ctx)


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


@page_router.get("/terms")
async def terms_page(request: Request, user: _OptionalUser = None):
    """Terms of service page."""
    ctx = _get_template_context(request, user=user, title="利用規約 | サービス利用条件", active_page="terms")
    return _render("public/terms.html", ctx)


@page_router.get("/tokushoho")
async def tokushoho_page(request: Request, user: _OptionalUser = None):
    """特定商取引法に基づく表記 page."""
    ctx = _get_template_context(request, user=user, title="特定商取引法に基づく表記", active_page="tokushoho")
    return _render("public/tokushoho.html", ctx)


@page_router.get("/pricing")
async def pricing_page(request: Request, user: _OptionalUser = None):
    """Pricing plans page."""
    ctx = _get_template_context(request, user=user, title="料金プラン | AI監査プラットフォーム", active_page="pricing")
    return _render("public/pricing.html", ctx)


@page_router.get("/audit-process")
async def audit_process_page(request: Request, user: _OptionalUser = None):
    """Audit process explanation page."""
    ctx = _get_template_context(request, user=user, title="AI監査プロセス | 評価方法の詳細", active_page="audit-process")
    return _render("public/audit_process.html", ctx)


@page_router.get("/independence")
async def independence_page(request: Request, user: _OptionalUser = None):
    """Independence declaration page."""
    ctx = _get_template_context(request, user=user, title="独立性宣言 | ベンダー非依存の評価体制", active_page="about")
    return _render("public/independence.html", ctx)


@page_router.get("/transparency")
async def transparency_page(request: Request, user: _OptionalUser = None):
    """Transparency policy page."""
    ctx = _get_template_context(request, user=user, title="透明性ポリシー | 評価基準と利益相反の開示", active_page="transparency")
    return _render("public/transparency.html", ctx)


@page_router.get("/audit-protocol")
async def audit_protocol_page(request: Request, user: _OptionalUser = None):
    """Detailed audit protocol page."""
    ctx = _get_template_context(request, user=user, title="監査プロトコル | 5軸評価フレームワーク詳細", active_page="audit-protocol")
    return _render("public/audit_protocol.html", ctx)


@page_router.get("/faq")
async def faq_page(request: Request, user: _OptionalUser = None):
    """FAQ page with FAQPage structured data for rich results."""
    ctx = _get_template_context(request, user=user, title="よくある質問（FAQ） | AIツール監査について", active_page="faq")
    return _render("public/faq.html", ctx)


@page_router.get("/contact")
async def contact_page(request: Request, user: _OptionalUser = None):
    """Contact form page."""
    ctx = _get_template_context(request, user=user, title="お問い合わせ | AI監査のご相談・トライアル申請", active_page="contact")
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
):
    """Manual checklist evaluation page."""
    if redirect := _check_dashboard_access(user):
        return redirect
    ctx = _get_template_context(request, user=user, title="手動チェックリスト評価", session_id=session_id, active_page="manual")
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

SITE_ORIGIN = "https://platform.aixis.jp"

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
    ("/terms", "yearly", "0.2"),
    ("/tokushoho", "yearly", "0.2"),
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

    # Static pages
    for path, freq, prio in _STATIC_PAGES:
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}{path}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{prio}</priority></url>"
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
    cat_result = await db.execute(select(ToolCategory.slug))
    for (cat_slug,) in cat_result.all():
        lines.append(
            f"  <url><loc>{SITE_ORIGIN}/categories/{cat_slug}</loc>"
            f"<changefreq>weekly</changefreq>"
            f"<priority>0.6</priority></url>"
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


@page_router.get("/card/{slug}.png")
async def card_image(slug: str, db: AsyncSession = Depends(get_db)):
    """Generate a PNG card image for Google Images indexing.

    Produces a clean card-like image showing tool name, grade, score, category,
    and 5-axis score bars — designed for image search discoverability.
    """
    from .db.models.tool import Tool, ToolCategory
    from .db.models.score import ToolPublishedScore
    from sqlalchemy.orm import selectinload
    from PIL import Image, ImageDraw, ImageFont
    import io

    result = await db.execute(
        select(Tool).where(Tool.slug == slug, Tool.is_public == True)  # noqa: E712
        .options(selectinload(Tool.scores), selectinload(Tool.category))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        return Response(status_code=404)

    latest_score = tool.scores[0] if tool.scores else None
    category_name = tool.category.name_jp if tool.category else ""
    tool_name = tool.name_jp or tool.name or slug

    # Card dimensions (2x for retina)
    W, H = 800, 480
    BG = (255, 255, 255)
    DARK = (15, 23, 42)  # slate-900
    GRAY = (100, 116, 139)  # slate-500
    LIGHT_GRAY = (226, 232, 240)  # slate-200
    BRAND = (99, 102, 241)  # indigo-500

    GRADE_COLORS = {
        "S": (212, 175, 55), "A": (56, 161, 105), "B": (43, 108, 176),
        "C": (237, 137, 54), "D": (229, 62, 62),
    }
    AXIS_COLORS = {
        "practicality": (56, 161, 105), "cost_performance": (56, 161, 105),
        "localization": (43, 108, 176), "safety": (56, 161, 105),
        "uniqueness": (237, 137, 54),
    }
    AXIS_LABELS = {
        "practicality": "実務適性", "cost_performance": "費用対効果",
        "localization": "日本語能力", "safety": "信頼性・安全性", "uniqueness": "革新性",
    }

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Try to load TrueType font, fall back to default
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_grade = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_score = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except (IOError, OSError):
        font_lg = ImageFont.load_default()
        font_md = font_lg
        font_sm = font_lg
        font_grade = font_lg
        font_score = font_lg

    # --- Card border ---
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=12, outline=LIGHT_GRAY, width=2)

    # --- Header: tool name + vendor ---
    y = 32
    draw.text((40, y), tool_name[:30], fill=DARK, font=font_lg)
    y += 40
    if tool.vendor:
        draw.text((40, y), tool.vendor[:40], fill=GRAY, font=font_md)
        y += 28

    # --- Category badge ---
    if category_name:
        y += 8
        bbox = draw.textbbox((0, 0), category_name, font=font_sm)
        tw = bbox[2] - bbox[0]
        draw.rounded_rectangle([38, y - 2, 52 + tw, y + 20], radius=4, fill=(241, 245, 249))
        draw.text((44, y), category_name, fill=GRAY, font=font_sm)
        y += 30

    # --- Grade badge (top-right) ---
    if latest_score and latest_score.overall_grade:
        grade = latest_score.overall_grade
        gc = GRADE_COLORS.get(grade, (148, 163, 184))
        gx, gy, gs = W - 100, 28, 56
        draw.rounded_rectangle([gx, gy, gx + gs, gy + gs], radius=10, fill=gc)
        # Center grade letter
        gbbox = draw.textbbox((0, 0), grade, font=font_grade)
        gw = gbbox[2] - gbbox[0]
        gh = gbbox[3] - gbbox[1]
        draw.text((gx + (gs - gw) // 2, gy + (gs - gh) // 2 - 4), grade, fill=(255, 255, 255), font=font_grade)

    # --- 5-axis score bars ---
    y = max(y + 12, 160)
    bar_x, bar_w, bar_h = 180, 380, 14

    if latest_score:
        for axis_key in ["practicality", "cost_performance", "localization", "safety", "uniqueness"]:
            val = getattr(latest_score, axis_key, None)
            label = AXIS_LABELS.get(axis_key, axis_key)
            color = AXIS_COLORS.get(axis_key, BRAND)

            draw.text((40, y), label, fill=GRAY, font=font_sm)
            # Background bar
            draw.rounded_rectangle([bar_x, y + 2, bar_x + bar_w, y + 2 + bar_h], radius=4, fill=LIGHT_GRAY)
            # Fill bar
            if val and val > 0:
                fill_w = max(8, int(bar_w * val / 5.0))
                if val >= 4.0:
                    color = (56, 161, 105)  # green
                elif val >= 3.0:
                    color = (43, 108, 176)  # blue
                elif val >= 2.0:
                    color = (214, 158, 46)  # yellow
                else:
                    color = (229, 62, 62)  # red
                draw.rounded_rectangle([bar_x, y + 2, bar_x + fill_w, y + 2 + bar_h], radius=4, fill=color)
            # Score value
            score_text = f"{val:.1f}" if val else "--"
            draw.text((bar_x + bar_w + 16, y), score_text, fill=DARK, font=font_sm)
            y += 30

    # --- Overall score ---
    y = H - 60
    draw.line([(40, y - 12), (W - 40, y - 12)], fill=LIGHT_GRAY, width=1)
    draw.text((40, y), "総合スコア", fill=GRAY, font=font_sm)
    if latest_score and latest_score.overall_score is not None:
        score_str = f"{latest_score.overall_score:.1f} / 5.0"
        sbbox = draw.textbbox((0, 0), score_str, font=font_score)
        sw = sbbox[2] - sbbox[0]
        draw.text((W - 40 - sw, y - 4), score_str, fill=DARK, font=font_score)

    # --- Branding ---
    draw.text((W - 180, H - 24), "platform.aixis.jp", fill=(148, 163, 184), font=font_sm)

    # Encode to PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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
  <text x="100" y="120" font-family="sans-serif" font-size="22" fill="#94a3b8" font-weight="600">Aixis AI Audit Platform</text>
  <text x="100" y="280" font-family="sans-serif" font-size="72" fill="#f8fafc" font-weight="800">{tool_name}</text>
  <text x="100" y="340" font-family="sans-serif" font-size="28" fill="#94a3b8">{vendor}</text>
  <text x="100" y="520" font-family="sans-serif" font-size="20" fill="#64748b">独立監査スコア（5軸評価）</text>
  <circle cx="1000" cy="300" r="100" fill="none" stroke="#6366f1" stroke-width="6"/>
  <text x="1000" y="290" font-family="sans-serif" font-size="64" fill="#f8fafc" font-weight="800" text-anchor="middle">{overall_score}</text>
  <text x="1000" y="330" font-family="sans-serif" font-size="18" fill="#94a3b8" text-anchor="middle">/ 5.0</text>
  <text x="1000" y="520" font-family="sans-serif" font-size="18" fill="#475569" text-anchor="middle">platform.aixis.jp</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={
        "Cache-Control": "public, max-age=86400",
    })
