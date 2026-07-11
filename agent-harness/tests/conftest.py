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
    """Skip live tests during ordinary pytest runs unless the user selects -m live."""
    if config.option.markexpr == "live":
        return
    skip_live = pytest.mark.skip(reason="live tests run only with pytest -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
