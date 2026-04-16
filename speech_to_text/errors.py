from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AppError(Exception):
    message: str
    status_code: int = 500
    error_type: str = "server_error"
    code: Optional[str] = None

    def __str__(self) -> str:
        return self.message


class ValidationError(AppError):
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_type="invalid_request_error",
            code=code,
        )


class AuthenticationError(AppError):
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(
            message=message,
            status_code=401,
            error_type="invalid_request_error",
            code=code,
        )


class UploadTooLargeError(AppError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            status_code=413,
            error_type="invalid_request_error",
            code="file_too_large",
        )


class ConfigurationError(AppError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            status_code=500,
            error_type="server_error",
            code="server_misconfigured",
        )


class UpstreamServiceError(AppError):
    def __init__(self, message: str, code: Optional[str] = "upstream_error"):
        super().__init__(
            message=message,
            status_code=502,
            error_type="upstream_error",
            code=code,
        )


class UpstreamTimeoutError(AppError):
    def __init__(self, message: str = "上游服务请求超时"):
        super().__init__(
            message=message,
            status_code=504,
            error_type="upstream_timeout",
            code="upstream_timeout",
        )


class ServiceBusyError(AppError):
    def __init__(self, message: str, code: Optional[str] = "service_busy"):
        super().__init__(
            message=message,
            status_code=429,
            error_type="rate_limit_error",
            code=code,
        )


class OperationCancelled(Exception):
    pass
