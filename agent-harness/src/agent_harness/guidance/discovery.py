from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_harness.guidance.imports import ImportLimits, expand_imports
from agent_harness.guidance.models import GuidanceDiagnostic, GuidanceDocument, GuidanceSnapshot, GuidanceSourceKind
from agent_harness.guidance.rules import rule_diagnostic, rule_patterns
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import iso_now


@dataclass(slots=True)
class GuidanceManager:
    """Discover layered guidance and freeze it into complete-document snapshots."""

    workspace: Path
    cwd: Path
    user_root: Path
    admin_root: Path | None = None
    fallback_filenames: tuple[str, ...] = ("CLAUDE.md",)
    max_guidance_bytes: int = 32768
    import_limits: ImportLimits | None = None

    def discover(self, thread_id: str, runtime_instance_id: str, *, project_trusted: bool) -> GuidanceSnapshot:
        """Discover guidance in precedence order and return one immutable snapshot."""
        diagnostics: list[GuidanceDiagnostic] = []
        candidates: list[tuple[Path, GuidanceSourceKind, Path, int, bool]] = []
        if self.admin_root:
            self._append_main(candidates, self.admin_root, GuidanceSourceKind.ADMIN, 100, True)
        self._append_main(candidates, self.user_root, GuidanceSourceKind.USER, 200, True)
        project_root = self._project_root()
        for depth, directory in enumerate(self._project_chain(project_root)):
            self._append_main(candidates, directory, GuidanceSourceKind.PROJECT, 300 + depth, project_trusted)
            if candidates and candidates[-1][2] == directory and candidates[-1][1] == GuidanceSourceKind.PROJECT:
                path, kind, _, precedence, trusted = candidates[-1]
                candidates[-1] = (path, kind, project_root, precedence, trusted)
        rule_candidates = self._rule_candidates(project_root, project_trusted)

        documents: list[GuidanceDocument] = []
        rules: list[GuidanceDocument] = []
        omitted: list[str] = []
        total = 0
        for path, kind, boundary, precedence, trusted in [*candidates, *rule_candidates]:
            document = self._load(path, kind, boundary, precedence, trusted, diagnostics)
            if document is None:
                continue
            if total + document.byte_size > self.max_guidance_bytes:
                omitted.append(str(path))
                continue
            total += document.byte_size
            if kind == GuidanceSourceKind.PATH_RULE:
                rules.append(document)
            elif trusted or kind != GuidanceSourceKind.PROJECT:
                documents.append(document)
        combined = hashlib.sha256("\n".join(item.content_hash for item in [*documents, *rules]).encode()).hexdigest()
        return GuidanceSnapshot(
            snapshot_id=f"guidance_{combined[:16]}",
            runtime_instance_id=runtime_instance_id,
            thread_id=thread_id,
            documents=tuple(documents),
            path_rules=tuple(rules),
            combined_hash=combined,
            total_bytes=total,
            truncated=bool(omitted),
            omitted_documents=tuple(omitted),
            diagnostics=tuple(diagnostics),
        )

    def _append_main(
        self,
        output: list[tuple[Path, GuidanceSourceKind, Path, int, bool]],
        directory: Path,
        kind: GuidanceSourceKind,
        precedence: int,
        trusted: bool,
    ) -> None:
        """Append the first non-empty override, standard, or fallback file in one directory."""
        for name in ("AGENTS.override.md", "AGENTS.md", *self.fallback_filenames):
            path = directory / name
            try:
                if path.is_file() and path.stat().st_size > 0:
                    output.append((path, kind, directory, precedence, trusted))
                    return
            except OSError:
                continue

    def _rule_candidates(self, project_root: Path, project_trusted: bool) -> list[tuple[Path, GuidanceSourceKind, Path, int, bool]]:
        """Return user and project rule files in deterministic scope order."""
        result: list[tuple[Path, GuidanceSourceKind, Path, int, bool]] = []
        roots = []
        if self.admin_root:
            roots.append((self.admin_root / "rules", 150, True))
        roots.extend(((self.user_root / "rules", 250, True), (project_root / ".agents" / "rules", 400, project_trusted)))
        for root, precedence, trusted in roots:
            if root.exists():
                result.extend((path, GuidanceSourceKind.PATH_RULE, root, precedence, trusted) for path in sorted(root.rglob("*.md")))
        return result

    def _load(
        self,
        path: Path,
        kind: GuidanceSourceKind,
        boundary: Path,
        precedence: int,
        trusted: bool,
        diagnostics: list[GuidanceDiagnostic],
    ) -> GuidanceDocument | None:
        """Load one complete guidance document and collect recoverable diagnostics."""
        limits = self.import_limits or ImportLimits()
        content, import_diagnostics = expand_imports(path, boundary, limits)
        diagnostics.extend(import_diagnostics)
        if not content.strip():
            return None
        patterns: tuple[str, ...] = ()
        excludes: tuple[str, ...] = ()
        if kind == GuidanceSourceKind.PATH_RULE:
            try:
                patterns, excludes, content = rule_patterns(content)
            except (ValueError, TypeError) as exc:
                diagnostics.append(rule_diagnostic(path, exc))
                return None
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            relative = path.resolve().relative_to(boundary.resolve()).as_posix()
        except ValueError:
            relative = None
        return GuidanceDocument(
            document_id=new_id("guidance_doc"),
            source_kind=kind,
            path=path.resolve(),
            scope_root=boundary.resolve(),
            relative_path=relative,
            content=content,
            content_hash=digest,
            byte_size=len(encoded),
            precedence=precedence,
            directory_depth=len(path.resolve().parts),
            path_patterns=patterns,
            exclude_patterns=excludes,
            trusted=trusted,
            loaded_at=iso_now(),
        )

    def _project_root(self) -> Path:
        """Return the Git top-level root or the configured workspace root."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.cwd), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            root = Path(result.stdout.strip()).resolve()
            root.relative_to(self.workspace.resolve())
            return root
        except (subprocess.SubprocessError, OSError, ValueError):
            return self.workspace.resolve()

    def _project_chain(self, root: Path) -> list[Path]:
        """Return every directory from project root through the current working directory."""
        try:
            relative = self.cwd.resolve().relative_to(root.resolve())
        except ValueError:
            return [root.resolve()]
        chain = [root.resolve()]
        current = root.resolve()
        for part in relative.parts:
            current /= part
            chain.append(current)
        return chain
