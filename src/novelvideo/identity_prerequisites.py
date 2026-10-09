"""Canonical prerequisites for episode identity planning."""

from __future__ import annotations

from collections.abc import Iterable

IDENTITY_CHARACTERS_REQUIRED_CODE = "IDENTITY_CHARACTERS_REQUIRED"
IDENTITY_CHARACTERS_REQUIRED_MESSAGE = "请先从知识图谱构建角色，再规划身份"
IDENTITY_CHARACTERS_BUILDING_CODE = "IDENTITY_CHARACTERS_BUILDING"
IDENTITY_CHARACTERS_BUILDING_MESSAGE = "角色正在构建，请完成后再规划身份"


class IdentityPlanningPrerequisiteError(ValueError):
    """Base class for user-actionable identity planning prerequisites."""

    error_code = "IDENTITY_PLANNING_PREREQUISITE"


class IdentityCharactersRequiredError(IdentityPlanningPrerequisiteError):
    error_code = IDENTITY_CHARACTERS_REQUIRED_CODE

    def __init__(self) -> None:
        super().__init__(IDENTITY_CHARACTERS_REQUIRED_MESSAGE)


class IdentityCharactersBuildingError(IdentityPlanningPrerequisiteError):
    error_code = IDENTITY_CHARACTERS_BUILDING_CODE

    def __init__(self) -> None:
        super().__init__(IDENTITY_CHARACTERS_BUILDING_MESSAGE)


def require_identity_characters(characters: Iterable[object]) -> None:
    """Reject identity planning until the durable character library is ready."""
    if not any(True for _ in characters):
        raise IdentityCharactersRequiredError()


def identity_prerequisite_response(
    error: IdentityPlanningPrerequisiteError,
) -> dict[str, str | bool]:
    return {
        "ok": False,
        "code": error.error_code,
        "error": str(error),
    }
