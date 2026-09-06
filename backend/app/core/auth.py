from datetime import datetime, timedelta, timezone
import uuid
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from sqlalchemy.future import select

from app.core.config import settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import User

logger = get_logger("auth")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> User:
    if token is None:
        raise AuthenticationError("Not authenticated")

    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.JWT_ALGORITHM]
        )
        raw_user_id: Optional[str] = payload.get("sub")
        if raw_user_id is None:
            raise AuthenticationError("Invalid token")
        user_id = uuid.UUID(raw_user_id)
    except JWTError as exc:
        logger.warning(
            "JWT decode failed", extra={"event_name": "auth.invalid_token", "error_type": type(exc).__name__}
        )
        raise AuthenticationError("Invalid or expired token")
    except (ValueError, TypeError):
        raise AuthenticationError("Invalid token")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()

    if user is None:
        raise AuthenticationError("User not found")
    return user


def require_roles(*roles: str):
    """Dependency factory: resolve the caller and require one of the given roles.

    Usage: ``user: User = Depends(require_roles("admin"))``. Non-matching but
    authenticated users get a 403; unauthenticated callers still get a 401.
    """

    async def _require(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            logger.warning(
                "Role check denied",
                extra={
                    "event_name": "authx.role_denied",
                    "user_id": str(current_user.id),
                    "role": current_user.role,
                    "required_roles": list(roles),
                },
            )
            raise AuthorizationError("Insufficient permissions.")
        return current_user

    return _require
