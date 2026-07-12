from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_harness.checkpoints.models import CheckpointEnvelope, ResumePoint
from agent_harness.domain.tools import ToolRecoveryPolicy


class RecoveryDisposition(StrEnum):
    """Action selected without consulting the model."""

    CONTINUE = "continue"
    RETRY = "retry"
    SYNTHESIZE = "synthesize"
    WAIT_APPROVAL = "wait_approval"
    MANUAL = "manual"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """Deterministic plan for one latest committed checkpoint."""

    checkpoint_id: str
    disposition: RecoveryDisposition
    resume_point: ResumePoint
    reason: str


class RecoveryCoordinator:
    """Convert durable boundaries into conservative restart decisions."""

    def plan(self, checkpoint: CheckpointEnvelope, tool_policy: ToolRecoveryPolicy | None = None) -> RecoveryPlan:
        """Plan recovery and block uncertain side effects unless policy proves safety."""
        point = checkpoint.resume_point
        if point == ResumePoint.TERMINAL:
            return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.TERMINAL, point, "turn already terminal")
        if point == ResumePoint.WAITING_APPROVAL:
            return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.WAIT_APPROVAL, point, "original approval remains pending")
        if point == ResumePoint.TOOL_IN_FLIGHT:
            if tool_policy == ToolRecoveryPolicy.RETRY_SAFE:
                return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.RETRY, point, "read-only tool may create a new attempt")
            if tool_policy in {ToolRecoveryPolicy.VERIFY_THEN_RETRY, ToolRecoveryPolicy.VERIFY_THEN_SYNTHESIZE}:
                return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.MANUAL, point, "tool postcondition must be reconciled first")
            return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.MANUAL, ResumePoint.RECOVERY_REQUIRED, "unknown side effect must not be replayed")
        if point == ResumePoint.MODEL_IN_FLIGHT:
            return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.RETRY, point, "model response was not committed")
        return RecoveryPlan(checkpoint.checkpoint_id, RecoveryDisposition.CONTINUE, point, "resume from committed logical boundary")
