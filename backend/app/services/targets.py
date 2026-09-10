"""Target-agent CRUD service + provider-config resolution + connection tests.

Target agents are the reusable "thing you attack". They live under a project
(owned by a user, enforced server-side) and carry a provider type plus
provider-specific config. Secrets (``api_key``) are encrypted at rest and only
decrypted at the moment a call is made in the execution service or a connection
test — the API surface never sees or returns them.
"""
import uuid
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AuthorizationError, InvalidInputError, NotFoundError
from app.core.secrets import decrypt_value, encrypt_value
from app.db.session import AsyncSessionLocal
from app.models.domain import Project, Target
from app.schemas.target import (
    TargetConnectionTestRequest,
    TargetConnectionTestResponse,
    TargetCreate,
    TargetRead,
    TargetUpdate,
)
from app.targets.factory import TargetFactory

_HTTP_SCHEMES = ("http://", "https://")


def validate_target_writable(
    provider_type: str, endpoint_url: Optional[str] = None
) -> None:
    """Fail fast on obviously unusable target definitions (server-side)."""
    ptype = (provider_type or "").lower().strip()
    if not ptype or ptype not in settings.target_type_list:
        raise InvalidInputError(
            f"provider_type must be one of: {', '.join(settings.target_type_list)}"
        )
    if ptype == "custom_http":
        if not endpoint_url:
            raise InvalidInputError(
                "custom_http targets require an endpoint_url."
            )
        if not endpoint_url.lower().startswith(_HTTP_SCHEMES):
            raise InvalidInputError("endpoint_url must be http(s)://.")


def _config_for(target: Target, include_secrets: bool = False) -> dict:
    cfg = dict(target.config_json or {})
    if not include_secrets:
        cfg.pop("api_key", None)
    return cfg


def build_provider_config(target: Target) -> dict:
    """Resolved, secret-decrypted config for a live provider call."""
    cfg = dict(target.config_json or {})
    if target.endpoint_url:
        cfg["endpoint_url"] = target.endpoint_url
    if cfg.get("api_key"):
        cfg["api_key"] = decrypt_value(str(cfg["api_key"]))
    return cfg


def snapshot_target(target: Target) -> dict:
    """Immutable, secret-free snapshot persisted on the Experiment row."""
    cfg = _config_for(target)
    return {
        "id": str(target.id),
        "name": target.name,
        "provider_type": target.provider_type,
        "is_enabled": target.is_enabled,
        "endpoint_url": target.endpoint_url,
        "model": cfg.get("model"),
        "api_base": cfg.get("api_base"),
    }


def target_to_read(target: Target) -> TargetRead:
    cfg = _config_for(target)
    return TargetRead(
        id=target.id,
        project_id=target.project_id,
        name=target.name,
        provider_type=target.provider_type,
        description=cfg.get("description"),
        is_enabled=target.is_enabled,
        endpoint_url=target.endpoint_url,
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
        api_base=cfg.get("api_base"),
        has_api_key=bool(cfg.get("api_key")),
        created_at=target.created_at,
    )


