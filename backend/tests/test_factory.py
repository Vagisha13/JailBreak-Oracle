"""Regression tests for the shared orchestrator composition root (E-05).

Every execution path must wire the same agents. Before the factory existed the
campaigns router built an orchestrator WITHOUT a verifier, so dual verification
silently never ran on the in-process fallback path.
"""
import pytest

from app.services.factory import build_campaign_orchestrator
from app.api.routers.campaigns import get_orchestrator
from app.worker import build_orchestrator as build_worker_orchestrator


@pytest.mark.asyncio
async def test_factory_wires_verifier(mock_llm_providers):
    orchestrator = build_campaign_orchestrator()
    assert orchestrator.verifier_agent is not None
    assert orchestrator.verification_service is not None


@pytest.mark.asyncio
async def test_factory_can_omit_verifier(mock_llm_providers):
    orchestrator = build_campaign_orchestrator(include_verifier=False)
    assert orchestrator.verifier_agent is None
    assert orchestrator.verification_service is None


@pytest.mark.asyncio
async def test_campaigns_router_path_has_verifier(mock_llm_providers):
    """The in-process fallback path must run dual verification."""
    orchestrator = get_orchestrator()
    assert orchestrator.verification_service is not None


@pytest.mark.asyncio
async def test_worker_path_has_verifier(mock_llm_providers):
    orchestrator = build_worker_orchestrator()
    assert orchestrator.verification_service is not None