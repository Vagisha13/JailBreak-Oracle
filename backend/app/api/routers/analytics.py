"""Analytics API (Phase 12 / E-22).

Exposes the DB-backed metrics service over authenticated, ownership-scoped
endpoints. Every data route enforces user isolation (401 / 403) exactly like the
rest of the API surface.
"""
import uuid

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.api.access import get_user_project_ids, get_experiment_or_403
from app.models.domain import User
from app.services.metrics import MetricsService

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


def get_metrics_service() -> MetricsService:
    return MetricsService()


@router.get("/metrics")
async def get_global_metrics(
    current_user: User = Depends(get_current_user),
    service: MetricsService = Depends(get_metrics_service),
):
    """Aggregate metrics across all campaigns owned by the caller."""
    project_ids = await get_user_project_ids(current_user.id)
    metrics = await service.global_metrics(project_ids)
    return metrics.to_dict()


@router.get("/experiments/{experiment_id}/metrics")
async def get_experiment_metrics(
    experiment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: MetricsService = Depends(get_metrics_service),
):
    """Aggregate metrics for a single owned campaign (403 for cross-owner)."""
    await get_experiment_or_403(experiment_id, current_user.id)
    metrics = await service.experiment_metrics(experiment_id)
    return metrics.to_dict()
