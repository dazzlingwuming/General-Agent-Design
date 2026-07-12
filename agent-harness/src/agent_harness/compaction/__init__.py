"""Idle-only context compaction that never deletes canonical rollout history."""

from agent_harness.compaction.service import CompactionRecord, CompactionService

__all__ = ["CompactionRecord", "CompactionService"]
