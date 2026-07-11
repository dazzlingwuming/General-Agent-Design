from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Distinct project identity, tool boundary, and launch directory paths."""

    project_root: Path
    workspace_root: Path
    cwd: Path

    def scope_chain(self) -> tuple[Path, ...]:
        """Return directories from the project root through the launch directory."""
        relative = self.cwd.relative_to(self.project_root)
        chain = [self.project_root]
        current = self.project_root
        for part in relative.parts:
            current /= part
            chain.append(current)
        return tuple(chain)


def resolve_project_paths(cwd: Path, workspace_root: Path | None = None) -> ProjectPaths:
    """Resolve a Git project root while keeping cwd as the tool default directory."""
    launch = cwd.resolve()
    boundary = (workspace_root or launch).resolve()
    project = _git_root(launch) or boundary
    try:
        launch.relative_to(project)
    except ValueError:
        project = boundary
    if workspace_root is None or boundary == launch:
        boundary = project
    try:
        launch.relative_to(boundary)
    except ValueError as exc:
        raise ValueError("cwd must be inside workspace_root") from exc
    return ProjectPaths(project.resolve(), boundary.resolve(), launch)


def _git_root(cwd: Path) -> Path | None:
    """Return Git's top-level directory, or None outside a repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        return None
