"""API v1 master router."""
from fastapi import APIRouter

from .tools import router as tools_router
from .audits import router as audits_router
from .scores import router as scores_router
from .comparisons import router as comparisons_router
from .reports import router as reports_router
from .auth import router as auth_router
from .contact import router as contact_router
from .profiles import router as profiles_router
from .settings import router as settings_router
from .presets import router as presets_router
from .api_keys import router as api_keys_router
from .webhooks import router as webhooks_router
from .notifications import router as notifications_router
from .schedules import router as schedules_router
from .vendor import router as vendor_router
from .benchmarks import router as benchmarks_router
from .industries import router as industries_router
from .risk_governance import router as risk_governance_router
from .stats import router as stats_router
from .clients import router as clients_router
from .agent import router as agent_router
from .extension import router as extension_router

api_router = APIRouter()


@api_router.get("/health")
async def health_check():
    return {"status": "ok"}


api_router.include_router(auth_router, prefix="/auth", tags=["認証"])
api_router.include_router(contact_router, prefix="/contact", tags=["お問い合わせ"])
api_router.include_router(tools_router, prefix="/tools", tags=["ツール"])
api_router.include_router(audits_router, prefix="/audits", tags=["監査"])
api_router.include_router(scores_router, prefix="/scores", tags=["スコア"])
api_router.include_router(comparisons_router, prefix="/comparisons", tags=["比較"])
api_router.include_router(reports_router, prefix="/reports", tags=["レポート"])
api_router.include_router(profiles_router, prefix="/profiles", tags=["プロファイル"])
api_router.include_router(settings_router, prefix="/settings", tags=["設定"])
api_router.include_router(presets_router, prefix="/presets", tags=["プリセット"])
api_router.include_router(api_keys_router, prefix="/api-keys", tags=["APIキー"])
api_router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhook"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["通知"])
api_router.include_router(schedules_router, prefix="/schedules", tags=["スケジュール"])
api_router.include_router(vendor_router, prefix="/vendor", tags=["ベンダー"])
api_router.include_router(benchmarks_router, prefix="/benchmarks", tags=["ベンチマーク"])
api_router.include_router(industries_router, prefix="/industries", tags=["業界・ユースケース"])
api_router.include_router(risk_governance_router, prefix="/risk-governance", tags=["リスク・ガバナンス"])
api_router.include_router(stats_router, prefix="/stats", tags=["stats"])
api_router.include_router(clients_router, prefix="/clients", tags=["クライアント管理"])
api_router.include_router(agent_router, prefix="/agent", tags=["ローカルエージェント"])
api_router.include_router(extension_router, prefix="/extension", tags=["Chrome拡張"])
