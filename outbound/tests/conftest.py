import sys
from pathlib import Path

import pytest

# Permite `import common`, `import db` etc. de dentro de tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: testes que batem em API real (rodar na máquina do Lucas: pytest -m live)",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Grava uma linha em `runs` para cada teste (best-effort; ignora se o db não responder)."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    try:
        import db

        db.log_run(f"test:{item.name}", report.passed, report.outcome)
    except Exception:
        pass
