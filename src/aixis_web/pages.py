"""SSR page routes using Jinja2 templates."""
from datetime import datetime
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
templates.env.globals["now"] = datetime.utcnow

page_router = APIRouter(default_response_class=HTMLResponse)


def _get_template_context(request: Request, user=None, **extra) -> dict:
    """Build template context with i18n support."""
    lang = detect_language(
        query_param=request.query_params.get("lang"),
        accept_language=request.headers.get("accept-language"),
        user_pref=getattr(user, "preferred_language", None) if user else None,
        cookie_lang=request.cookies.get("aixis_lang"),
    )
    translator = get_translator(lang)
    return {
        "request": request,
        "user": user,
        "_": translator,
        "lang": lang,
        **extra,
    }


# ──────────── Legacy /platform redirect ────────────


@page_router.get("/platform")
async def legacy_platform_landing(request: Request):
    """Serve landing page at old /platform URL for cached 301 redirects."""
    ctx = _get_template_context(request, title="感覚ではなく、数値で選ぶ。", active_page="home")
    return templates.TemplateResponse("public/landing.html", ctx)


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
async def landing(request: Request):
    """Landing page."""
    ctx = _get_template_context(request, title="感覚ではなく、数値で選ぶ。", active_page="home")
    return templates.TemplateResponse("public/landing.html", ctx)


@page_router.get("/tools")
async def tools_page(request: Request):
    """Tool catalog page."""
    ctx = _get_template_context(request, title="AIツール監査データベース", active_page="tools")
    return templates.TemplateResponse("public/tools.html", ctx)


@page_router.get("/categories")
async def categories_index(request: Request):
    """Categories index page."""
    return templates.TemplateResponse(
        "public/categories.html",
        {"request": request, "title": "カテゴリ別ランキング", "active_page": "categories"},
    )


@page_router.get("/tools/{slug}")
async def tool_detail_page(request: Request, slug: str):
    """Tool detail page."""
    return templates.TemplateResponse(
        "public/tool_detail.html",
        {"request": request, "title": "ツール詳細", "slug": slug, "active_page": "tools"},
    )


@page_router.get("/compare")
async def compare_page(request: Request):
    """Comparison view page."""
    return templates.TemplateResponse(
        "public/compare.html",
        {"request": request, "title": "AIツール比較", "active_page": "compare"},
    )


@page_router.get("/categories/{slug}")
async def category_page(request: Request, slug: str):
    """Category page."""
    return templates.TemplateResponse(
        "public/category.html",
        {"request": request, "title": "カテゴリ", "slug": slug, "active_page": "categories"},
    )


@page_router.get("/terms")
async def terms_page(request: Request):
    """Terms of service page."""
    ctx = _get_template_context(request, title="利用規約", active_page="terms")
    return templates.TemplateResponse("public/terms.html", ctx)


@page_router.get("/pricing")
async def pricing_page(request: Request):
    """Pricing plans page."""
    ctx = _get_template_context(request, title="料金プラン", active_page="pricing")
    return templates.TemplateResponse("public/pricing.html", ctx)


@page_router.get("/audit-process")
async def audit_process_page(request: Request):
    """Audit process explanation page."""
    ctx = _get_template_context(request, title="監査プロセス", active_page="services")
    return templates.TemplateResponse("public/audit_process.html", ctx)


@page_router.get("/independence")
async def independence_page(request: Request):
    """Independence declaration page."""
    ctx = _get_template_context(request, title="独立性宣言", active_page="about")
    return templates.TemplateResponse("public/independence.html", ctx)


@page_router.get("/transparency")
async def transparency_page(request: Request):
    """Transparency policy page."""
    ctx = _get_template_context(request, title="透明性ポリシー", active_page="transparency")
    return templates.TemplateResponse("public/transparency.html", ctx)


@page_router.get("/audit-protocol")
async def audit_protocol_page(request: Request):
    """Detailed audit protocol page."""
    ctx = _get_template_context(request, title="監査プロトコル", active_page="audit-protocol")
    return templates.TemplateResponse("public/audit_protocol.html", ctx)


@page_router.get("/contact")
async def contact_page(request: Request):
    """Contact form page."""
    ctx = _get_template_context(request, title="お問い合わせ", active_page="contact")
    return templates.TemplateResponse("public/contact.html", ctx)


@page_router.get("/login")
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse(
        "public/login.html",
        {"request": request, "title": "ログイン", "active_page": "login"},
    )


# ──────────── Auth-Protected Pages ────────────


