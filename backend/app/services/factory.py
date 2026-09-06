"""Composition root for building the campaign orchestrator.

Single source of truth so every execution path wires the exact same agents:
FastAPI routes (``app/api/...``), the in-process fallback (no Redis), and the
durable worker (``app/worker.py``). Before this module existed the router wired
an orchestrator *without* a verifier, so dual verification silently never ran
on that path.
"""
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.agents.verifier import VerifierAgent
from app.core.config import settings
from app.services.campaign import CampaignOrchestrator
from app.services.memory import MemoryService
from app.targets.embeddings import EmbeddingProvider, MockEmbeddingProvider
from app.targets.factory import TargetFactory


def build_embedding_provider() -> EmbeddingProvider:
    """Build the configured embedding provider (mock is the CI-safe default)."""
    if settings.EMBEDDING_PROVIDER == "openai":
        from app.targets.embeddings import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(model_name=settings.EMBEDDING_MODEL)
    return MockEmbeddingProvider()


def build_campaign_orchestrator(include_verifier: bool = True) -> CampaignOrchestrator:
    """Build the campaign orchestrator shared by router, fallback, and worker."""
    attacker_provider = TargetFactory.get_provider(
        settings.ATTACKER_PROVIDER, default_model=settings.ATTACKER_MODEL
    )
    evaluator_provider = TargetFactory.get_provider(
        settings.EVALUATOR_PROVIDER, default_model=settings.EVALUATOR_MODEL
    )
    verifier_provider = TargetFactory.get_provider(
        settings.VERIFIER_PROVIDER, default_model=settings.VERIFIER_MODEL
    )

    memory_service = MemoryService(build_embedding_provider())
    attacker_agent = AttackerAgent(
        provider=attacker_provider, memory_service=memory_service
    )
    evaluator_agent = EvaluatorAgent(provider=evaluator_provider)

    if include_verifier:
        return CampaignOrchestrator(
            attacker_agent=attacker_agent,
            evaluator_agent=evaluator_agent,
            memory_service=memory_service,
            verifier_agent=VerifierAgent(provider=verifier_provider),
        )
    return CampaignOrchestrator(
        attacker_agent=attacker_agent,
        evaluator_agent=evaluator_agent,
        memory_service=memory_service,
    )
