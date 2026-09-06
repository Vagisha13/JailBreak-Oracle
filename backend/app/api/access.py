"""Resource ownership helpers.

All ownership checks run at the service/API layer (server-side) so that
frontend-only protection can never be bypassed. Unauthenticated callers are
rejected by ``get_current_user`` (401); authenticated callers that do not own
the resource receive a 403; missing resources receive a 404.
"""
import uuid
from typing import Optional

from sqlalchemy.future import select

from app.core.errors import AuthorizationError, NotFoundError
from app.db.session import AsyncSessionLocal
from app.models.domain import (
    Experiment,
    Project,
    Target,
    Vulnerability,
)


async def get_project_or_403(project_id: uuid.UUID, user_id: uuid.UUID) -> Project:
    async with AsyncSessionLocal() as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalars().first()
        if project is None:
            raise NotFoundError("Project not found.")
        if project.owner_id != user_id:
            raise AuthorizationError("You do not have access to this project.")
        return project


async def get_target_or_403(target_id: uuid.UUID, user_id: uuid.UUID) -> Target:
    async with AsyncSessionLocal() as session:
        target = (
            await session.execute(select(Target).where(Target.id == target_id))
        ).scalars().first()
        if target is None:
            raise NotFoundError("Target not found.")
        project = (
            await session.execute(select(Project).where(Project.id == target.project_id))
        ).scalars().first()
        if project is None or project.owner_id != user_id:
            raise AuthorizationError("You do not have access to this target.")
        return target


async def get_experiment_or_403(
    experiment_id: uuid.UUID, user_id: uuid.UUID
) -> Experiment:
    async with AsyncSessionLocal() as session:
        experiment = (
            await session.execute(
                select(Experiment).where(Experiment.id == experiment_id)
            )
        ).scalars().first()
        if experiment is None:
            raise NotFoundError("Experiment not found.")
        project = (
            await session.execute(
                select(Project).where(Project.id == experiment.project_id)
            )
        ).scalars().first()
        if project is None or project.owner_id != user_id:
            raise AuthorizationError("You do not have access to this campaign.")
        return experiment


async def get_vulnerability_or_403(
    vulnerability_id: uuid.UUID, user_id: uuid.UUID
) -> Vulnerability:
    async with AsyncSessionLocal() as session:
        vuln = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.id == vulnerability_id)
            )
        ).scalars().first()
        if vuln is None:
            raise NotFoundError("Vulnerability not found.")
        experiment = (
            await session.execute(
                select(Experiment).where(Experiment.id == vuln.experiment_id)
            )
        ).scalars().first()
        if experiment is None:
            raise NotFoundError("Vulnerability not found.")
        project = (
            await session.execute(
                select(Project).where(Project.id == experiment.project_id)
            )
        ).scalars().first()
        if project is None or project.owner_id != user_id:
            raise AuthorizationError("You do not have access to this vulnerability.")
        return vuln


async def get_user_project_ids(user_id: uuid.UUID) -> list[uuid.UUID]:
    """Return all project IDs owned by the given user."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(Project.id).where(Project.owner_id == user_id))
        return [row[0] for row in rows.all()]


async def experiment_belongs_to_user(
    experiment_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Experiment]:
    return await get_experiment_or_403(experiment_id, user_id)
