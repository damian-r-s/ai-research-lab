"""Base error types shared across modules. API maps these to HTTP responses in one place."""


class LabError(Exception):
    """Base class for expected, domain-level failures (not programming errors)."""

    code: str = "lab_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotAuthenticated(LabError):
    code = "not_authenticated"


class PermissionDenied(LabError):
    code = "permission_denied"


class TooManyAttempts(LabError):
    code = "too_many_attempts"


class ConflictError(LabError):
    code = "conflict"


class NotFound(LabError):
    code = "not_found"
