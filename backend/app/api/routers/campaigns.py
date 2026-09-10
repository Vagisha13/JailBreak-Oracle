import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from sqlalchemy.future import select
from sqlalchemy import func

from app.core.auth import get_current_user
from app.core.config import settings
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
    AgentRun,
)
from app.schemas.campaign import CampaignStartRequest, CampaignResponse
from app.services.factory import build_campaign_orchestrator
from app.services.campaign import CampaignOrchestrator
from app.services.queue import enqueue_campaign
from app.api.access import get_target_or_403, get_project_or_403, get_experiment_or_403

logger = get_logger("api.campaigns")

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def _aware(dt: datetime) -> datetime:
    """Normalize naive DB timestamps to UTC (SQLite drivers drop tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def campaign_is_stale(experiment: Experiment) -> bool:
    """Mirror the worker's liveness semantics (E-25): only runnable campaigns
    can be stale, judged on the heartbeat (legacy NULL falls back to
    ``created_at``) vs. the campaign timeout."""
    if experiment.status not in ("PENDING", "RUNNING"):
        return False
    last_activity = experiment.heartbeat_at or experiment.created_at
    if last_activity is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.CAMPAIGN_TIMEOUT_SECONDS
    )
    return _aware(last_activity) < cutoff


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

    # Ledger + progress rollup (E-12/E-21): everything the dashboard displays —
    # tokens, cost, per-role / per-model usage, current round — is derived from
    # the persisted records, never synthesized client-side.
    async with AsyncSessionLocal() as session:
        usage_stmt = (
            select(
                func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
            ).where(TokenUsage.experiment_id == experiment.id)
        )
        usage_row = (await session.execute(usage_stmt)).one()

        per_role_rows = (
            await session.execute(
                select(
                    TokenUsage.role,
                    func.count(TokenUsage.id),
                    func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
                )
                .where(TokenUsage.experiment_id == experiment.id)
                .group_by(TokenUsage.role)
                .order_by(TokenUsage.role)
            )
        ).all()
        per_model_rows = (
            await session.execute(
                select(
                    func.coalesce(TokenUsage.model, "unknown"),
                    func.count(TokenUsage.id),
                    func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
                )
                .where(TokenUsage.experiment_id == experiment.id)
                .group_by(TokenUsage.model)
                .order_by(TokenUsage.model)
            )
        ).all()

        current_round = (
            await session.execute(
                select(func.coalesce(func.max(Attack.round_number), 0)).where(
                    Attack.experiment_id == experiment.id
                )
            )
        ).scalar() or 0

    prompt_tokens = usage_row[0]
    completion_tokens = usage_row[1]
    total_cost = round(float(usage_row[2]), 4)
    max_cost = (
        experiment.max_cost_usd
        if experiment.max_cost_usd is not None
        else settings.MAX_CAMPAIGN_COST
    )

    # Failure reason for FAILED campaigns, derived from the orchestrator's
    # persisted <reason, error_type, error_message> telemetry (never synthesized).
    failure_reason = None
    if experiment.status == "FAILED":
        failure_stmt = (
            select(AgentRun.state_json)
            .where(
                AgentRun.experiment_id == experiment.id,
                AgentRun.agent_type == "campaign",
            )
            .order_by(AgentRun.created_at.desc())
        )
        for (state,) in (await session.execute(failure_stmt)).all():
            if isinstance(state, dict) and state.get("status") == "FAILED":
                reason = state.get("reason")
                if reason:
                    error_type = state.get("error_type")
                    message = state.get("error_message")
                    detail = f" ({error_type}): {message}" if error_type else ""
                    failure_reason = f"{reason}{detail}"
                break

    per_role: dict[str, dict] = {}
    for role, calls, pt, ct, cost in per_role_rows:
        per_role[role] = {
            "calls": calls,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "cost_usd": round(float(cost), 4),
        }

    per_model: dict[str, dict] = {}
    for model, calls, pt, ct, cost in per_model_rows:
        per_model[model] = {
            "calls": calls,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "cost_usd": round(float(cost), 4),
        }

    budget = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "total_cost_usd": total_cost,
        "max_cost_usd": experiment.max_cost_usd,
        "remaining_cost_usd": round(max(0.0, max_cost - total_cost), 4),
        "per_role": per_role,
        "per_model": per_model,
    }

    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status,
        "attack_budget": experiment.attack_budget,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
        "finished_at": experiment.finished_at.isoformat() if experiment.finished_at else None,
        "current_round": current_round,
        "total_rounds": experiment.attack_budget,
        "heartbeat_at": experiment.heartbeat_at.isoformat() if experiment.heartbeat_at else None,
        "is_stale": campaign_is_stale(experiment),
        "resumable": experiment.status in ("PENDING", "RUNNING"),
        "failure_reason": failure_reason,
        "budget": budget,
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


def _serialize_finding(vulnerability: Vulnerability) -> dict:
    """Full finding payload backing the dashboard's confirmed-findings view."""
    return {
        "id": str(vulnerability.id),
        "experiment_id": str(vulnerability.experiment_id),
        "attack_id": str(vulnerability.attack_id),
        "category": vulnerability.category,
        "severity": vulnerability.severity,
        "confidence": vulnerability.confidence,
        "reasoning": vulnerability.reasoning,
        "verified_status": vulnerability.verified_status,
        "verification_reasoning": vulnerability.verification_reasoning,
        "remediation_guidance": vulnerability.remediation_guidance,
        "verifier_confidence": vulnerability.verifier_confidence,
        "verified_at": (
            vulnerability.verified_at.isoformat() if vulnerability.verified_at else None
        ),
        "created_at": (
            vulnerability.created_at.isoformat() if vulnerability.created_at else None
        ),
        "evaluator_evidence": vulnerability.evaluator_evidence or [],
        "verifier_evidence": vulnerability.verifier_evidence or [],
    }


@router.get("/{experiment_id}/findings")
async def get_campaign_findings(
    experiment_id: uuid.UUID, current_user: User = Depends(get_current_user)
):
    """All recorded vulns for a campaign, most severe first (E-21 findings list)."""
    await get_experiment_or_403(experiment_id, current_user.id)

    async with AsyncSessionLocal() as session:
        stmt = select(Vulnerability).where(
            Vulnerability.experiment_id == experiment_id
        )
        vulnerabilities = (await session.execute(stmt)).scalars().all()

    def _sort_key(v: Vulnerability):
        return (
            -_SEVERITY_RANK.get((v.severity or "").upper(), 0),
            v.created_at or datetime.min.replace(tzinfo=timezone.utc),
        )

    return [
        _serialize_finding(v) for v in sorted(vulnerabilities, key=_sort_key)
    ]


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
