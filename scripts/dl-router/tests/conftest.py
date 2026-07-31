"""Shared fixtures. Everything is hermetic: temp filesystem roots, an in-memory
or temp SQLite store, a stub qBittorrent, an injected clock. No browser, no
HDD, no cluster, no network — see the design spec's test plan.

All fixture data is SYNTHETIC (`Jane Doe`, `acme-studio`, `example-site.test`).
"""
from __future__ import annotations

import json
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
