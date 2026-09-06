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
    yield


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


from app.api.routers import campaigns, reports, vulnerabilities, auth  # noqa: E402

app.include_router(auth.router, prefix="/api/v1")
app.include_router(campaigns.router, prefix="/api/v1")
app.include_router(reports.router)
app.include_router(vulnerabilities.router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
