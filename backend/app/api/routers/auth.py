import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from app.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_roles,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import User
from sqlalchemy.future import select

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])


class UserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str
    role: str


class UserRolePatch(BaseModel):
    role: Literal["researcher", "admin"]


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate):
    email = data.email.strip().lower()
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalars().first():
            raise HTTPException(status_code=400, detail="Email already registered")
        role = (
            "admin"
            if email in settings.bootstrap_admin_email_list
            else "researcher"
        )
        user = User(email=email, hashed_password=hash_password(data.password), role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        token = create_access_token({"sub": str(user.id)})

    logger.info(
        "User registered",
        extra={
            "event_name": "auth.register",
            "user_id": str(user.id),
            "role": role,
        },
    )
    return Token(access_token=token)


@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.email == form.username.strip().lower())
        )
        user = result.scalars().first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id)})
    logger.info(
        "User logged in",
        extra={"event_name": "auth.login", "user_id": str(user.id)},
    )
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user.id), email=current_user.email, role=current_user.role
    )


@router.get("/users", response_model=list[UserResponse])
async def list_users(current_user: User = Depends(require_roles("admin"))):
    """Admin-only directory of all registered users."""
    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(select(User).order_by(User.created_at))
        ).scalars().all()
    return [
        UserResponse(id=str(u.id), email=u.email, role=u.role) for u in users
    ]


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def set_user_role(
    user_id: uuid.UUID,
    body: UserRolePatch,
    current_user: User = Depends(require_roles("admin")),
):
    """Admin-only role assignment. Admins cannot demote themselves."""
    if user_id == current_user.id and body.role != "admin":
        raise HTTPException(
            status_code=400, detail="Admins cannot demote themselves."
        )
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalars().first()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        user.role = body.role
        await session.commit()
        target = UserResponse(
            id=str(user.id), email=user.email, role=user.role
        )

    logger.info(
        "User role updated",
        extra={
            "event_name": "authx.role_updated",
            "actor_id": str(current_user.id),
            "target_id": str(user_id),
            "role": body.role,
        },
    )
    return target
