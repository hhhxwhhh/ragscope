class AppError(Exception):
    """Base class for expected application errors."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"

    def __init__(self, message: str = "未认证") -> None:
        super().__init__(message)


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class QuotaExceededError(AppError):
    status_code = 429
    code = "quota_exceeded"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