@page_router.get("/dashboard")
async def dashboard_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin dashboard (requires auth)."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    ctx = _get_template_context(request, user=user, title="管理ダッシュボード", active_page="dashboard")
    return templates.TemplateResponse("dashboard/index.html", ctx)


@page_router.get("/dashboard/tools")
async def tools_management_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Tool management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/tools.html",
        {"request": request, "title": "ツール管理", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/manual")
async def manual_list_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Manual evaluation list page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/manual_list.html",
        {"request": request, "title": "手動評価一覧", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/settings")
async def settings_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Platform settings page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/settings.html",
        {"request": request, "title": "設定", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/audits/new")
async def new_audit_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """New audit creation page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/audit_new.html",
        {"request": request, "title": "新規監査を開始", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/audits/{session_id}")
async def audit_detail_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit session detail page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/audit_detail.html",
        {
            "request": request,
            "title": "監査セッション詳細",
            "user": user,
            "session_id": session_id,
            "active_page": "dashboard",
        },
    )


@page_router.get("/dashboard/audits/{session_id}/manual")
async def manual_checklist_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Manual checklist evaluation page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/manual_checklist.html",
        {
            "request": request,
            "title": "手動チェックリスト評価",
            "user": user,
            "session_id": session_id,
            "active_page": "dashboard",
        },
    )


@page_router.get("/dashboard/comparison")
async def comparison_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Tool score comparison page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/comparison.html",
        {"request": request, "title": "スコア比較", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/custom-tests")
async def custom_tests_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Custom test case management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/custom_tests.html",
        {"request": request, "title": "カスタムテスト管理", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/api-keys")
async def api_keys_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """API key management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/api_keys.html",
        {"request": request, "title": "APIキー管理", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/audits/{session_id}/log")
async def audit_log_page(
    request: Request,
    session_id: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit log detail page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/audit_log.html",
        {
            "request": request,
            "title": "監査ログ詳細",
            "user": user,
            "session_id": session_id,
            "active_page": "dashboard",
        },
    )


@page_router.get("/dashboard/webhooks")
async def webhooks_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Webhook management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/webhooks.html",
        {"request": request, "title": "Webhook管理", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/dashboard/notifications")
async def notifications_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Notification center page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/notifications.html",
        {"request": request, "title": "通知センター", "user": user, "active_page": "dashboard"},
    )


@page_router.get("/portal")
async def portal_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Client portal (requires auth)."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "portal/index.html",
        {"request": request, "title": "クライアントポータル", "user": user, "active_page": "portal"},
    )


# ──────────── Scheduled Re-audits ────────────


@page_router.get("/dashboard/schedules")
async def schedules_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Audit schedule management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/schedules.html",
        {"request": request, "title": "スケジュール管理", "user": user, "active_page": "dashboard"},
    )


# ──────────── Vendor Portal ────────────


@page_router.get("/vendor/guide")
async def vendor_guide_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Public vendor guide — tool listing request info."""
    ctx = _get_template_context(request, user=user, title="ベンダーの皆様へ", active_page="vendor")
    return templates.TemplateResponse("vendor/landing.html", ctx)


@page_router.get("/vendor")
async def vendor_dashboard_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Vendor self-service dashboard (requires auth)."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    ctx = _get_template_context(request, user=user, title="ベンダーポータル", active_page="vendor")
    return templates.TemplateResponse("vendor/dashboard.html", ctx)


@page_router.get("/vendor/submit")
async def vendor_submit_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Vendor tool submission form."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "vendor/submit_tool.html",
        {"request": request, "title": "ツール申請", "user": user, "active_page": "vendor"},
    )


@page_router.get("/dashboard/submissions")
async def admin_submissions_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin submission review queue."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/submissions.html",
        {"request": request, "title": "申請審査", "user": user, "active_page": "dashboard"},
    )


# ──────────── Benchmarks & Leaderboard ────────────


@page_router.get("/benchmarks/{slug}/leaderboard")
async def leaderboard_page(
    request: Request,
    slug: str,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Public benchmark leaderboard page."""
    return templates.TemplateResponse(
        "public/leaderboard.html",
        {"request": request, "title": "リーダーボード", "slug": slug, "user": user, "active_page": "benchmarks"},
    )


@page_router.get("/dashboard/benchmarks")
async def benchmark_manage_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Admin benchmark management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard/benchmark_manage.html",
        {"request": request, "title": "ベンチマーク管理", "user": user, "active_page": "dashboard"},
    )
