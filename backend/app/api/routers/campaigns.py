import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.future import select
from sqlalchemy import func
from app.db.session import AsyncSessionLocal
from app.models.domain import User, Project, Target, Experiment, Vulnerability
from app.schemas.campaign import CampaignStartRequest, CampaignResponse
from app.services.campaign import CampaignOrchestrator
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.services.memory import MemoryService
from app.targets.factory import TargetFactory
from app.targets.embeddings import OpenAIEmbeddingProvider

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

def get_orchestrator() -> CampaignOrchestrator:
    target_provider = TargetFactory.get_provider("mock")
    embedding_provider = OpenAIEmbeddingProvider()
    
    attacker = AttackerAgent(provider=target_provider)
    evaluator = EvaluatorAgent(provider=target_provider)
    memory = MemoryService(embedding_provider=embedding_provider)
    
    return CampaignOrchestrator(
        attacker_agent=attacker,
        evaluator_agent=evaluator,
        memory_service=memory,
        target_provider=target_provider
    )

@router.get("/stats")
async def get_dashboard_stats():
    """Aggregate high-level metrics for the frontend dashboard."""
    async with AsyncSessionLocal() as session:
        active = (await session.execute(
            select(func.count()).select_from(Experiment).where(Experiment.status.in_(["PENDING", "RUNNING"]))
        )).scalar() or 0
        
        completed = (await session.execute(
            select(func.count()).select_from(Experiment).where(Experiment.status == "COMPLETED")
        )).scalar() or 0
        
        vulns = (await session.execute(
            select(func.count()).select_from(Vulnerability)
        )).scalar() or 0
        
        return {
            "active_campaigns": active,
            "completed_reports": completed,
            "total_vulnerabilities": vulns
        }

@router.post("/setup-demo")
async def setup_demo_environment():
    async with AsyncSessionLocal() as session:
        user = User(email=f"demo_{uuid.uuid4().hex[:6]}@oracle.sec", hashed_password="pw")
        session.add(user)
        await session.flush()
        
        project = Project(name="Demo Web Project", owner_id=user.id)
        session.add(project)
        await session.flush()
        
        target = Target(project_id=project.id, name="Demo Mock Target", provider_type="mock")
        session.add(target)
        await session.commit()
        
        return {
            "project_id": str(project.id),
            "target_id": str(target.id),
            "message": "Demo environment ready!"
        }

@router.get("", response_model=list[dict])
async def list_campaigns():
    async with AsyncSessionLocal() as session:
        stmt = select(Experiment).order_by(Experiment.created_at.desc())
        experiments = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(e.id),
                "name": e.name,
                "status": e.status,
                "attack_budget": e.attack_budget,
                "created_at": e.created_at.isoformat() if e.created_at else None
            }
            for e in experiments
        ]

@router.post("/start", response_model=CampaignResponse)
async def start_campaign(
    request: CampaignStartRequest,
    background_tasks: BackgroundTasks,
    orchestrator: CampaignOrchestrator = Depends(get_orchestrator)
):
    try:
        experiment_id = await orchestrator.initialize_experiment(
            project_id=request.project_id,
            target_id=request.target_id,
            name=request.name,
            attack_budget=request.attack_budget,
            exploration_ratio=request.exploration_ratio
        )
        
        background_tasks.add_task(orchestrator.run_attack_loop, experiment_id)
        
        return CampaignResponse(
            experiment_id=experiment_id,
            status="RUNNING",
            message="Campaign initialized and attack loop started in background."
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))