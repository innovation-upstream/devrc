"""Put the session_insight package dir on sys.path so its stdlib-style modules
(schema, scrub, select, prepare, consolidate, write, cli) import directly — the
same idiom the sibling collector/validation test suites use for their scripts."""
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent   # scripts/session-analysis/session_insight
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))


# --- GUARD 9: the repository the suite RUNS FROM ----------------------------- #
# 🔴 THE SECOND ENTRY POINT, and it belongs in EVERY test directory a bare
# `pytest <dir>` can be pointed at. `scripts/run-tests.sh` loads the same module
# with `-p testlib.gitenv_plugin` for every target, so this changes nothing
# under the runner; it is what protects a hand-run `pytest`. #683's audit found
# exactly ONE of seven conftests wired, and not the one `gitenv_plugin`'s own
# rationale cites (`test_bash_guard.py::_mkrepo` and `test_guard_core.py`'s
# module-scoped repos, which run during COLLECTION).
# `test_git_repo_isolation.py::test_the_conftest_entry_points_are_a_pinned_ledger`
# fails when a conftest under `scripts/` is added or removed, so the next one
# cannot be forgotten — that is the "asserted ledger of every caller" shape
# claude/RULES.md asks for, rather than a single pinned example.
import sys as _guard9_sys  # noqa: E402
from pathlib import Path as _Guard9Path  # noqa: E402

for _guard9_parent in _Guard9Path(__file__).resolve().parents:
    if (_guard9_parent / "testlib" / "gitenv_plugin.py").is_file():
        if str(_guard9_parent) not in _guard9_sys.path:
            _guard9_sys.path.insert(0, str(_guard9_parent))
        break

from testlib.gitenv_plugin import (  # noqa: E402,F401
    _devrc_git_repo_isolation,
    pytest_collection_finish,
    pytest_configure,
    pytest_runtest_logstart,
    pytest_sessionfinish,
)
