from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_harness.guidance.models import GuidanceDiagnostic

IMPORT_RE = re.compile(r"^\s*@import\s+([^\s]+)\s*$")


@dataclass(slots=True)
class ImportLimits:
    """Resource limits for recursive guidance imports."""

    max_depth: int = 4
    max_files: int = 32
    max_total_bytes: int = 32768


def expand_imports(path: Path, boundary: Path, limits: ImportLimits) -> tuple[str, list[GuidanceDiagnostic]]:
    """Expand standalone import lines while enforcing depth, cycle, and path boundaries."""
    diagnostics: list[GuidanceDiagnostic] = []
    seen: set[Path] = set()
    total_bytes = 0

    def load(current: Path, depth: int, stack: tuple[Path, ...]) -> str:
        """Load one import node and recursively replace valid import directives."""
        nonlocal total_bytes
        try:
            resolved = current.resolve(strict=True)
            root = boundary.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError):
            diagnostics.append(GuidanceDiagnostic("error", "import_boundary", "导入文件不存在或超出允许范围", str(current)))
            return ""
        if resolved in stack:
            diagnostics.append(GuidanceDiagnostic("error", "import_cycle", "检测到 Guidance 循环导入", str(resolved)))
            return ""
        if resolved in seen:
            return ""
        if depth > limits.max_depth:
            diagnostics.append(GuidanceDiagnostic("error", "import_depth", "Guidance 导入深度超过限制", str(resolved)))
            return ""
        if len(seen) >= limits.max_files:
            diagnostics.append(GuidanceDiagnostic("error", "import_count", "Guidance 导入文件数超过限制", str(resolved)))
            return ""
        try:
            raw = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            diagnostics.append(GuidanceDiagnostic("error", "invalid_utf8", "Guidance 导入文件不是 UTF-8", str(resolved)))
            return ""
        size = len(raw.encode("utf-8"))
        if total_bytes + size > limits.max_total_bytes:
            diagnostics.append(GuidanceDiagnostic("warning", "import_bytes", "Guidance 导入总大小超过限制", str(resolved)))
            return ""
        seen.add(resolved)
        total_bytes += size
        output: list[str] = []
        in_fence = False
        for line in raw.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            match = None if in_fence else IMPORT_RE.match(line)
            if match:
                imported = Path(match.group(1))
                if imported.is_absolute():
                    diagnostics.append(GuidanceDiagnostic("error", "absolute_import", "禁止绝对路径 Guidance 导入", str(imported)))
                    continue
                output.append(load(resolved.parent / imported, depth + 1, (*stack, resolved)))
            else:
                output.append(line)
        return "\n".join(output).strip()

    return load(path, 0, ()), diagnostics
