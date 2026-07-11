from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUBAGENT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "evidence": {"type": "array"},
        "unresolved_questions": {"type": "array"},
        "confidence": {"type": "number"},
        "structured_data": {"type": "object"},
    },
    "required": ["summary", "evidence", "unresolved_questions", "confidence"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class OutputSchemaRegistry:
    """Registry of structured output schemas that agent definitions may reference."""

    _schemas: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(self, schema_id: str, schema: dict[str, Any]) -> None:
        """Register one output schema by stable id."""
        if schema_id in self._schemas:
            raise ValueError(f"Output schema already registered: {schema_id}")
        self._schemas[schema_id] = schema

    def get(self, schema_id: str) -> dict[str, Any]:
        """Return one registered output schema by id."""
        try:
            return self._schemas[schema_id]
        except KeyError as exc:
            raise ValueError(f"Unknown output schema: {schema_id}") from exc

    def has(self, schema_id: str) -> bool:
        """Return whether an output schema id exists."""
        return schema_id in self._schemas


def create_default_output_registry() -> OutputSchemaRegistry:
    """Create the built-in output schema registry for phase 2 child agents."""
    registry = OutputSchemaRegistry()
    registry.register("subagent_result", SUBAGENT_RESULT_SCHEMA)
    return registry
