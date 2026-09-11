import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` relative to this file (backend/.env), never the process CWD:
# uvicorn, the worker, tests, and REPL sessions must all load the SAME file no
# matter where they are launched from.
_ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"


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

    # Open self-registration. Disable for locked-down demos / invite-only
    # deployments; existing users can still log in and admins remain usable.
    ALLOW_REGISTRATION: bool = True

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

    # ── Durable campaign job queue (PostgreSQL) ───────────────
    # A job is retried (with backoff) until it is terminal or it exhausts
    # ``max_attempts``; a worker crash is never a permanent FAILED.
    CAMPAIGN_JOB_MAX_ATTEMPTS: int = 3
    # Base backoff (seconds) before a failed job may be claimed again; the delay
    # grows linearly with the attempt number.
    CAMPAIGN_JOB_RETRY_BACKOFF_SECONDS: float = 30.0

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

    # ── Firebase (Firestore distributed rate limiting; Redis replacement) ──
    # The campaign job queue and every application table remain PostgreSQL; the
    # Firebase Admin SDK + Cloud Firestore only run the shared API rate limiter.
    # All three are required in production (validated at startup). In
    # development/tests they are optional: without them the rate limiter runs
    # per-instance (single-worker local dev only).
    FIREBASE_PROJECT_ID: Optional[str] = None
    FIREBASE_CLIENT_EMAIL: Optional[str] = None
    FIREBASE_PRIVATE_KEY: Optional[str] = None

    # ── Target Agents (external LLM endpoints) ─────────────────
    # Types accepted by TargetProviderFactory.
    TARGET_TYPES: str = "litellm,custom_http"
    # Time budget (seconds) for one TARGET_* provider call (custom_http / litellm).
    TARGET_CALL_TIMEOUT_SECONDS: float = 60.0
    # Maximum size of a custom HTTP target response body we are willing to read.
    MAX_TARGET_RESPONSE_BYTES: int = 512_000
    # Comma-separated private hosts the custom_http provider may call (SSRF
    # guard default-deny). Typically empty in production. Local development and
    # the test suite allow 127.0.0.1 / localhost so a locally-run dummy target
    # can be exercised end-to-end.
    SSRF_ALLOW_PRIVATE_HOSTS: str = ""
    # Comma-separated IPs (or hostnames) always allowed even when private
    # (e.g. a legacy internal LLM gateway on your own VPC).
    SSRF_DENY_ANY_PRIVATE: bool = True

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
        env_file=str(_ENV_FILE_PATH),
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
    def target_type_list(self) -> list[str]:
        return [t.strip() for t in self.TARGET_TYPES.split(",") if t.strip()]

    @property
    def ssrf_allow_private_hosts(self) -> list[str]:
        return [h.strip().lower() for h in self.SSRF_ALLOW_PRIVATE_HOSTS.split(",") if h.strip()]

    @property
    def firebase_configured(self) -> bool:
        """True when all Firebase service-account credentials are present."""
        return bool(
            self.FIREBASE_PROJECT_ID and self.FIREBASE_CLIENT_EMAIL and self.FIREBASE_PRIVATE_KEY
        )

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
        if self.RATE_LIMIT_ENABLED and not self.firebase_configured:
            raise RuntimeError(
                "Production requires Firebase credentials (FIREBASE_PROJECT_ID, "
                "FIREBASE_CLIENT_EMAIL, FIREBASE_PRIVATE_KEY) for the distributed "
                "rate limiter (see .env.example)."
            )


settings = Settings()


def export_provider_keys() -> None:
    """Make configured provider keys visible to runtime SDKs (litellm, openai).

    pydantic-settings loads ``.env`` only into the ``Settings`` object; the
    SDKs resolve keys from the process environment or their own CWD-relative
    dotenv loading. A missing key surfaces much later as a confusing, silent
    ``Attacker provider failed`` campaign failure. Export the configured keys so
    real provider calls work regardless of the process working directory.
    Existing process env vars always win (they override ``.env``).
    """
    for env_name, value in (
        ("OPENAI_API_KEY", settings.OPENAI_API_KEY),
        ("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY),
        ("DEEPSEEK_API_KEY", settings.DEEPSEEK_API_KEY),
    ):
        if value and not os.environ.get(env_name):
            os.environ[env_name] = value


export_provider_keys()
