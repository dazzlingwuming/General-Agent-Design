from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from agent_harness.sandbox.base import CommandExecution, CommandResult
from agent_harness.sandbox.process import terminate_process_tree, truncate_streams
from agent_harness.security.models import SandboxMode, SandboxPolicy


@dataclass(slots=True)
class NoSandboxBackend:
    """Explicit full-access process backend; never selected as an automatic fallback."""

    name: str = "none"

    async def availability(self) -> tuple[bool, str]:
        """Report availability because this backend provides no isolation."""
        return True, "no isolation"

    async def execute(self, execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Execute directly only when the user selected danger-full-access."""
        if policy.mode != SandboxMode.DANGER_FULL_ACCESS:
            raise RuntimeError("NoSandboxBackend requires danger-full-access")
        env = {key: value for key, value in os.environ.items() if key in policy.environment_allow}
        env.update(execution.env)
        process = await asyncio.create_subprocess_exec(
            execution.program,
            *execution.args,
            cwd=execution.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        timeout = execution.timeout_seconds or policy.timeout_seconds
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(process)
            return CommandResult(-1, "", "Process timed out", timed_out=True, backend=self.name)
        except asyncio.CancelledError:
            await terminate_process_tree(process)
            raise
        stdout, stderr, truncated = truncate_streams(stdout_raw.decode(errors="replace"), stderr_raw.decode(errors="replace"), policy.max_output_chars)
        return CommandResult(process.returncode or 0, stdout, stderr, truncated=truncated, backend=self.name)
