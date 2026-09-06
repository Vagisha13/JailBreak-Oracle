import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List


class SeverityBreakdown(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class StrategyPerformance(BaseModel):
    strategy_name: str
    total_attempts: int
    successful_jailbreaks: int
    success_rate_percentage: float


class VulnerabilitySummaryItem(BaseModel):
    vulnerability_id: uuid.UUID
    category: str
    severity: str
    confidence: float
    reasoning: str
    verified_status: str


class RedTeamReport(BaseModel):
    experiment_id: uuid.UUID
    experiment_name: str
    target_id: uuid.UUID
    target_name: str
    generated_at: datetime
    overall_risk_score: float = Field(
        ..., description="Calculated risk score from 0.0 (Safe) to 100.0 (Critical)"
    )
    total_attacks_executed: int
    jailbreak_success_rate: float
    severity_breakdown: SeverityBreakdown
    strategy_performance: List[StrategyPerformance]
    top_vulnerabilities: List[VulnerabilitySummaryItem]
    remediation_summary: List[str]
