from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_harness.agents.registry import create_default_agent_registry
from agent_harness.config import HarnessConfig
from agent_harness.context.builder import ContextBuilder
from agent_harness.context.prompt import SYSTEM_PROMPT
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import ModelProvider
from agent_harness.domain.run import RunState
from agent_harness.runtime.agent_loop import AgentLoop
from agent_harness.runtime.subagents.control_tools import register_subagent_control_tools
from agent_harness.runtime.subagents.scheduler import SubagentScheduler
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.tools.runtime import ToolRuntime
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.summary import write_result_summary


@dataclass(slots=True)
class RunManager:
    config: HarnessConfig
    provider: ModelProvider

    async def run(self, task: str, workspace: Path) -> RunState:
        """Create a run state, wire dependencies, execute the loop, and save summary."""
        root = workspace.resolve()
        state = RunState(task=task, workspace_root=root)
        state.messages.append(CanonicalMessage(role="user", content=task))
        return await self.run_existing(state, self.config.trace.directory)

    async def run_existing(self, state: RunState, trace_root: Path) -> RunState:
        """Run one turn against an existing state so interactive sessions keep history."""
        root = state.workspace_root.resolve()
        trace = JsonlTraceSink(state.run_id, trace_root, fail_on_write_error=self.config.trace.fail_on_write_error)
        trace.emit(
            "run.created" if state.turn_count <= 1 else "turn.created",
            payload={"task": state.task, "workspace_root": str(root), "turn_id": state.turn_id, "turn_count": state.turn_count},
        )
        registry = create_default_registry(root, self.config.tools.default_timeout_seconds)
        agent_registry = create_default_agent_registry(self.config.provider.model, self.config.provider.name, self.config.run)
        scheduler = SubagentScheduler(
            run_id=state.run_id,
            workspace_root=root,
            config=self.config,
            provider=self.provider,
            trace=trace,
            agent_registry=agent_registry,
            max_concurrent=self.config.subagents.max_concurrent,
            max_total=self.config.subagents.max_total,
            max_depth=self.config.subagents.max_depth,
        )
        register_subagent_control_tools(registry, scheduler, self.config.tools.default_timeout_seconds)
        agent_registry.validate(registry.names() | {"submit_result"})
        agent = agent_registry.get("coding_assistant")
        agent.system_prompt = (
            SYSTEM_PROMPT
            + "\n\n子 Agent 可用角色：\n"
            + agent_registry.tool_description()
            + "\n\n委派规则：复杂、可并行、边界明确的分析可以使用 spawn_subagent；最终回答前必须 wait_subagents 或 cancel_subagent 清理所有活动子 Agent。"
        )
        loop = AgentLoop(
            agent=agent,
            provider=self.provider,
            context_builder=ContextBuilder(
                char_to_token_ratio=self.config.context.char_to_token_ratio,
                max_estimated_input_tokens=self.config.context.max_estimated_input_tokens,
                recent_turns=self.config.context.recent_turns,
            ),
            tool_runtime=ToolRuntime(registry=registry, max_result_chars=self.config.tools.max_result_chars),
            trace=trace,
            final_guard=lambda: (
                "仍有活动子 Agent 未处理。请先调用 wait_subagents 获取结果，或调用 cancel_subagent 取消它们，然后再最终回答。"
                if scheduler.active_agent_ids()
                else None
            ),
        )
        try:
            return await loop.run(state)
        finally:
            if scheduler.active_agent_ids():
                await scheduler.cancel_all()
            state.agent_summary = scheduler.summary()
            write_result_summary(state, trace.run_dir, trace.path)
            trace.close()
