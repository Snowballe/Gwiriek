"""Errors raised by services, mapped to exit codes by the CLI and to tool errors by MCP."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError


class ServiceError(Exception):
    """Base class for expected business errors (never a bug)."""

    code = "error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the error."""
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(ServiceError):
    """The referenced object does not exist."""

    code = "not_found"


class AmbiguousReferenceError(ServiceError):
    """A reference matches several objects."""

    code = "ambiguous_reference"


class InvalidDataError(ServiceError):
    """Input data breaks a validation rule."""

    code = "invalid"

    @classmethod
    def from_django(cls, exc: DjangoValidationError) -> "InvalidDataError":
        """Convert a Django ``ValidationError`` into an ``InvalidDataError``."""
        if hasattr(exc, "error_dict"):
            fields = {
                field: [str(m) for e in errors for m in e.messages]
                for field, errors in exc.error_dict.items()
            }
        else:
            fields = {"__all__": [str(m) for m in exc.messages]}
        message = "; ".join(
            msg if field == "__all__" else f"{field}: {msg}"
            for field, msgs in fields.items()
            for msg in msgs
        )
        return cls(message, {"fields": fields})


class OperationNotAllowedError(ServiceError):
    """The operation is forbidden in the object's current state."""

    code = "not_allowed"
