from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from agent_harness.sandbox.base import CommandExecution, CommandResult
from agent_harness.sandbox.process import terminate_process_tree, truncate_streams
from agent_harness.security.models import SandboxMode, SandboxPolicy


def windows_path_to_wsl(path: PureWindowsPath) -> PurePosixPath:
    """Translate an absolute Windows drive path without consulting the host OS."""
    drive = path.drive.rstrip(":").lower()
    if not drive or not path.is_absolute():
        raise ValueError(f"Cannot translate path to WSL: {path}")
    return PurePosixPath("/mnt", drive, *path.parts[1:])


@dataclass(slots=True)
class BubblewrapSandboxBackend:
    """Linux bubblewrap backend with read-only host mounts and optional workspace writes."""

    name: str = "bubblewrap"

    async def availability(self) -> tuple[bool, str]:
        """Check that bubblewrap exists on a native Linux host."""
        path = shutil.which("bwrap")
        return (bool(path), path or "bubblewrap not found")

    async def execute(self, execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Compile policy into bubblewrap argv and execute without a shell."""
        available, reason = await self.availability()
        if not available:
            raise RuntimeError(f"Sandbox unavailable: {reason}")
        argv = self.build_argv(execution, policy)
        return await self._spawn("bwrap", argv, execution, policy)

    def build_argv(self, execution: CommandExecution, policy: SandboxPolicy) -> list[str]:
        """Build the bubblewrap argument vector for one execution policy."""
        root = policy.workspace_root.resolve()
        argv = ["--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        if not policy.network_enabled:
            argv.append("--unshare-net")
        argv.extend(["--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        if policy.mode == SandboxMode.WORKSPACE_WRITE:
            argv.extend(["--bind", str(root), str(root)])
        else:
            argv.extend(["--ro-bind", str(root), str(root)])
        argv.extend(["--chdir", str(execution.cwd.resolve()), "--clearenv"])
        for key in sorted(policy.environment_allow):
            if key in os.environ:
                argv.extend(["--setenv", key, os.environ[key]])
        for key, value in sorted(execution.env.items()):
            argv.extend(["--setenv", key, value])
        argv.extend(["--", execution.program, *execution.args])
        return argv

    async def _spawn(self, program: str, argv: list[str], execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Spawn bubblewrap and clean up its process group on timeout or cancellation."""
        process = await asyncio.create_subprocess_exec(program, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
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


@dataclass(slots=True)
class WslBubblewrapSandboxBackend(BubblewrapSandboxBackend):
    """Windows host adapter that invokes bubblewrap inside an installed WSL2 distro."""

    distribution: str | None = None
    name: str = "wsl2-bubblewrap"

    async def availability(self) -> tuple[bool, str]:
        """Verify that WSL has a distribution and that distribution provides bubblewrap."""
        if os.name != "nt" or not shutil.which("wsl.exe"):
            return False, "WSL is unavailable"
        command = ["wsl.exe"]
        if self.distribution:
            command.extend(["--distribution", self.distribution])
        command.extend(["--exec", "sh", "-lc", "command -v bwrap"])
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return False, stderr.decode(errors="replace").strip() or "No WSL distribution with bubblewrap"
        return True, stdout.decode(errors="replace").strip()

    async def execute(self, execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Translate Windows paths and execute bubblewrap through wsl.exe without shell parsing."""
        available, reason = await self.availability()
        if not available:
            raise RuntimeError(f"Sandbox unavailable: {reason}")
        workspace = self._wsl_path(policy.workspace_root)
        cwd = self._wsl_path(execution.cwd)
        argv = self._build_wsl_argv(execution, policy, workspace, cwd)
        prefix: list[str] = []
        if self.distribution:
            prefix.extend(["--distribution", self.distribution])
        prefix.extend(["--exec", "bwrap"])
        return await self._spawn("wsl.exe", [*prefix, *argv], execution, policy)

    def _build_wsl_argv(self, execution: CommandExecution, policy: SandboxPolicy, workspace: str, cwd: str) -> list[str]:
        """Build Linux bubblewrap argv without re-resolving POSIX paths on Windows."""
        argv = ["--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        if not policy.network_enabled:
            argv.append("--unshare-net")
        argv.extend(["--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        bind_flag = "--bind" if policy.mode == SandboxMode.WORKSPACE_WRITE else "--ro-bind"
        argv.extend([bind_flag, workspace, workspace, "--chdir", cwd, "--clearenv"])
        argv.extend(["--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"])
        for key in ("LANG", "LC_ALL", "TERM"):
            if key in policy.environment_allow and key in os.environ:
                argv.extend(["--setenv", key, os.environ[key]])
        for key, value in sorted(execution.env.items()):
            argv.extend(["--setenv", key, value])
        argv.extend(["--", execution.program, *execution.args])
        return argv

    def _wsl_path(self, path: Path) -> str:
        """Convert a resolved drive path into its conventional WSL mount path."""
        try:
            return str(windows_path_to_wsl(PureWindowsPath(path.resolve())))
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
