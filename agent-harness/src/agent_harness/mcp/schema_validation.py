from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError, validators

from agent_harness.domain.errors import ToolInputValidationError
from agent_harness.mcp.errors import MCPProtocolError


def check_mcp_schema(schema: dict[str, Any]) -> None:
    """Reject an invalid server-provided JSON Schema without exposing data values."""
    try:
        validators.validator_for(schema, default=Draft202012Validator).check_schema(schema)
    except SchemaError as exc:
        raise MCPProtocolError(f"Invalid MCP JSON Schema: {exc.message}") from exc


def validate_mcp_value(instance: Any, schema: dict[str, Any], *, label: str) -> None:
    """Validate MCP input or output with the schema-selected JSON Schema draft."""
    check_mcp_schema(schema)
    validator_class = validators.validator_for(schema, default=Draft202012Validator)
    errors = sorted(validator_class(schema, format_checker=FormatChecker()).iter_errors(instance), key=lambda item: (list(item.path), list(item.schema_path)))
    if not errors:
        return
    error: ValidationError = errors[0]
    path = ".".join(map(str, error.path)) or "$"
    raise ToolInputValidationError(f"MCP {label} schema validation failed at {path}: {error.message}", details={"path": list(error.path), "schema_path": list(error.schema_path), "validator": error.validator})
