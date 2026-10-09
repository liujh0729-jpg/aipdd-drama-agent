"""Shared validation for teacher-produced sketch edit labels JSONL."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import SketchEditResult

HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
MAX_EDIT_INSTRUCTION_LENGTH = 900


class SketchEditLabelRow(BaseModel):
    project_dir: str
    episode_num: int
    beat_number: int
    execution_mode: str = "polish"
    sketch_path: str
    beat: dict[str, Any]
    sketch_colors: list[dict[str, Any]]
    result: SketchEditResult
    raw_text: str = ""


class LabelsValidationError(ValueError):
    """Raised when labels.jsonl contains invalid rows."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        errors = payload.get("errors") or []
        first_error = errors[0] if errors else "unknown validation error"
        count = int(payload.get("error_count") or len(errors) or 1)
        super().__init__(f"labels.jsonl validation failed ({count} errors): {first_error}")


def _semantic_checks(row: SketchEditLabelRow, line_number: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    result = row.result
    execution_mode = (row.execution_mode or "").strip().lower()

    if execution_mode != "polish":
        errors.append(f"line {line_number}: execution_mode must be polish")

    if result.decision.value != "revise":
        errors.append(f"line {line_number}: labels.jsonl may contain revise rows only")
    if result.main_problem is None:
        errors.append(f"line {line_number}: revise rows must have main_problem")
    if not result.edit_instruction.strip():
        errors.append(f"line {line_number}: revise rows must have non-empty edit_instruction")
    mentioned_identities = {
        str(color_row.get("identity") or "").strip()
        for color_row in (row.sketch_colors or [])
        if str(color_row.get("identity") or "").strip()
        and str(color_row.get("identity") or "").strip() in result.edit_instruction
    }
    color_correction_required = (
        result.main_problem is not None
        and result.main_problem.value == "identity_color_mismatch"
    )
    if (color_correction_required or mentioned_identities) and not HEX_RE.search(
        result.edit_instruction
    ):
        errors.append(
            f"line {line_number}: revise color-sensitive edit_instruction must include at least one #RRGGBB color"
        )
    if len(mentioned_identities) > 2:
        errors.append(
            f"line {line_number}: revise edit_instruction may mention at most 2 named identities"
        )

    if len(result.edit_instruction) > MAX_EDIT_INSTRUCTION_LENGTH:
        errors.append(
            f"line {line_number}: edit_instruction exceeds "
            f"{MAX_EDIT_INSTRUCTION_LENGTH} characters"
        )

    if result.confidence < 0.5:
        warnings.append(f"line {line_number}: low_quality (confidence < 0.5)")

    return errors, warnings


def validate_labels_jsonl(path: Path) -> dict[str, Any]:
    """Validate teacher-produced labels.jsonl and return a summary payload."""

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    row_count = 0
    errors: list[str] = []
    warnings: list[str] = []
    low_quality_count = 0

    with resolved.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON ({exc})")
                continue
            try:
                row = SketchEditLabelRow.model_validate(payload)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"line {line_number}: schema validation failed ({exc})")
                continue
            row_errors, row_warnings = _semantic_checks(row, line_number)
            errors.extend(row_errors)
            warnings.extend(row_warnings)
            low_quality_count += sum(1 for message in row_warnings if "low_quality" in message)
            row_count += 1

    result_payload: dict[str, Any] = {
        "labels_jsonl": str(resolved),
        "row_count": row_count,
        "valid": not errors,
        "warning_count": len(warnings),
        "low_quality_count": low_quality_count,
        "warnings": warnings,
    }
    if errors:
        result_payload["error_count"] = len(errors)
        result_payload["errors"] = errors
        raise LabelsValidationError(result_payload)
    return result_payload
