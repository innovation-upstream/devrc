"""Shared fixtures. Everything is hermetic: temp filesystem roots, an in-memory
or temp SQLite store, a stub qBittorrent, an injected clock. No browser, no
HDD, no cluster, no network — see the design spec's test plan.

All fixture data is SYNTHETIC (`Jane Doe`, `acme-studio`, `example-site.test`).
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402
from dirindex import DirIndex, FileIndex  # noqa: E402
from store import Store  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _expand(value):
    """Expand the fixture file's {prefix?, repeat, count} long-string form.

    Anything else (including a plain dict, which is one of the non-string
    cases) is returned untouched.
    """
    if isinstance(value, dict) and "repeat" in value:
        return str(value.get("prefix", "")) + str(value["repeat"]) * int(
            value["count"])
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_name_cases() -> dict:
    """THE shared hostile-input table (tests/fixtures/name_cases.json).

    `tests/sanitize.test.mjs` loads the SAME file through the same expansion
    rules, so safety.py and extension/sanitize.js are asserted against one
    table rather than two hand-copied lists that silently drifted apart.
    """
    raw = json.loads((FIXTURES / "name_cases.json").read_text(encoding="utf-8"))
    return {k: _expand(v) for k, v in raw.items() if not k.startswith("_")}


def load_url_cases() -> dict:
    """THE shared identity-signal table (tests/fixtures/url_cases.json).

    `tests/fixtures.mjs` loads the SAME file, so matcher.py and
    extension/route_core.js are asserted against one table. The extension's
    cached fallback runs exactly when the sidecar is unreachable, so a
    divergence between the two would be invisible until it misfiled something.
    """
    raw = json.loads((FIXTURES / "url_cases.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}

# The three naming conventions that coexist in a real library. The matcher must
# fold all of them to one key, which is why existing dirs are never renamed.
SAMPLE_DIRS = [
    "Jane Doe",          # Title Case
    "john-smith",        # lower-kebab
    "Mary_Major",        # snake_Case
    "acme-studio",
    "Aster Vale",
    "other",
]


class FakeClock:
    """Monotonic-ish clock the tests drive by hand."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


def _free_port() -> int:
    """A loopback port with NOTHING listening on it, chosen at call time.

    🔴 Do not go back to a hard-coded number. Every "the sidecar is DOWN /
    unreachable" assertion in this suite is really an assertion that nobody is
    listening on the configured port, and a literal (8799) makes that a claim
    about the WHOLE MACHINE rather than about the test.

    MEASURED 2026-08-02 on the workbench: an orphaned `python3` from the
    previous day (pid 2994086, started Aug 1 13:40, ppid 1) was listening on
    127.0.0.1:8799, so `test_an_unreachable_sidecar_gives_an_actionable_message`
    reached a REAL dl-router and failed with `sidecar HTTP 409: not_owned_tab`
    instead of the expected message. The nix sandbox has no such listener, so
    this failed on the dev-host tier ONLY — the two-tier hazard again, in the
    opposite direction from the shebang one.

    Asking the kernel for port 0 and closing gives a port that is free NOW.
    That is not a lease — but it beats a constant, and the alternative (an
    assertion whose truth depends on ambient host state) is not a test.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def closed_port() -> int:
    """See `_free_port`. Use this wherever a test needs a port that is DOWN."""
    return _free_port()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def library(tmp_path):
    """A synthetic library root with the sample subject directories."""
    root = tmp_path / "library"
    root.mkdir()
    for name in SAMPLE_DIRS:
        (root / name).mkdir()
    return root


@pytest.fixture
def store(tmp_path, clock):
    st = Store(tmp_path / "state" / "dl-router.sqlite3", clock=clock)
    yield st
    st.close()


@pytest.fixture
def dir_index(library, clock):
    return DirIndex(library, clock=clock, ttl=5.0, other_dir="other")


@pytest.fixture
def file_index(library, clock):
    return FileIndex(library, clock=clock, ttl=60.0)


@pytest.fixture
def dirs_file(tmp_path):
    """Directory kinds for the sample library — every directory `performer`.

    Only a `performer` directory may auto-file (a `category` always asks, and
    an unclassified one does too), so without a classification EVERY auto-file
    assertion in the suite would be asserting the kind gate rather than the
    rule it names. The gate has its own tests, and the category/unclassified
    cases write their own file.
    """
    path = tmp_path / "dirs.toml"
    body = ["performer = ["]
    body += [f'  "{name}",' for name in SAMPLE_DIRS]
    body += ["]", "category = []", ""]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


@pytest.fixture
def cfg(tmp_path, library, dirs_file):
    """A Config pointing entirely at temp paths."""
    data = config_mod._deep_merge(config_mod.DEFAULTS, {
        "library_root": str(library),
        "host": "127.0.0.1",
        "port": 0,
    })
    return config_mod.Config(data, path=tmp_path / "config.toml",
                             state_dir=tmp_path / "state",
                             token_file=tmp_path / "token",
                             dirs_file=dirs_file)


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
