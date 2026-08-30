#!/usr/bin/env python3
"""REPO-WIDE structural guard: no test may reach for `/usr/bin/env` at runtime.

WHY
---
`/usr/bin/env` exists on every NixOS dev host (a symlink into the coreutils
store path) and does NOT exist in the nix build sandbox, which is where the
authoritative gate runs (`nix build .#checks.x86_64-linux.pytests`).
`patchShebangs` fixes shebangs in the SOURCE TREE; it cannot touch a file a test
writes while running. So the defect is structurally invisible on the tier people
look at and only ever red on the tier that gates merges. Full account:
`scripts/testlib/mockbin.py`.

WHY REPO-WIDE
-------------
#298 shipped this scan pointed at ONE directory
(`scripts/collector/opencode/tests`). On the same day
`scripts/dl-router/tests/test_setup_script.py` was found carrying the identical
defect — the scan could not see it, because the hazard is a property of the
repo, not of one suite. Directory-scoped guards regenerate the bug in the next
directory; `claude/RULES.md` → "One rule, one place".

WHY THREE SHAPES
----------------
Until 2026-08-06 the scan saw ONE shape: a shebang sitting directly behind a
quote, which is what a one-line stub body looks like. `93caa3a` then added
`scripts/tests/test_rig_control.py` and `scripts/tests/test_monitor_blackout.py`
— 27 tests that went red in the sandbox and stayed red for two days — and this
guard was green through all of it, because those files carry the hazard in two
shapes it could not see: a shebang opening its own line inside a multi-line
`textwrap.dedent` body, and `/usr/bin/env` as a plain argv element (not a
shebang at all — it raises FileNotFoundError before any stub is reached).
`testlib.shebang_scan.line_is_offender` enumerates all three; each has its own
positive control below.

HOW TO SATISFY IT
-----------------
Use `testlib.mockbin.write_exec(path, body)` — it owns the shebang (`/bin/sh`)
and RAISES if a call site supplies its own. To EXEC an interpreter, resolve it
with `shutil.which(...)` once, to an absolute path. Do not add an ALLOWLIST
entry to get green; the allowlist is for sites that solve the problem a
different, verified way, and every entry must say which way.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import shebang_scan as S  # noqa: E402

SCAN_ROOT = REPO / "scripts"
# 🔴 `test_*.sh` was ADDED after this scan was measured blind to the one file
# that carried the defect. `scripts/tests/test_release_wrapper.sh` writes three
# runtime stubs and gave all three the `/usr/bin/env` shebang; the glob said
# `test_*.py`, so this guard was structurally incapable of seeing it — the same
# "narrow the glob until it covers nothing" shape it exists to catch. Nothing
# noticed for a SECOND reason: no gate ran that file at all until GUARD 7's
# SHELL_TESTS brought it under `run-tests.sh`, and its first run inside the nix
# sandbox was RED — with case 1 passing for the wrong reason, which is worse.
PATTERNS = ("test_*.py", "conftest.py", "test_*.sh")

# --- THE PINNED ALLOWLIST ------------------------------------------------------
# (relative path, substring the offending line must contain, why it is safe).
#
# Accounting works in BOTH directions, deliberately — the same discipline as
# run-tests.sh's EXPECTED_SKIPS:
#   * an offender matching no entry  → FAIL (a new call site appeared);
#   * an entry matching no offender  → FAIL (the site changed; re-verify and
#     delete the pin, rather than leaving a rubber stamp behind).
#
# 🔴 Nothing here may be `/usr/bin/env`. Two shapes qualify:
#   (a) the literal interpreter path is already absolute and sandbox-present
#       (`/bin/sh`, `sys.executable`);
#   (b) the string is not a shebang at all.
ALLOWLIST = [
    ("scripts/tests/test_playwright_nixos.py", "/bin/sh",
     "writes /bin/sh directly — absolute, present in the sandbox"),
    ("scripts/tests/test_notify_failure.py", "_bash",
     "interpolates a RESOLVED bash path (shutil.which), not /usr/bin/env"),
    ("scripts/claude-hooks/tests/test_claude_notify.py", "/bin/sh",
     "writes /bin/sh directly — absolute, present in the sandbox"),
    ("scripts/task-spec-drafter/tests/test_allowlist_covers_prompt_shapes.py",
     "/bin/sh",
     "writes /bin/sh directly — absolute, present in the sandbox"),
    ("scripts/task-spec-drafter/tests/test_ticket_status.py", "sys.executable",
     "points the stub at the RUNNING interpreter"),
    ("scripts/browser-bridge/tests/test_browser_agent.py", "sys.executable",
     "_write_exec() rewrites #!/usr/bin/env python3 -> sys.executable before "
     "writing; the two literal stub bodies below it are inputs to that rewrite"),
    ("scripts/browser-bridge/tests/test_browser_agent.py", "_write_exec(path,",
     "the two literal stub bodies are ARGUMENTS to _write_exec(), which rewrites "
     "the shebang before writing (see the entry above)"),
    ("scripts/dl-router/tests/test_backfill.py", "root",
     "not a shebang: a dl-router manifest header sentinel being mutated"),
    ("scripts/claude-hooks/tests/test_guard_core.py", "/usr/bin/doas",
     "not an argv the test EXECS: a parametrize list of wrapper-binary strings "
     "fed to gc.evaluate() as text, so the path is never resolved"),
    # 🔴 The needle is /bin/sh WITHOUT the leading hash-bang, and that is not a
    # style choice: a quote immediately followed by those two characters is
    # itself one of the scan's needles, so writing the fuller string here makes
    # THIS file its own offender. `test_this_guards_source_does_not_match_itself`
    # catches it — observed while adding this very entry.
    ("scripts/tests/test_mention_scan.py", "/bin/sh",
     "shape (b) — not a shebang the test writes or execs: a parametrize entry in "
     "the mention scanner's NEGATIVE-case list, passed to scan_mentions() as a "
     "plain string to prove that a hash followed by a non-digit is not a "
     "mention. No file is created and nothing is run. It names /bin/sh rather "
     "than the env-based interpreter path so the pin stays inside shape (a) too"),
]


def _offenders() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for pattern in PATTERNS:
        for path, lineno, line in S.scan_tree(SCAN_ROOT, pattern):
            hits.append((str(path.relative_to(REPO)), lineno, line))
    return sorted(hits)


def _matches(entry, hit) -> bool:
    rel, needle, _why = entry
    return hit[0] == rel and needle in hit[2]


def test_no_test_writes_a_usr_bin_env_shebang_at_runtime():
    """The scan itself. Every hit must be pinned."""
    unpinned = [h for h in _offenders()
                if not any(_matches(e, h) for e in ALLOWLIST)]
    assert not unpinned, (
        "a test writes its own shebang — use testlib.mockbin.write_exec:\n  "
        + "\n  ".join(f"{p}:{n}: {l}" for p, n, l in unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    An entry that stops matching means the call site moved or changed. Left in
    place it silently pre-approves whatever appears at that path next.
    """
    hits = _offenders()
    stale = [f"{rel} ~ {needle!r} ({why})"
             for rel, needle, why in ALLOWLIST
             if not any(_matches((rel, needle, why), h) for h in hits)]
    assert not stale, (
        "ALLOWLIST entries match nothing — the site changed. Re-verify how that "
        "file writes its shebang, then delete the pin:\n  " + "\n  ".join(stale))


