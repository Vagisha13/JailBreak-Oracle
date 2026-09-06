from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.agents.verifier import VerifierAgent
from app.services.memory import MemoryService
from app.services.campaign import CampaignOrchestrator
from app.targets.factory import TargetFactory
from app.targets.embeddings import EmbeddingProvider, MockEmbeddingProvider
from app.core.config import settings


def get_campaign_orchestrator() -> CampaignOrchestrator:
    """Dependency injection provider for the Campaign Orchestrator."""

    # Attacker
    attacker_provider = TargetFactory.get_provider(
        settings.ATTACKER_PROVIDER, default_model=settings.ATTACKER_MODEL
    )

    # Evaluator
    evaluator_provider = TargetFactory.get_provider(
        settings.EVALUATOR_PROVIDER, default_model=settings.EVALUATOR_MODEL
    )

    # Verifier
    verifier_provider = TargetFactory.get_provider(
        settings.VERIFIER_PROVIDER, default_model=settings.VERIFIER_MODEL
    )

    # Embedding provider
    embed_provider: EmbeddingProvider = MockEmbeddingProvider()
    if settings.EMBEDDING_PROVIDER == "openai":
        from app.targets.embeddings import OpenAIEmbeddingProvider
        embed_provider = OpenAIEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL
        )

    # Memory service
    memory_service = MemoryService(embed_provider)

    # Agents
    attacker_agent = AttackerAgent(
        provider=attacker_provider,
        memory_service=memory_service,
    )
    evaluator_agent = EvaluatorAgent(provider=evaluator_provider)
    verifier_agent = VerifierAgent(provider=verifier_provider)

    return CampaignOrchestrator(
        attacker_agent=attacker_agent,
        evaluator_agent=evaluator_agent,
        memory_service=memory_service,
        verifier_agent=verifier_agent,
    )