async def create_target(
    current_user_id: uuid.UUID, data: TargetCreate
) -> Target:
    validate_target_writable(data.provider_type, data.endpoint_url)

    async with AsyncSessionLocal() as session:
        project = (
            await session.execute(
                select(Project).where(Project.id == data.project_id)
            )
        ).scalars().first()
        if project is None:
            raise NotFoundError("Project not found.")
        if project.owner_id != current_user_id:
            raise AuthorizationError("You do not have access to this project.")

        config_json: dict = dict(data.extra_config or {})
        if data.description is not None:
            config_json["description"] = data.description
        if data.api_base is not None:
            config_json["api_base"] = data.api_base
        if data.model is not None:
            config_json["model"] = data.model
        if data.temperature is not None:
            config_json["temperature"] = data.temperature
        if data.api_key:
            config_json["api_key"] = encrypt_value(data.api_key)

        target = Target(
            project_id=data.project_id,
            name=data.name.strip(),
            provider_type=data.provider_type.lower().strip(),
            endpoint_url=data.endpoint_url,
            config_json=config_json,
            is_enabled=True,
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        return target


async def list_targets(current_user_id: uuid.UUID) -> list[Target]:
    async with AsyncSessionLocal() as session:
        owned_project_ids = select(Project.id).where(
            Project.owner_id == current_user_id
        )
        stmt = (
            select(Target)
            .where(Target.project_id.in_(owned_project_ids))
            .order_by(Target.created_at.desc())
        )
        return list((await session.execute(stmt)).scalars().all())


async def get_target_for_user(
    target_id: uuid.UUID, current_user_id: uuid.UUID
) -> Target:
    """Load a target and enforce project ownership server-side."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Target, Project.owner_id)
            .join(Project, Project.id == Target.project_id)
            .where(Target.id == target_id)
        )
        row = (await session.execute(stmt)).first()
    if row is None:
        raise NotFoundError("Target not found.")
    target, owner_id = row
    if owner_id != current_user_id:
        raise AuthorizationError("You do not have access to this target.")
    return target


async def update_target(
    target_id: uuid.UUID,
    current_user_id: uuid.UUID,
    data: TargetUpdate,
) -> Target:
    target = await get_target_for_user(target_id, current_user_id)

    new_provider_type = (data.provider_type or target.provider_type).lower().strip()
    validate_target_writable(new_provider_type, data.endpoint_url or target.endpoint_url)

    async with AsyncSessionLocal() as session:
        db_target = (
            await session.execute(select(Target).where(Target.id == target_id))
        ).scalars().first()
        if db_target is None:
            raise NotFoundError("Target not found.")

        cfg = dict(db_target.config_json or {})

        if data.name is not None:
            db_target.name = data.name.strip()
        if data.provider_type is not None:
            db_target.provider_type = new_provider_type
        if data.endpoint_url is not None:
            db_target.endpoint_url = data.endpoint_url
        if data.is_enabled is not None:
            db_target.is_enabled = data.is_enabled
        if data.description is not None:
            cfg["description"] = data.description
        if data.api_base is not None:
            cfg["api_base"] = data.api_base
        if data.model is not None:
            cfg["model"] = data.model
        if data.temperature is not None:
            cfg["temperature"] = data.temperature
        # Secret semantics (E-28): NULL api_key means "keep the existing key".
        if data.api_key:
            cfg["api_key"] = encrypt_value(data.api_key)
        if data.extra_config is not None:
            for key, value in data.extra_config.items():
                cfg[key] = value

        db_target.config_json = cfg
        await session.commit()
        await session.refresh(db_target)
        return db_target


async def delete_target(target_id: uuid.UUID, current_user_id: uuid.UUID) -> None:
    await get_target_for_user(target_id, current_user_id)
    async with AsyncSessionLocal() as session:
        db_target = (
            await session.execute(select(Target).where(Target.id == target_id))
        ).scalars().first()
        if db_target is not None:
            await session.delete(db_target)
            await session.commit()


def _truncate_preview(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def test_target(
    current_user_id: uuid.UUID,
    target_id: uuid.UUID,
    data: TargetConnectionTestRequest,
) -> TargetConnectionTestResponse:
    """Run one real call against the target to verify connectivity.

    The same provider/config resolution used by campaign execution, with the
    SSRF guard and failure classification applied — a green test tells the
    operator the target is genuinely reachable and speaking the contract.
    """
    target = await get_target_for_user(target_id, current_user_id)
    if not target.is_enabled:
        raise InvalidInputError("Target is disabled. Enable it before testing.")

    config = build_provider_config(target)
    default_model = config.get("model") or settings.DEFAULT_TARGET_MODEL
    provider = TargetFactory.get_provider(
        target.provider_type, default_model=default_model
    )
    result = await provider.execute(prompt=data.prompt, config=config)
    ok = result.error is None
    return TargetConnectionTestResponse(
        ok=ok,
        latency_ms=round(result.latency_ms, 2),
        error=result.error,
        error_type=result.error_type,
        status_code=result.status_code,
        response_preview=None if not ok else _truncate_preview(result.response_text),
    )
