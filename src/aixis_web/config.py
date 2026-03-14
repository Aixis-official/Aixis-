"""Application configuration using pydantic-settings."""
from pydantic_settings import BaseSettings


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
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Paths
    config_dir: str = "config"
    output_dir: str = "output"

    # Admin seed
    admin_email: str = "admin@aixis.jp"
    admin_password: str = "changeme123"

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

    model_config = {"env_prefix": "AIXIS_", "env_file": ".env"}


settings = Settings()
