from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cutflow.dsl.models import CutFlowScript


class CutFlowValidationError(ValueError):
    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def validate_cutflow_dict(data: dict[str, Any]) -> tuple[bool, list[dict[str, Any]] | None]:
    try:
        CutFlowScript.model_validate(data)
        return True, None
    except ValidationError as e:
        return False, e.errors()


def validate_cutflow_json(json_str: str) -> tuple[bool, list[dict[str, Any]] | None]:
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, [{"loc": ("json",), "msg": f"Invalid JSON: {e}", "type": "json_invalid"}]
    return validate_cutflow_dict(data)


def validate_cutflow_file(path: str | Path) -> tuple[bool, list[dict[str, Any]] | None]:
    p = Path(path)
    if not p.exists():
        return False, [{"loc": ("file",), "msg": f"File not found: {p}", "type": "missing"}]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, [{"loc": ("json",), "msg": f"Invalid JSON: {e}", "type": "json_invalid"}]
    return validate_cutflow_dict(data)


def assert_valid(data: dict[str, Any] | str) -> CutFlowScript:
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise CutFlowValidationError(f"Invalid JSON: {e}") from e
        data = parsed
    try:
        return CutFlowScript.model_validate(data)
    except ValidationError as e:
        raise CutFlowValidationError(f"Validation failed: {e}", e.errors()) from e


def get_json_schema() -> dict[str, Any]:
    return CutFlowScript.model_json_schema()
