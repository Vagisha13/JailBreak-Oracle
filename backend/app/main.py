import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect as sa_inspect
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.errors import APIError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimitMiddleware
from app.core.redis_client import check_health as check_redis_health
from app.core.security import SecurityHeadersMiddleware
from app.db.session import engine
from app.db.base import Base

from app import models  # noqa: F401 — ensure all models are registered with Base

logger = get_logger("app")

_REQUIRED_SCHEMA_TABLE = "experiments"


async def _schema_exists() -> bool:
    """Return True if the schema has already been created (via migrations)."""
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: sa_inspect(sync_conn).has_table(_REQUIRED_SCHEMA_TABLE)
        )


async def _schema_matches_models() -> bool:
    """Return True when the database matches the current ORM models exactly.

    ``create_all`` only creates missing tables; it never alters existing ones, so
    a dev database that predates a model change silently drifts out of sync and
    later explodes with opaque 500s (e.g. "no such column"). Fail fast instead.
    """

    async with engine.connect() as conn:
        def _check(sync_conn) -> bool:
            inspector = sa_inspect(sync_conn)
            return all(
                inspector.has_table(table.name)
                and set(table.columns.keys())
                <= {col["name"] for col in inspector.get_columns(table.name)}
                for table in Base.metadata.sorted_tables
            )

        return await conn.run_sync(_check)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_critical_secrets()
    if settings.APP_ENV == "production":
        # Schema administration is the migration's job in production. Fail fast
        # with a clear message instead of silently creating tables.
        if not await _schema_exists():
            raise RuntimeError(
                "Database schema missing. Run migrations first: "
                "`alembic upgrade head` (backend/)."
            )
    else:
        # Development convenience only — the test suite uses its own isolated DB.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        if not await _schema_matches_models():
            raise RuntimeError(
                "Development database schema is out of date. Run "
                "`alembic upgrade head` (backend/) or delete the stale "
                "dev database file to regenerate it."
            )

    # Fail fast when Redis is unusable: the worker and shared rate limiting
    # depend on it, and it is far easier to diagnose at startup than from a
    # buried campaign failure logged later.
    if settings.REDIS_URL:
        health = await get_redis_health()
        if health["available"]:
            logger.info("Redis connectivity confirmed", extra={"event_name": "redis.health_ok"})
        else:
            reason = health.get("reason", "unknown")
            raise RuntimeError(
                f"REDIS_URL is configured but Redis is unreachable ({reason}). "
                "Start Redis or remove REDIS_URL to run degraded."
            )
    yield


async def get_redis_health() -> dict:
    """Probe the shared Redis client for the startup check and /health."""
    return await check_redis_health()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.DESCRIPTION,
    version=settings.VERSION,
    lifespan=lifespan,
)

# Middleware ordering (first added = innermost): CORS stays outermost so that
# both proxied API responses and rate-limit responses carry CORS headers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers=exc.headers() if hasattr(exc, "headers") else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "code": "validation_error"},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error_id = uuid.uuid4()
    logger.error(
        "Unhandled exception",
        extra={
            "event_name": "app.unhandled_error",
            "error_id": str(error_id),
            "error_type": type(exc).__name__,
            "path": request.url.path,
        },
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "code": "internal_error",
            "error_id": str(error_id),
        },
    )


from app.api.routers import campaigns, reports, vulnerabilities, auth, analytics, targets  # noqa: E402

app.include_router(auth.router, prefix="/api/v1")
app.include_router(campaigns.router, prefix="/api/v1")
app.include_router(reports.router)
app.include_router(vulnerabilities.router)
app.include_router(analytics.router)
app.include_router(targets.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    # Keep the liveness body stable and minimal: downstream health checks and
    # the rate-limit exemption rely on it. Dependency detail lives on the
    # explicit /health/dependencies endpoint so it stays opt-in.
    return {"status": "healthy"}


@app.get("/health/dependencies")
async def health_dependencies():
    """Detailed dependency probe for dashboards / status pages."""
    return {
        "status": "healthy",
        "dependencies": {
            "database": "ok",
            "redis": await check_redis_health(),
        },
    }
