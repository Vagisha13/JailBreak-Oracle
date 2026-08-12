import uuid
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.report import RedTeamReport
from app.services.report import ReportService

router = APIRouter(prefix="/api/v1/reports", tags=["Reports & Analytics"])


def get_report_service() -> ReportService:
    return ReportService()


@router.get("/experiment/{experiment_id}", response_model=RedTeamReport)
async def get_experiment_report(
    experiment_id: uuid.UUID, service: ReportService = Depends(get_report_service)
):
    """Generates a complete security assessment report for a given red teaming experiment."""
    try:
        return await service.generate_experiment_report(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
