"""Shared fixtures. Everything is hermetic: temp filesystem roots, an in-memory
or temp SQLite store, a stub qBittorrent, an injected clock. No browser, no
HDD, no cluster, no network — see the design spec's test plan.

All fixture data is SYNTHETIC (`Jane Doe`, `acme-studio`, `example-site.test`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402
from dirindex import DirIndex, FileIndex  # noqa: E402
from store import Store  # noqa: E402

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
def cfg(tmp_path, library):
    """A Config pointing entirely at temp paths."""
    data = config_mod._deep_merge(config_mod.DEFAULTS, {
        "library_root": str(library),
        "host": "127.0.0.1",
        "port": 0,
    })
    return config_mod.Config(data, path=tmp_path / "config.toml",
                             state_dir=tmp_path / "state",
                             token_file=tmp_path / "token")