def test_positive_control_the_scan_can_actually_find_an_offender(tmp_path):
    """🔴 POSITIVE CONTROL for the two empty-list assertions above.

    `not unpinned` and `not stale` are ZEROS, and a zero is indistinguishable
    from a scan wired to nothing (RULES.md). Feed the SAME `scan_tree` a tree
    that MUST produce a non-zero count and watch the number move.
    """
    bad = tmp_path / "nested" / "test_bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("x = 1\n" + S.offending_source_line() + "\n",
                   encoding="utf-8")
    hits = S.scan_tree(tmp_path, "test_*.py")
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    assert hits[0][1] == 2


def test_positive_control_the_scan_finds_a_bare_shebang_line(tmp_path):
    """🔴 POSITIVE CONTROL for shape 2 — the shape that let 27 tests through.

    A multi-line `textwrap.dedent` stub body puts the shebang at the start of
    its own line, with no quote in front of it. The original one-shape scan
    could not see that and reported a clean zero for two days.
    """
    bad = tmp_path / "test_bad_multiline.py"
    bad.write_text("body = '''\n" + S.offending_bare_shebang_line() + "\n'''\n",
                   encoding="utf-8")
    hits = S.scan_tree(tmp_path, "test_*.py")
    assert len(hits) == 1, f"scan cannot see a bare shebang line: {hits}"
    assert hits[0][1] == 2


def test_positive_control_the_scan_finds_an_argv_element(tmp_path):
    """🔴 POSITIVE CONTROL for shape 3 — not a shebang at all.

    `subprocess.run([<env>, "bash", …])` raises FileNotFoundError in the sandbox
    before any stub is reached. This is what actually produced the 27 failures.
    """
    bad = tmp_path / "test_bad_argv.py"
    bad.write_text("x = 1\n" + S.offending_argv_line() + "\n", encoding="utf-8")
    hits = S.scan_tree(tmp_path, "test_*.py")
    assert len(hits) == 1, f"scan cannot see an argv element: {hits}"
    assert hits[0][1] == 2


def test_positive_control_an_unpinned_offender_is_reported(tmp_path):
    """POSITIVE CONTROL for the ALLOWLIST filter, not just the scanner.

    The scan finding a line and the assertion REPORTING it are two different
    things — an allowlist entry that is too broad would swallow a real offender
    silently. Push a fabricated hit through `_matches` against the real list.
    """
    fabricated = ("scripts/some-new-suite/tests/test_new.py", 7,
                  S.offending_source_line())
    assert not any(_matches(e, fabricated) for e in ALLOWLIST), (
        "the ALLOWLIST matched a file that is not in it — an entry is too broad")


def test_scan_root_actually_contains_test_files():
    """Guard the guard's INPUT. A wrong SCAN_ROOT yields an empty file set, and
    every assertion above then passes vacuously."""
    files = [p for pat in PATTERNS for p in SCAN_ROOT.rglob(pat)]
    assert len(files) > 50, f"SCAN_ROOT={SCAN_ROOT} found only {len(files)} files"


def test_the_first_line_exemption_is_line_one_only():
    """`line_is_offender` exempts line 1 (the module's own shebang, never
    exec'd). Pin that the exemption does not leak to line 2."""
    offending = S.offending_source_line()
    assert S.line_is_offender(1, offending) is False
    assert S.line_is_offender(2, offending) is True


def test_this_guards_source_does_not_match_itself():
    """The self-match trap: a scan whose needles appear in its own source
    reports itself. Both this file and the scanner must come back clean."""
    for path in (Path(__file__).resolve(), Path(S.__file__).resolve()):
        assert S.scan_file(path) == [], f"{path.name} matches its own scan"
