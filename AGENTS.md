# Repository Guidance

## Scope

- Python package and commands live under `agent-harness/`.
- Stage designs and truthful acceptance records live under `doc/`.
- Do not claim sandbox completion. Native Windows sandbox hardening is deferred.

## Code Rules

- Add a concise docstring to every new or modified function and method. Comments must explain non-obvious intent rather than restate code.
- Preserve Thread/Turn/Item ownership boundaries and append-only rollout semantics.
- Never swallow `asyncio.CancelledError` or retry a side-effecting MCP tool call whose outcome is unknown.
- Project guidance, Skills, MCP annotations, and model output cannot expand host permissions.
- Invalid administrator security configuration must fail closed.
- Keep API keys, bearer tokens, OAuth credentials, and secret argument values out of source, logs, rollout, and snapshots.

## Quality Gates

Run from `agent-harness/`:

```text
uv lock --check
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
```

Use `platform_linux`, `platform_windows`, `live_provider`, and `live_oauth` only in their corresponding environments. A skip is not a passed platform acceptance.

## Completion

- Add a deterministic failing test before fixing lifecycle, cancellation, recovery, or permission behavior.
- Record implementation differences from design documents explicitly.
- Do not describe local tests as GitHub CI success or fake/local fixtures as external live validation.
