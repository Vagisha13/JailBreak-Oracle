from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.services.memory import MemoryService
from app.services.campaign import CampaignOrchestrator
from app.targets.embeddings import MockEmbeddingProvider

from tests.test_campaign import (
    MockAttackerProvider,
    MockEvaluatorProvider,
)


def get_campaign_orchestrator() -> CampaignOrchestrator:
    """Dependency injection provider for the Campaign Orchestrator."""

    # Embedding provider
    embed_provider = MockEmbeddingProvider()

    # Memory service
    memory_service = MemoryService(embed_provider)

    # Attacker
    attacker_provider = MockAttackerProvider()

    attacker_agent = AttackerAgent(
        provider=attacker_provider,
        memory_service=memory_service,
    )

    # Evaluator
    evaluator_provider = MockEvaluatorProvider()

    evaluator_agent = EvaluatorAgent(
        provider=evaluator_provider,
    )

    # Orchestrator
    return CampaignOrchestrator(
        attacker_agent=attacker_agent,
        evaluator_agent=evaluator_agent,
        memory_service=memory_service,
    )
