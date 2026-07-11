from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import json
import os
from dataclasses import replace

from agent_harness.agents.registry import create_default_agent_registry
from agent_harness.config import HarnessConfig
from agent_harness.context.builder import ContextBuilder
from agent_harness.context.prompt import SYSTEM_PROMPT
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.errors import HarnessError
from agent_harness.domain.model import ModelProvider
from agent_harness.domain.run import RunState
from agent_harness.runtime.agent_loop import AgentLoop
from agent_harness.runtime.subagents.control_tools import register_subagent_control_tools
from agent_harness.runtime.subagents.scheduler import SubagentScheduler
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.tools.runtime import ToolRuntime
from agent_harness.sandbox.manager import SandboxManager
from agent_harness.security.approval import ApprovalHandler, DenyApprovalHandler
from agent_harness.security.models import SandboxPolicy
from agent_harness.security.permission_engine import PermissionEngine
from agent_harness.security.rules import PermissionRule
from agent_harness.security.models import PermissionDecision, RuleSource
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.summary import write_result_summary
from agent_harness.utils.ids import new_id
from agent_harness.guidance.discovery import GuidanceManager
from agent_harness.guidance.imports import ImportLimits
from agent_harness.guidance.models import GuidanceSnapshot, WorkingSet
from agent_harness.guidance.rules import activate_rules
from agent_harness.skills.activation import SkillManager
from agent_harness.skills.catalog import build_catalog
from agent_harness.skills.control_tools import create_activate_skill_tool, create_read_skill_resource_tool
from agent_harness.skills.discovery import SkillDiscovery, SkillSearchPath
from agent_harness.skills.models import SkillCatalogSnapshot, SkillScope
from agent_harness.skills.models import SkillActivationSnapshot
from agent_harness.domain.subagents import DelegationRequest


