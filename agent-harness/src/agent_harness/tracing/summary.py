from __future__ import annotations

import json
from pathlib import Path

from agent_harness.domain.run import RunState
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import duration_ms


def write_result_summary(run: RunState, run_dir: Path, trace_path: Path) -> Path:
    """Write the final result.json summary for a completed, failed, or cancelled run."""
    result_path = run_dir / "result.json"
    completed = run.completed_at or run.updated_at
    payload = {
        "run_id": run.run_id,
        "status": run.status.value,
        "task": run.task,
        "workspace_root": str(run.workspace_root),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_ms": duration_ms(run.started_at, completed),
        "iteration_count": run.iteration,
        "model_call_count": run.model_call_count,
        "tool_call_count": run.tool_call_count,
        "usage": run.usage_total,
        "usage_scope": "turn",
        "context": {"last_input_tokens": run.usage_total.input_tokens, "estimated": False},
        "final_output": run.final_output,
        "error": run.error,
        "trace_path": trace_path,
        "agent_summary": run.agent_summary,
    }
    result_path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path
