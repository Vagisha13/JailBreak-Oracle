from app.services.campaign import CampaignOrchestrator
from app.services.factory import build_campaign_orchestrator


def get_campaign_orchestrator() -> CampaignOrchestrator:
    """Dependency injection provider for the Campaign Orchestrator."""
    return build_campaign_orchestrator(include_verifier=True)