@dataclass(slots=True)
class RunManager:
    config: HarnessConfig
    provider: ModelProvider
    approval_handler: ApprovalHandler = field(default_factory=DenyApprovalHandler)
    rollout_audit: Callable[[str, dict[str, Any]], None] | None = None
    guidance_snapshot: GuidanceSnapshot | None = None
    skill_catalog: SkillCatalogSnapshot | None = None
    skill_manager: SkillManager | None = None
    working_set: WorkingSet = field(default_factory=WorkingSet)

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
        security = self.config.security
        if security.sandbox_mode.value == "danger-full-access" and not security.full_access_confirmed:
            raise HarnessError("danger-full-access must be explicitly confirmed by the CLI user")
        policy = SandboxPolicy(
            mode=security.sandbox_mode,
            workspace_root=root,
            readable_roots=(root,),
            writable_roots=(root,) if security.sandbox_mode.value == "workspace-write" else (),
            network_enabled=security.network_enabled,
            environment_allow=frozenset(security.environment_allow),
            timeout_seconds=security.default_timeout_seconds,
            max_output_chars=security.max_output_chars,
        )
        sandbox_backend = SandboxManager(security.sandbox_backend, security.wsl_distribution).create_backend(security.sandbox_mode)
        registry = create_default_registry(root, self.config.tools.default_timeout_seconds, sandbox_backend=sandbox_backend, sandbox_policy=policy)
        if self.guidance_snapshot is None or self.skill_manager is None:
            self.initialize_project_context(state.run_id, root, project_trusted=not self.config.guidance.require_workspace_trust)
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
        if self.skill_manager and self.skill_catalog and self.skill_catalog.skills:
            registry.register(
                create_activate_skill_tool(
                    self.skill_manager,
                    lambda: state.turn_id or "turn_unknown",
                    self._rollout_event,
                    lambda activation: self._delegate_fork_skill(scheduler, activation),
                )
            )
            registry.register(create_read_skill_resource_tool(self.skill_manager, self.config.skills.max_resource_bytes, self._rollout_event))
        register_subagent_control_tools(registry, scheduler, self.config.tools.default_timeout_seconds)
        agent_registry.validate(registry.names() | {"submit_result"})
        agent = agent_registry.get("coding_assistant")
        if self.skill_manager and self.skill_catalog and self.skill_catalog.skills:
            agent.enabled_tools.extend(["activate_skill", "read_skill_resource"])
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
                guidance_snapshot=self.guidance_snapshot,
                active_path_rules_provider=lambda: activate_rules(
                    self.guidance_snapshot.path_rules if self.guidance_snapshot else (), self.working_set, root
                ),
                skill_catalog=self.skill_catalog,
                active_skills_provider=lambda: tuple(self.skill_manager.active) if self.skill_manager else (),
                enabled_tools_provider=self._effective_skill_tools,
            ),
            tool_runtime=ToolRuntime(
                registry=registry,
                max_result_chars=self.config.tools.max_result_chars,
                permission_engine=PermissionEngine([*_builtin_rules(), *security.rules]),
                approval_handler=self.approval_handler,
                workspace_root=root,
                sandbox_policy_factory=lambda principal: policy,
                audit=lambda event, payload: self._emit_security_audit(trace, event, payload),
            ),
            trace=trace,
            final_guard=lambda: (
                "仍有活动子 Agent 未处理。请先调用 wait_subagents 获取结果，或调用 cancel_subagent 取消它们，然后再最终回答。"
                if scheduler.active_agent_ids()
                else None
            ),
            sandbox_mode=security.sandbox_mode,
            approval_policy=security.approval_policy,
            enabled_tools_provider=self._effective_skill_tools,
            tool_success_callback=lambda name, arguments: self._update_working_set(root, name, arguments),
        )
        try:
            return await loop.run(state)
        finally:
            if scheduler.active_agent_ids():
                await scheduler.cancel_all()
            state.agent_summary = scheduler.summary()
            write_result_summary(state, trace.run_dir, trace.path)
            trace.close()

    def initialize_project_context(self, thread_id: str, workspace: Path, *, project_trusted: bool, resume: bool = False) -> None:
        """Discover and persist stable Guidance and Skill metadata for one thread runtime."""
        root = workspace.resolve()
        user_root = Path(os.getenv("APPDATA", Path.home())) / "agent-harness"
        admin_root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "AgentHarness"
        if self.config.guidance.enabled:
            manager = GuidanceManager(
                workspace=root,
                cwd=root,
                user_root=user_root,
                admin_root=admin_root,
                fallback_filenames=self.config.guidance.project_doc_fallback_filenames,
                max_guidance_bytes=self.config.guidance.max_guidance_bytes,
                import_limits=ImportLimits(
                    self.config.guidance.max_import_depth,
                    self.config.guidance.max_import_files,
                    self.config.guidance.max_import_total_bytes,
                ),
            )
            previous = self.guidance_snapshot
            self.guidance_snapshot = manager.discover(thread_id, new_id("runtime"), project_trusted=project_trusted)
            snapshot_dir = self.config.trace.thread_directory / thread_id / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            old_hashes = {path.stem.removeprefix("guidance-") for path in snapshot_dir.glob("guidance-*.json")}
            path = snapshot_dir / f"guidance-{self.guidance_snapshot.combined_hash}.json"
            path.write_text(json.dumps(self.guidance_snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            changed = (previous is not None and previous.combined_hash != self.guidance_snapshot.combined_hash) or (resume and bool(old_hashes) and self.guidance_snapshot.combined_hash not in old_hashes)
            event = "guidance.snapshot_changed" if changed else "guidance.snapshot_created"
            self._rollout_event(event, {"snapshot_id": self.guidance_snapshot.snapshot_id, "combined_hash": self.guidance_snapshot.combined_hash})
        if self.config.skills.enabled:
            paths = [
                SkillSearchPath(SkillScope.BUNDLED, Path(__file__).parents[1] / "bundled_skills", "bundled"),
                SkillSearchPath(SkillScope.ADMIN, admin_root / "skills", "admin"),
                SkillSearchPath(SkillScope.USER, user_root / "skills", "user"),
                SkillSearchPath(SkillScope.USER, Path.home() / ".agents" / "skills", "user"),
            ]
            for skills_root in sorted(path for path in root.rglob(".agents/skills") if path.is_dir()):
                owner = skills_root.parent.parent
                relative = owner.relative_to(root).as_posix()
                prefix = "project" if relative == "." else f"project:{relative}"
                paths.append(SkillSearchPath(SkillScope.PROJECT, skills_root, prefix, project_trusted))
            discovery = SkillDiscovery(tuple(paths), self.config.skills.max_skills, self.config.skills.max_skill_scan_depth, self.config.skills.max_skill_directories)
            records, diagnostics = discovery.discover()
            records = tuple(replace(record, enabled=False) if record.skill_id in self.config.skills.disabled_skill_ids else record for record in records)
            catalog_chars = int(self.config.context.max_estimated_input_tokens * self.config.skills.catalog_context_ratio * self.config.context.char_to_token_ratio)
            self.skill_catalog = build_catalog(records, diagnostics, max_chars=catalog_chars or self.config.skills.catalog_fallback_max_chars)
            self.skill_manager = SkillManager(records, self.config.trace.thread_directory / thread_id / "snapshots" / "skills")
            if resume:
                self.skill_manager.resume()
            self._rollout_event("skill.catalog_created", {"catalog_id": self.skill_catalog.catalog_id, "skill_count": len(records), "char_count": self.skill_catalog.char_count})

    def reset_turn_context(self) -> None:
        """Start a fresh Working Set while keeping active Skills durable across turns."""
        self.working_set = WorkingSet()

    def _effective_skill_tools(self, agent_tools: list[str]) -> list[str]:
        """Intersect Agent tools with every active Skill restriction without granting new tools."""
        allowed = set(agent_tools)
        if self.skill_manager:
            for activation in self.skill_manager.active:
                if activation.allowed_tools:
                    allowed.intersection_update(activation.allowed_tools)
        return [name for name in agent_tools if name in allowed]

    async def _delegate_fork_skill(self, scheduler: SubagentScheduler, activation: SkillActivationSnapshot) -> dict[str, Any]:
        """Run a fork-context Skill in its declared child agent and return structured output."""
        if not self.config.skills.support_fork_context:
            raise HarnessError("Fork Skill support is disabled")
        if not activation.agent:
            raise HarnessError("Fork Skill must declare an agent")
        spawned = await scheduler.spawn(
            DelegationRequest(
                agent_name=activation.agent,
                task=activation.rendered_instructions,
                context=f"Skill: {activation.qualified_name}\nArguments: {activation.arguments}",
                idempotency_key=activation.activation_id,
                allowed_tools=activation.allowed_tools,
            )
        )
        waited = await scheduler.wait([str(spawned["agent_id"])], timeout_seconds=self.config.run.max_wall_time_seconds)
        return dict(waited["results"][0])

    def _update_working_set(self, workspace: Path, tool_name: str, arguments: dict[str, Any]) -> None:
        """Update confirmed or candidate paths after successful model-visible file tools."""
        value = arguments.get("path")
        if not isinstance(value, str):
            return
        resolved = str((workspace / value).resolve()) if not Path(value).is_absolute() else str(Path(value).resolve())
        if tool_name in {"read_file", "write_file", "apply_patch", "delete_path"}:
            self.working_set.confirmed_paths.add(resolved)
        elif tool_name == "search_text":
            self.working_set.candidate_paths.add(resolved)

    def _rollout_event(self, event: str, payload: dict[str, Any]) -> None:
        """Send phase 4 canonical events to the active session recorder when available."""
        if self.rollout_audit:
            self.rollout_audit(event, payload)

    def _emit_security_audit(self, trace: JsonlTraceSink, event: str, payload: dict[str, Any]) -> None:
        """Write security events to the trace and optional canonical rollout sink."""
        trace.emit(event, payload=payload)
        if self.rollout_audit:
            self.rollout_audit(event, payload)


def _builtin_rules() -> list[PermissionRule]:
    """Return conservative built-in command rules that project config cannot override."""
    return [
        PermissionRule("builtin-git-push-deny", PermissionDecision.DENY, RuleSource.BUILTIN, tool="run_command", argv_prefix=("git", "push")),
        PermissionRule("builtin-git-commit-ask", PermissionDecision.ASK, RuleSource.BUILTIN, tool="run_command", argv_prefix=("git", "commit")),
        PermissionRule("builtin-pytest-allow", PermissionDecision.ALLOW, RuleSource.BUILTIN, tool="run_command", argv_prefix=("pytest",)),
    ]
