"""Target-agent CRUD + connectivity test endpoints.

Everything is owner-scoped server-side (E-23 pattern): a user can only see or
modify targets under their own projects. Secrets (API keys) are encrypted at
rest and never returned by any read endpoint — only a boolean ``has_api_key``.
"""
import uuid

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.domain import User
from app.schemas.target import (
    TargetConnectionTestRequest,
    TargetConnectionTestResponse,
    TargetCreate,
    TargetRead,
    TargetUpdate,
)
from app.services import targets as target_service

logger = get_logger("api.targets")

router = APIRouter(prefix="/targets", tags=["Target Agents"])


@router.post("", response_model=TargetRead, status_code=201)
async def create_target(
    data: TargetCreate,
    current_user: User = Depends(get_current_user),
) -> TargetRead:
    target = await target_service.create_target(current_user.id, data)
    logger.info(
        "Target agent created",
        extra={
            "event_name": "targets.created",
            "user_id": str(current_user.id),
            "target_id": str(target.id),
            "provider_type": target.provider_type,
        },
    )
    return target_service.target_to_read(target)


@router.get("", response_model=list[TargetRead])
async def list_targets(
    current_user: User = Depends(get_current_user),
) -> list[TargetRead]:
    targets = await target_service.list_targets(current_user.id)
    return [target_service.target_to_read(t) for t in targets]


@router.get("/{target_id}", response_model=TargetRead)
async def get_target(
    target_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
) -> TargetRead:
    target = await target_service.get_target_for_user(target_id, current_user.id)
    return target_service.target_to_read(target)


@router.patch("/{target_id}", response_model=TargetRead)
async def update_target(
    target_id: uuid.UUID,
    data: TargetUpdate,
    current_user: User = Depends(get_current_user),
) -> TargetRead:
    target = await target_service.update_target(target_id, current_user.id, data)
    logger.info(
        "Target agent updated",
        extra={
            "event_name": "targets.updated",
            "user_id": str(current_user.id),
            "target_id": str(target_id),
        },
    )
    return target_service.target_to_read(target)


@router.delete("/{target_id}")
async def delete_target(
    target_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
) -> dict:
    await target_service.delete_target(target_id, current_user.id)
    logger.info(
        "Target agent deleted",
        extra={
            "event_name": "targets.deleted",
            "user_id": str(current_user.id),
            "target_id": str(target_id),
        },
    )
    return {"detail": "Target deleted."}


@router.post("/{target_id}/test", response_model=TargetConnectionTestResponse)
async def test_target(
    target_id: uuid.UUID,
    data: TargetConnectionTestRequest,
    current_user: User = Depends(get_current_user),
) -> TargetConnectionTestResponse:
    result = await target_service.test_target(current_user.id, target_id, data)
    logger.info(
        "Target connection test",
        extra={
            "event_name": "targets.tested",
            "user_id": str(current_user.id),
            "target_id": str(target_id),
            "ok": result.ok,
            "error_type": result.error_type,
        },
    )
    return result
