from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Jailbreak Oracle"
    VERSION: str = "2.0.0"
    DESCRIPTION: str = "Adaptive LLM Red-Teaming & Vulnerability Discovery Platform"

    # "development" or "production". Production enforces stricter validation.
    APP_ENV: str = "development"

    # ── Database ──────────────────────────────────────────────
    ASYNC_DATABASE_URL: str = "sqlite+aiosqlite:///./oracle.db"
    SYNC_DATABASE_URL: str = "sqlite:///./oracle.db"
    # Overrides ASYNC/SYNC URLs for the test suite only. When unset the test
    # suite provisions an isolated temporary SQLite file automatically.
    TEST_DATABASE_URL: Optional[str] = None

    # ── Authentication ────────────────────────────────────────
    # No hardcoded or ephemeral default: production requires an explicit value.
    SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    # Comma-separated emails promoted to the "admin" role on registration (E-16).
    # Unset/empty means every new user is a "researcher".
    BOOTSTRAP_ADMIN_EMAILS: str = ""

    # ── LLM Provider Keys ─────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # ── Agent Model Configuration ─────────────────────────────
    # Attacker agent — generates adversarial prompts
    ATTACKER_PROVIDER: str = "litellm"
    ATTACKER_MODEL: str = "gpt-4o-mini"

    # Evaluator agent — grades target responses
    EVALUATOR_PROVIDER: str = "litellm"
    EVALUATOR_MODEL: str = "gpt-4o-mini"

    # Verifier agent — independently confirms vulnerabilities
    VERIFIER_PROVIDER: str = "litellm"
    VERIFIER_MODEL: str = "gpt-4o-mini"

    # Defender agent — generates remediation guidance + regression scores
    DEFENDER_PROVIDER: str = "litellm"
    DEFENDER_MODEL: str = "gpt-4o-mini"

    # Default target model (overridden per-campaign)
    DEFAULT_TARGET_PROVIDER: str = "litellm"
    DEFAULT_TARGET_MODEL: str = "gpt-4o-mini"

    # ── Embedding ─────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "mock"  # "openai" or "mock"
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # ── Campaign Defaults ─────────────────────────────────────
    DEFAULT_MAX_ROUNDS: int = 20
    DEFAULT_ATTACK_BUDGET: int = 50
    DEFAULT_EXPLORATION_RATIO: float = 0.3
    MAX_CAMPAIGN_ROUNDS: int = 500
    MAX_ATTACK_BUDGET: int = 500
    MAX_CAMPAIGN_COST: float = 50.0  # USD
    CAMPAIGN_TIMEOUT_SECONDS: int = 3600

    # ── Rate Limiting ─────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60  # default bucket
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10  # login / register
    CAMPAIGN_RATE_LIMIT_PER_MINUTE: int = 5  # campaign creation / start
    LLM_RATE_LIMIT_PER_MINUTE: int = 30  # LLM-backed endpoints (verify)
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    # Comma-separated reverse-proxy IPs/CIDRs allowed to set ``X-Forwarded-For``
    # for rate limiting (E-24). Empty disables XFF trust entirely, so clients
    # cannot spoof their way around per-IP buckets via the header.
    TRUSTED_PROXIES: str = ""

    # ── Redis (job queue + distributed rate limiting) ─────────
    REDIS_URL: Optional[str] = None

    # ── LLM Provider Resilience ───────────────────────────────
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 1.0

    # ── CORS / Security ───────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"
    SECURITY_ENABLE_HEADERS: bool = True

    # ── Logging ───────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def bootstrap_admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.BOOTSTRAP_ADMIN_EMAILS.split(",") if e.strip()]

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [p.strip() for p in self.TRUSTED_PROXIES.split(",") if p.strip()]

    @property
    def jwt_secret(self) -> str:
        """
        JWT signing secret.
        - Production: must be supplied explicitly (validated at startup).
        - Development: a stable dev secret is preferred so tokens survive restarts;
          fall back to a per-process random secret otherwise.
        """
        if self.SECRET_KEY:
            return self.SECRET_KEY
        if self.APP_ENV != "production":
            return "development-only-insecure-secret-do-not-use-in-production"
        raise RuntimeError("SECRET_KEY must be set in production.")

    def validate_critical_secrets(self) -> None:
        """Fail fast when required production configuration is missing."""
        if self.APP_ENV != "production":
            return
        if not self.SECRET_KEY:
            raise RuntimeError("SECRET_KEY must be set in production (see .env.example).")
        if not self.ASYNC_DATABASE_URL or "sqlite" in self.ASYNC_DATABASE_URL:
            raise RuntimeError(
                "Production requires a PostgreSQL ASYNC_DATABASE_URL "
                "(SQLite is only supported for development/tests)."
            )
        if not self.REDIS_URL:
            raise RuntimeError(
                "REDIS_URL must be set in production (required for the campaign worker)."
            )


settings = Settings()
