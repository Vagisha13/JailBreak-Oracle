import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, Text, Float, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

from app.db.base import Base, BaseMixin


def utc_now():
    return datetime.now(timezone.utc)


class User(Base, BaseMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="researcher")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    projects: Mapped[List["Project"]] = relationship("Project", back_populates="owner")


class Project(Base, BaseMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    owner: Mapped["User"] = relationship("User", back_populates="projects")
    targets: Mapped[List["Target"]] = relationship("Target", back_populates="project")
    experiments: Mapped[List["Experiment"]] = relationship(
        "Experiment", back_populates="project"
    )


class Target(Base, BaseMixin):
    __tablename__ = "targets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    project: Mapped["Project"] = relationship("Project", back_populates="targets")
    experiments: Mapped[List["Experiment"]] = relationship(
        "Experiment", back_populates="target"
    )


class Experiment(Base, BaseMixin):
    __tablename__ = "experiments"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    attack_budget: Mapped[int] = mapped_column(Integer, default=50)
    exploration_ratio: Mapped[float] = mapped_column(Float, default=0.4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Worker liveness heartbeat (E-25): bumped while the campaign ACTUALLY runs.
    # Recovery uses this (not created_at) to decide staleness, so legitimately
    # long campaigns are never misjudged as abandoned.
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    project: Mapped["Project"] = relationship("Project", back_populates="experiments")
    target: Mapped["Target"] = relationship("Target", back_populates="experiments")
    attacks: Mapped[List["Attack"]] = relationship(
        "Attack", back_populates="experiment"
    )
    vulnerabilities: Mapped[List["Vulnerability"]] = relationship(
        "Vulnerability", back_populates="experiment"
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun", back_populates="experiment"
    )
    token_usage: Mapped[List["TokenUsage"]] = relationship(
        "TokenUsage", back_populates="experiment"
    )


class Attack(Base, BaseMixin):
    __tablename__ = "attacks"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), index=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True)
    # Mutation lineage: root attacks have NULL; specialised variants point at the
    # attack they evolved from. round_number records which campaign round the
    # attack was created in (mutations keep their parent's round).
    parent_attack_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attacks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    experiment: Mapped["Experiment"] = relationship(
        "Experiment", back_populates="attacks"
    )
    results: Mapped[List["AttackResult"]] = relationship(
        "AttackResult", back_populates="attack"
    )


class AttackResult(Base, BaseMixin):
    __tablename__ = "attack_results"

    attack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attacks.id", ondelete="CASCADE"), index=True
    )
    target_response: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    token_usage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    attack: Mapped["Attack"] = relationship("Attack", back_populates="results")


class Vulnerability(Base, BaseMixin):
    __tablename__ = "vulnerabilities"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    attack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attacks.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(50), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    verified_status: Mapped[str] = mapped_column(
        String(50), default="UNCONFIRMED", index=True
    )
    # Persisted independent-verifier fields (E-09). Populated when the
    # vulnerability is put through dual verification.
    verification_reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    remediation_guidance: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    verifier_confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    experiment: Mapped["Experiment"] = relationship(
        "Experiment", back_populates="vulnerabilities"
    )


class AgentRun(Base, BaseMixin):
    __tablename__ = "agent_runs"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    experiment: Mapped["Experiment"] = relationship(
        "Experiment", back_populates="agent_runs"
    )


class TokenUsage(Base, BaseMixin):
    __tablename__ = "token_usage"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    experiment: Mapped["Experiment"] = relationship(
        "Experiment", back_populates="token_usage"
    )


class AttackMutation(Base, BaseMixin):
    __tablename__ = "attack_mutations"

    attack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attacks.id", ondelete="CASCADE"), index=True
    )
    mutation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    mutated_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
