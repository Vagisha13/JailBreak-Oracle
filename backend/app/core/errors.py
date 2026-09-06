from typing import Any, Dict, Optional


class APIError(Exception):
    """Base class for all domain API errors returned to clients."""

    status_code = 500
    code = "internal_error"

    def __init__(self, detail: str = "Internal server error", code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code

    def to_dict(self) -> Dict[str, Any]:
        return {"detail": self.detail, "code": self.code}

    def headers(self) -> Dict[str, str]:
        return {}


class AuthenticationError(APIError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(APIError):
    status_code = 403
    code = "forbidden"


class NotFoundError(APIError):
    status_code = 404
    code = "not_found"


class ConflictError(APIError):
    status_code = 409
    code = "conflict"


class InvalidInputError(APIError):
    status_code = 422
    code = "validation_error"


class RateLimitedError(APIError):
    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(
        self,
        detail: str = "Rate limit exceeded. Please retry later.",
        code: Optional[str] = None,
        retry_after: Optional[int] = None,
    ):
        super().__init__(detail, code or self.code)
        self.retry_after = retry_after

    def headers(self) -> Dict[str, str]:
        if self.retry_after is not None:
            return {"Retry-After": str(self.retry_after)}
        return {}


class ProviderError(APIError):
    status_code = 502
    code = "provider_error"
