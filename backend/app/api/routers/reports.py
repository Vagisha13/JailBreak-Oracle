import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.domain import User
from app.schemas.report import RedTeamReport
from app.services.report import ReportService
from app.api.access import get_experiment_or_403

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["Reports & Analytics"])


def get_report_service() -> ReportService:
    return ReportService()


@router.get("/experiment/{experiment_id}", response_model=RedTeamReport)
async def get_experiment_report(
    experiment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """Generates a security assessment report for an owned experiment."""
    await get_experiment_or_403(experiment_id, current_user.id)
    try:
        return await service.generate_experiment_report(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        logger.exception("Failed to generate report for experiment %s", experiment_id)
        raise HTTPException(status_code=500, detail="Failed to generate report.")
