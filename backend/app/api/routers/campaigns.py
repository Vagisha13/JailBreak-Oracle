import uuid

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from sqlalchemy.future import select
from sqlalchemy import func

from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import (
    User,
    Project,
    Target,
    Experiment,
    Vulnerability,
    Attack,
    AttackResult,
    AttackMutation,
    TokenUsage,
)
from app.schemas.campaign import CampaignStartRequest, CampaignResponse
from app.services.factory import build_campaign_orchestrator
from app.services.campaign import CampaignOrchestrator
from app.services.queue import enqueue_campaign
from app.api.access import get_target_or_403, get_project_or_403, get_experiment_or_403

logger = get_logger("api.campaigns")

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


def get_orchestrator() -> CampaignOrchestrator:
    return build_campaign_orchestrator()


@router.get("/stats")
async def get_dashboard_stats(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        owner_project_ids = select(Project.id).where(Project.owner_id == current_user.id)
        user_exp_ids = select(Experiment.id).where(
            Experiment.project_id.in_(owner_project_ids)
        )

        active = (
            await session.execute(
                select(func.count())
                .select_from(Experiment)
                .where(
                    Experiment.status.in_(["PENDING", "RUNNING"]),
                    Experiment.id.in_(user_exp_ids),
                )
            )
        ).scalar() or 0

        completed = (
            await session.execute(
                select(func.count())
                .select_from(Experiment)
                .where(
                    Experiment.status == "COMPLETED",
                    Experiment.id.in_(user_exp_ids),
                )
            )
        ).scalar() or 0

        vulns = (
            await session.execute(
                select(func.count())
                .select_from(Vulnerability)
                .where(Vulnerability.experiment_id.in_(user_exp_ids))
            )
        ).scalar() or 0

        return {
            "active_campaigns": active,
            "completed_reports": completed,
            "total_vulnerabilities": vulns,
        }


@router.post("/setup-demo")
async def setup_demo_environment(current_user: User = Depends(get_current_user)):
    """Create a demo project + mock target owned by the current user."""
    async with AsyncSessionLocal() as session:
        project = Project(name="Demo Web Project", owner_id=current_user.id)
        session.add(project)
        await session.flush()

        target = Target(project_id=project.id, name="Demo Mock Target", provider_type="mock")
        session.add(target)
        await session.commit()

        logger.info(
            "Demo environment created",
            extra={
                "event_name": "campaign.demo_created",
                "user_id": str(current_user.id),
            },
        )

        return {
            "project_id": str(project.id),
            "target_id": str(target.id),
            "message": "Demo environment ready!",
        }


@router.get("", response_model=list[dict])
async def list_campaigns(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        owner_project_ids = select(Project.id).where(Project.owner_id == current_user.id)
        stmt = (
            select(Experiment)
            .where(Experiment.project_id.in_(owner_project_ids))
            .order_by(Experiment.created_at.desc())
        )
        experiments = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(e.id),
                "name": e.name,
                "status": e.status,
                "attack_budget": e.attack_budget,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in experiments
        ]


@router.get("/strategies")
async def list_strategies():
    from app.strategies.registry import get_all_strategies

    strategies = get_all_strategies()
    return [
        {"name": s.name, "category": s.category, "metadata": s.metadata()}
        for s in strategies.values()
    ]


@router.get("/{experiment_id}/status")
async def get_campaign_status(
    experiment_id: uuid.UUID, current_user: User = Depends(get_current_user)
):
    experiment = await get_experiment_or_403(experiment_id, current_user.id)

    # Token/cost helm (E-12): roll up the campaign's persisted LLM ledger.
    async with AsyncSessionLocal() as session:
        usage_stmt = (
            select(
                func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
            ).where(TokenUsage.experiment_id == experiment.id)
        )
        usage_row = (await session.execute(usage_stmt)).one()

    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status,
        "attack_budget": experiment.attack_budget,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
        "finished_at": experiment.finished_at.isoformat() if experiment.finished_at else None,
        "budget": {
            "prompt_tokens": usage_row[0],
            "completion_tokens": usage_row[1],
            "total_cost_usd": round(float(usage_row[2]), 4),
            "max_cost_usd": experiment.max_cost_usd,
        },
    }


@router.get("/{experiment_id}/attacks")
async def get_campaign_attacks(
    experiment_id: uuid.UUID, current_user: User = Depends(get_current_user)
):
    await get_experiment_or_403(experiment_id, current_user.id)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Attack)
            .where(Attack.experiment_id == experiment_id)
            .order_by(Attack.created_at.desc())
        )
        attacks = (await session.execute(stmt)).scalars().all()
        results = []

        # Build attack_id -> mutation metadata map (for mutation_type attribution).
        mutation_stmt = (
            select(AttackMutation)
            .where(
                AttackMutation.attack_id.in_([a.id for a in attacks])
            )
        )
        mutations_map: dict[str, dict] = {}
        for mutation in (await session.execute(mutation_stmt)).scalars().all():
            mutations_map[str(mutation.attack_id)] = {
                "mutation_type": mutation.mutation_type
            }

        for attack in attacks:
            result_stmt = select(AttackResult).where(AttackResult.attack_id == attack.id)
            attack_result = (await session.execute(result_stmt)).scalars().first()

            vuln_stmt = select(Vulnerability).where(Vulnerability.attack_id == attack.id)
            vuln = (await session.execute(vuln_stmt)).scalars().first()

            results.append(
                {
                    "id": str(attack.id),
                    "strategy_name": attack.strategy_name,
                    "category": attack.category,
                    "prompt_text": attack.prompt_text,
                    "parent_attack_id": str(attack.parent_attack_id) if attack.parent_attack_id else None,
                    "round_number": attack.round_number,
                    "created_at": attack.created_at.isoformat() if attack.created_at else None,
                    "target_response": attack_result.target_response if attack_result else None,
                    "latency_ms": attack_result.latency_ms if attack_result else None,
                    "is_jailbreak": vuln is not None,
                    "severity": vuln.severity if vuln else None,
                    "verified_status": vuln.verified_status if vuln else None,
                    "mutation_type": (
                        mutations_map.get(str(attack.id), {}).get("mutation_type")
                    ),
                }
            )
        return results


@router.post("/start", response_model=CampaignResponse)
async def start_campaign(
    request: CampaignStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    orchestrator: CampaignOrchestrator = Depends(get_orchestrator),
):
    # Ownership: campaign resources must belong to the authenticated user.
    await get_project_or_403(request.project_id, current_user.id)
    await get_target_or_403(request.target_id, current_user.id)

    try:
        experiment_id = await orchestrator.initialize_experiment(
            project_id=request.project_id,
            target_id=request.target_id,
            name=request.name,
            attack_budget=request.attack_budget,
            exploration_ratio=request.exploration_ratio,
        )
    except Exception as exc:
        logger.error(
            "Failed to create campaign",
            extra={
                "event_name": "campaign.create_failed",
                "user_id": str(current_user.id),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to initialize campaign.")

    logger.info(
        "Campaign created",
        extra={
            "event_name": "campaign.created",
            "user_id": str(current_user.id),
            "campaign_id": str(experiment_id),
            "attack_budget": request.attack_budget,
        },
    )

    # Persistent Redis-backed queue when available; in-process fallback in dev.
    enqueued = await enqueue_campaign(experiment_id)

    if enqueued:
        message = "Campaign queued for execution."
    else:
        background_tasks.add_task(orchestrator.run_attack_loop, experiment_id)
        message = "Campaign started in process (Redis worker not configured)."

    return CampaignResponse(
        experiment_id=experiment_id,
        status="PENDING",
        message=message,
    )
