from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_pyfunc_call(pyfuncitem):
    """Run async test functions without requiring pytest-asyncio in this environment."""
    testfunction = pyfuncitem.obj
    if inspect.iscoroutinefunction(testfunction):
        funcargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(testfunction(**funcargs))
        return True
    return None


@pytest.fixture(autouse=True)
def isolate_default_trace_directory(tmp_path, monkeypatch):
    """Keep tests that use default HarnessConfig from polluting the project runs."""
    monkeypatch.chdir(tmp_path)


def pytest_collection_modifyitems(config, items):
    """Classify tests by responsibility and skip external live tests by default."""
    for item in items:
        relative = Path(str(item.path)).resolve().relative_to(ROOT.resolve())
        if relative.parts[0:2] == ("tests", "unit"):
            item.add_marker(pytest.mark.unit)
        elif relative.parts[0:2] == ("tests", "integration") and not ({"platform_linux", "platform_windows"} & set(item.keywords)):
            item.add_marker(pytest.mark.integration_local)
        elif relative.parts[0:2] == ("tests", "live"):
            item.add_marker(pytest.mark.live_provider)
    if config.option.markexpr in {"live", "live_provider", "live_oauth"}:
        return
    skip_live = pytest.mark.skip(reason="live tests run only with pytest -m live")
    for item in items:
        if {"live", "live_provider", "live_oauth"} & set(item.keywords):
            item.add_marker(skip_live)
