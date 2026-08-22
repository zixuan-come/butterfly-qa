"""Validation helpers for structured agent artifacts."""

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class ArtifactValidationError(ValueError):
    """Raised when an agent artifact cannot enter the workflow."""


def validate_artifact(model_type: type[ModelT], payload: Any) -> ModelT:
    """Validate and return a typed artifact, with a stable workflow error."""

    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ArtifactValidationError(
            f"{model_type.__name__} validation failed: {details}"
        ) from exc
