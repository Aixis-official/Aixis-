"""Application configuration using pydantic-settings."""
import logging
import secrets

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_DEFAULT_SECRET = "CHANGE-ME-IN-PRODUCTION"
_DEFAULT_ADMIN_PW = "changeme123"  # Only used for checking if password was changed


class Settings(BaseSettings):
    # Application
    app_name: str = "Aixis AI監査プラットフォーム"
    app_version: str = "2.0.0"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./aixis.db"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Auth
    secret_key: str = _DEFAULT_SECRET
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Paths
    config_dir: str = "config"
    output_dir: str = "output"

    # Admin seed
    admin_email: str = "admin@aixis.jp"
    admin_password: str = _DEFAULT_ADMIN_PW

    # Audit
    default_timeout_ms: int = 120000
    max_concurrent_audits: int = 3

    # AI Browser Agent (Haiku Vision — hybrid: learn once, replay without API)
    anthropic_api_key: str = ""
    ai_agent_model: str = "claude-haiku-4-5-20251001"
    ai_budget_max_calls: int = 30   # Discovery ~5 + Recovery ~25
    ai_budget_max_calls_per_case: int = 3
    ai_budget_max_cost_jpy: int = 20  # 1監査あたりのコスト上限（円）

    # SMTP settings
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@aixis.jp"
    smtp_to: str = "info@aixis.jp"

    # Resend API (HTTP-based email — used when SMTP ports are blocked)
    resend_api_key: str = ""
    resend_from: str = "Aixis <noreply@aixis.jp>"

    # Webhook settings
    webhook_enabled: bool = True
    webhook_max_retries: int = 4

    # Scheduler settings
    scheduler_enabled: bool = True
    scheduler_check_interval_seconds: int = 60

    # Public API settings
    public_api_enabled: bool = True
    public_api_default_rate_limit_per_minute: int = 60
    public_api_default_rate_limit_per_day: int = 10000

    # Security: Contact form rate limiting
    contact_rate_limit_per_ip: int = 5  # max submissions per IP per hour
    contact_rate_limit_window_seconds: int = 3600

    model_config = {"env_file": ".env"}


settings = Settings()


def validate_settings():
    """Validate critical security settings on startup."""
    warnings = []

    if settings.secret_key == _DEFAULT_SECRET:
        # In production this is critical; in development, auto-generate
        if not settings.debug:
            logger.critical(
                "SECURITY: secret_key is set to the default value! "
                "Set SECRET_KEY environment variable to a secure random string (min 32 chars). "
                "Auto-generating a temporary key for this session."
            )
        settings.secret_key = secrets.token_urlsafe(48)
        warnings.append("secret_key was default — auto-generated temporary key")

    if len(settings.secret_key) < 32:
        logger.warning(
            "SECURITY: secret_key is shorter than 32 characters. "
            "Use a longer key for production."
        )

    if settings.admin_password == _DEFAULT_ADMIN_PW and not settings.debug:
        logger.warning(
            "SECURITY: admin_password is set to the default value. "
            "Set ADMIN_PASSWORD environment variable to a secure password."
        )

    return warnings


# Run validation on import
_startup_warnings = validate_settings()
