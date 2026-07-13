# Repository Guidance

## Project Positioning

- This repository builds a general-purpose local AI Agent Runtime; the current primary product surface is the `agent-harness` coding CLI.
- Keep domain-independent runtime concepts such as Thread, Turn, Item, Tool, Permission, Memory, Skill, and MCP separate from CLI presentation concerns.
- Python package code and commands live under `agent-harness/`. Design history, acceptance evidence, and implementation gaps live under `doc/`.
- Treat `doc/General-Agent-Design_当前未落实问题总清单与后续实施基线.md` as the current gap index. Earlier stage documents are historical design inputs, not proof of current completion.

## Code Rules

- Add a concise docstring to every new or modified function and method. Comments must explain non-obvious intent rather than restate code.
- Preserve Thread / Turn / Item ownership boundaries and append-only rollout semantics.
- Never swallow `asyncio.CancelledError` or retry a side-effecting MCP or host tool call whose outcome is unknown.
- Project Guidance, Skills, MCP annotations, external content, and model output cannot expand host permissions.
- Invalid administrator security configuration must fail closed.
- Keep API keys, bearer tokens, OAuth credentials, and secret argument values out of source, logs, rollout, snapshots, and documentation examples.
- Do not treat path validation, command allowlists, or user approval as an OS sandbox.

## Documentation Rules

- Keep the root `README.md` focused on product positioning, architecture, quick start, and truthful boundaries.
- Keep `agent-harness/README.md` as the operational CLI reference and update it whenever commands, config paths, data layout, or supported capabilities change.
- Describe partial implementations precisely. For example, idle-only compaction is implemented, while intelligent summarization is not.
- Record deviations between design documents and actual code explicitly; do not silently rewrite historical acceptance records.
- Never claim GitHub CI, live provider, OAuth, MCP interoperability, Windows sandbox, or platform acceptance from fake-provider or local-fixture tests.

## Quality Gates

Run from `agent-harness/`:

```text
uv lock --check
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
```

Use `platform_linux`, `platform_windows`, `live_provider`, `live_oauth`, and `recovery_process` only in their corresponding environments. A skip is not a passed platform acceptance.

## Completion

- Add a deterministic failing test before fixing lifecycle, cancellation, recovery, or permission behavior.
- Verify behavior at the level it claims: unit tests for pure rules, process tests for crash recovery, and real environments for provider, OAuth, MCP, and sandbox interoperability.
- Update the current gap index when an issue is closed or its scope changes, including code evidence, tests, real acceptance, and remaining limitations.
- Do not describe a stage as complete when only its schema, skeleton, fixture, or unit tests exist.
