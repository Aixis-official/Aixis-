"""SSR page routes using Jinja2 templates."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from .api.deps import get_current_user
from .db.models.user import User
from .i18n import get_translator, detect_language

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
    if user:
        try:
            from .services.subscription_service import get_subscription_info
            ctx["subscription"] = get_subscription_info(user)
        except Exception:
            pass
    return ctx


# Common dependency for optional user on public pages
_OptionalUser = Annotated[User | None, Depends(get_current_user)]


# ──────────── Legacy /platform redirect ────────────


@page_router.get("/platform")
async def legacy_platform_landing(request: Request, user: _OptionalUser = None):
    """Serve landing page at old /platform URL for cached 301 redirects."""
    ctx = _get_template_context(request, user=user, title="AIツール比較・一覧 | 独立監査で選ぶ", active_page="home")
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
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=f"/{safe_path}", status_code=302)


# ──────────── Public Pages ────────────


@page_router.get("/")
async def landing(request: Request, user: _OptionalUser = None):
    """Landing page."""
    ctx = _get_template_context(request, user=user, title="AIツール比較・一覧 | 独立監査で選ぶ", active_page="home")
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
async def tool_detail_page(request: Request, slug: str, user: _OptionalUser = None):
    """Tool detail page."""
    ctx = _get_template_context(request, user=user, title="ツール詳細レビュー・評価", slug=slug, active_page="tools")
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


@page_router.get("/pricing")
async def pricing_page(request: Request, user: _OptionalUser = None):
    """Pricing plans page."""
    ctx = _get_template_context(request, user=user, title="料金プラン | 14日間無料トライアル", active_page="pricing")
    return _render("public/pricing.html", ctx)


@page_router.get("/audit-process")
async def audit_process_page(request: Request, user: _OptionalUser = None):
    """Audit process explanation page."""
    ctx = _get_template_context(request, user=user, title="AI監査プロセス | 評価方法の詳細", active_page="services")
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


@page_router.get("/contact")
async def contact_page(request: Request, user: _OptionalUser = None):
    """Contact form page."""
    ctx = _get_template_context(request, user=user, title="お問い合わせ | 14日間の無料トライアル申請", active_page="contact")
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
