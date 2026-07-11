from __future__ import annotations

import asyncio
import os
import signal
from asyncio.subprocess import Process


async def terminate_process_tree(process: Process) -> None:
    """Terminate a process group and fall back to direct process termination."""
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            getattr(os, "killpg")(process.pid, signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        if process.returncode is None:
            process.kill()
            await process.wait()


def truncate_streams(stdout: str, stderr: str, limit: int) -> tuple[str, str, bool]:
    """Apply a combined output limit while preserving both streams."""
    if len(stdout) + len(stderr) <= limit:
        return stdout, stderr, False
    stdout_limit = min(len(stdout), limit // 2)
    stderr_limit = max(0, limit - stdout_limit)
    return stdout[:stdout_limit] + "\n[truncated]", stderr[:stderr_limit] + "\n[truncated]", True
