"""Guards for the civitai-app-fleet skill's three scripts.

🔴 EVERY CASE BELOW IS A DEFECT THAT ACTUALLY OCCURRED, not an imagined one.
`app_state.py` and `preflight.py` exist because prose telling the reader to be
careful had already failed twice in a single session on 2026-09-07:

  - the per-slug status view reported `withdrawn` for an app that was building
    fine, because a withdrawn duplicate of the same version sat on top of it;
  - a hand-rolled state parser had never heard of `deploying` and silently
    reported `approved` instead, under-reporting progress without ever erroring.

The second is the reason `parse_rows` RAISES on an unknown token rather than
defaulting: a fall-through is invisible, and invisibility is what made it cost
an hour. `test_unknown_deploy_state_raises` is the guard that keeps it loud —
delete the raise and that test goes red.

`fleet.py`'s tests were added a round later, after an audit found it shipping
with none: three columns fabricated affirmative values on git failure and its
exit code promised a coverage it did not have. Its tests all drive a REAL
failure path through the `_RUN` seam, because the happy path hid every one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "claude",
    "skills",
    "civitai-app-fleet",
)


def _load(name: str):
    path = os.path.join(SKILL_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_cvt_{name}", path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


app_state = _load("app_state")
preflight = _load("preflight")
fleet = _load("fleet")


# The shape `civitai app status` actually prints. The custom-generators pair is
# copied from the real incident: two rows, same version, the withdrawn one
# listed FIRST because it is newer.
STATUS = """\
BLOCK_ID                    VERSION  STATUS     DEPLOY        SOURCE   SUBMITTED   URL
custom-generators           0.6.5    withdrawn  -             -        2026-09-08  -
sensei                      0.1.21   approved   deploying     -        2026-09-08  -
custom-generators           0.6.5    approved   building      -        2026-09-08  -
app-requests                0.4.1    approved   live          -        2026-09-08  https://app-requests.example.test/
app-requests                0.4.0    approved   live          abc1234  2026-09-05  https://app-requests.example.test/
prompt-library              0.1.0    withdrawn  -             -        2026-09-01  -
gen-matrix                  0.8.8    approved   failed        -        2026-09-04  -

note: the server returned the newest 100 submissions — older ones may exist.
"""
# Hosts are `*.example.test`, not the real per-app subdomains: devrc is a PUBLIC
# repo and the live hostnames are internal topology. `test_no_client_hostnames.py`
# enforces this and caught the first draft of this fixture. Nothing here parses
# the URL column, so the substitution costs the fixture nothing.


def rows():
    return app_state.parse_rows(STATUS)


# --- the withdrawn-duplicate mask (incident 1) --------------------------------


def test_withdrawn_duplicate_does_not_mask_the_real_row():
    """The exact failure: same version, two rows, withdrawn one newer."""
    got = app_state.resolve(rows(), "custom-generators", "0.6.5")
    assert (got["review"], got["deploy"]) == ("approved", "building"), got


def test_withdrawn_is_still_the_answer_when_it_is_the_only_row():
    """Ignoring withdrawn rows wholesale would be the opposite bug."""
    got = app_state.resolve(rows(), "prompt-library", "0.1.0")
    assert got["review"] == "withdrawn", got


def test_two_non_withdrawn_rows_resolve_to_the_newest():
    """The tie-break the docstring did not state and nothing pinned. The CLI
    lists newest-first, so the first non-withdrawn row IS the newest — a
    `pending` submission above an already-live one resolves to `pending`, which
    is right, but it is a behaviour rather than an accident."""
    newer = "app-requests                0.4.1    pending    -             -        2026-09-09  -\n"
    got = app_state.resolve(app_state.parse_rows(newer + STATUS), "app-requests", "0.4.1")
    assert (got["review"], got["deploy"]) == ("pending", "-"), got


def test_absent_version_reports_none_rather_than_inventing_a_state():
    got = app_state.resolve(rows(), "custom-generators", "9.9.9")
    assert got["review"] == "none", got


# --- the unknown-state fall-through (incident 2) ------------------------------


def test_deploying_is_a_known_state():
    """`deploying` is real and sits between building and live. The parser that
    omitted it reported `approved` and never erred."""
    got = app_state.resolve(rows(), "sensei", "0.1.21")
    assert got["deploy"] == "deploying", got


def test_unknown_deploy_state_raises_rather_than_falling_through():
    bad = STATUS.replace("approved   building", "approved   teleporting")
    with pytest.raises(app_state.UnknownState) as excinfo:
        app_state.parse_rows(bad)
    assert "teleporting" in str(excinfo.value)


def test_unknown_review_state_raises():
    bad = STATUS.replace("0.4.1    approved", "0.4.1    marinated")
    with pytest.raises(app_state.UnknownState):
        app_state.parse_rows(bad)


def test_preview_live_is_a_known_state():
    """🔴 REGRESSION. `preview-live` is real — it is the deploy state of
    `w6-ui-dogfood` in the live listing — and the first draft of DEPLOY_STATES
    omitted it, so `app_state.py` raised on the ACTUAL `civitai app status`
    output. Found by `fleet.py`'s first real run, one hour after the file
    merged: the guard firing on its own author. Every other member is exercised
    by the STATUS fixture; this one needs its own row because no repo in the
    fleet is currently in preview.
    """
    row = "w6-ui-dogfood               0.2.0    withdrawn  preview-live  -        2026-08-29  -\n"
    rows_with_preview = app_state.parse_rows(STATUS + row)
    got = app_state.resolve(rows_with_preview, "w6-ui-dogfood", "0.2.0")
    assert got["deploy"] == "preview-live", got


def test_deploy_states_contains_no_invented_members():
    """The mirror of the bug above, and the other half of the same mistake: the
    first draft also contained `queued`, which appears NOWHERE in the platform's
    output. A set that is too WIDE fails silently — it accepts a renamed or
    typo'd state as valid — so membership is pinned to what was actually
    observed, and widening it stays a deliberate edit with provenance.
    """
    assert app_state.DEPLOY_STATES == {
        "-",
        "building",
        "deploying",
        "live",
        "failed",
        "preview-live",
    }


def test_review_states_contains_no_invented_members():
    """🔴 The comment on REVIEW_STATES says "same standard as DEPLOY_STATES",
    and DEPLOY_STATES' standard is provenance PLUS an exact-set pin. An audit
    measured the gap: widening REVIEW_STATES with `queued` or `escalated`
    SURVIVED a fully green suite, because its only guard pinned the single
    literal `marinated`. The round closed a named hazard with a sentence; this
    is the pin the sentence claimed.
    """
    assert app_state.REVIEW_STATES == {"pending", "approved", "rejected", "withdrawn"}


def test_prose_and_header_lines_are_skipped_not_raised():
    """The CLI prints a header and trailing notes; neither is a row nor an error."""
    assert all(r["app"] != "note:" for r in rows())
    assert len(rows()) == 7


# --- the submit floor ---------------------------------------------------------


def test_submit_floor_counts_withdrawn_versions():
    """A withdrawn submission still occupies the floor — two apps in the fleet
    were in this state and could not re-submit at the same version."""
    assert app_state.submit_floor(rows(), "prompt-library") == "0.1.0"


def test_submit_floor_is_the_highest_not_the_newest():
    assert app_state.submit_floor(rows(), "app-requests") == "0.4.1"


def test_submit_floor_none_for_unknown_app():
    assert app_state.submit_floor(rows(), "never-submitted") is None


# --- preflight ----------------------------------------------------------------


def _tree(tmp_path, *, manifest_version="1.0.0", package_version="1.0.0",
          build="pnpm run build", lockfiles=("pnpm-lock.yaml",)):
    manifest = {"blockId": "x", "version": manifest_version, "outputDir": "dist"}
    if build is not None:
        manifest["buildCommand"] = build
    (tmp_path / "block.manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "package.json").write_text(json.dumps({"version": package_version}))
    for name in lockfiles:
        (tmp_path / name).write_text("")
    return str(tmp_path)


def test_preflight_passes_a_correct_pnpm_tree(tmp_path):
    assert preflight.run(_tree(tmp_path), floor="0.9.9")


def test_preflight_catches_version_lockstep_break(tmp_path):
    root = _tree(tmp_path, manifest_version="1.0.1", package_version="1.0.0")
    with pytest.raises(preflight.Failure, match="lockstep"):
        preflight.run(root)


def test_preflight_catches_pnpm_manifest_with_npm_lockfile(tmp_path):
    """buildCommand says pnpm; only package-lock.json committed."""
    root = _tree(tmp_path, build="pnpm run build", lockfiles=("package-lock.json",))
    with pytest.raises(preflight.Failure, match="pnpm-lock.yaml"):
        preflight.run(root)


def test_preflight_catches_missing_buildcommand_on_a_pnpm_tree(tmp_path):
    """The generate-from-model defect verbatim: no buildCommand, pnpm lockfile,
    so the builder's legacy `npm ci` default runs against no package-lock.json."""
    root = _tree(tmp_path, build=None, lockfiles=("pnpm-lock.yaml",))
    with pytest.raises(preflight.Failure, match="legacy"):
        preflight.run(root)


def test_preflight_catches_two_lockfiles(tmp_path):
    root = _tree(tmp_path, lockfiles=("pnpm-lock.yaml", "package-lock.json"))
    with pytest.raises(preflight.Failure, match="more than one lockfile"):
        preflight.run(root)


def test_preflight_rejects_double_spaced_build_command(tmp_path):
    """The platform's regex is anchored, so `pnpm  run build` is rejected there
    while naive first-token parsing reads it as a valid pnpm build."""
    root = _tree(tmp_path, build="pnpm  run build")
    with pytest.raises(preflight.Failure, match="allowlist"):
        preflight.run(root)


def test_preflight_blocks_a_version_at_or_below_the_floor(tmp_path):
    root = _tree(tmp_path, manifest_version="1.0.0", package_version="1.0.0")
    with pytest.raises(preflight.Failure, match="submit floor"):
        preflight.run(root, floor="1.0.0")


def test_preflight_allows_a_version_above_the_floor(tmp_path):
    root = _tree(tmp_path, manifest_version="1.0.1", package_version="1.0.1")
    assert preflight.run(root, floor="1.0.0")


# --- the offline seam itself --------------------------------------------------


def test_read_status_uses_the_file_seam_and_never_shells_out(tmp_path, monkeypatch):
    """If this regressed to always calling the CLI, the suite would need auth
    and a network — and would fail closed in the nix sandbox.

    🔴 Patches `app_state._RUN`, NOT `app_state.subprocess.run`. The latter is
    the SHARED subprocess module, and patching it through this module reached
    into devrc's suite-wide no-launch guard: measured 2026-09-08, the earlier
    version of this test took the full suite from 7 pre-existing failures to
    18, every extra one in a subprocess-using test in another file. A
    same-named, same-count dummy file caused zero extra failures, which is how
    the content — this line — was named rather than xdist redistribution.
    """
    dump = tmp_path / "status.txt"
    dump.write_text(STATUS)
    monkeypatch.setenv("CIVITAI_STATUS_FILE", str(dump))

    def explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("read_status shelled out despite CIVITAI_STATUS_FILE")

    monkeypatch.setattr(app_state, "_RUN", explode)
    assert app_state.read_status() == STATUS


def test_read_status_shells_out_when_no_file_seam_is_set(monkeypatch):
    """The positive control for the test above: with the env var cleared,
    `read_status` MUST reach `_RUN`. Without this, the seam test could pass
    against a function that never calls the CLI under any circumstances, and
    would be asserting nothing."""
    monkeypatch.delenv("CIVITAI_STATUS_FILE", raising=False)
    calls = []

    class Result:
        returncode = 0
        stdout = STATUS
        stderr = ""

    monkeypatch.setattr(app_state, "_RUN", lambda *a, **k: (calls.append(a), Result())[1])
    assert app_state.read_status() == STATUS
    assert calls and calls[0][0][:3] == ["civitai", "app", "status"], calls


# --- fleet.py: an unreadable repo must never render as a measured one ---------
#
# 🔴 `fleet.py` shipped with ZERO tests in the first draft, and an audit found
# three columns fabricating affirmative values plus an exit code that promised
# coverage it did not have. Every test below drives a REAL failure path — git
# exiting non-zero — through the `_RUN` seam, because the defects were only
# reachable when git failed and the happy path hid all of them.


class _FakeGit:
    """A `subprocess.run` stand-in. `ok` maps an argv-suffix to stdout; anything
    not listed exits 1, which is what a broken/absent repo actually does."""

    def __init__(self, ok: dict[str, str]):
        self.ok = ok
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kw):
        self.calls.append(list(argv))
        key = " ".join(argv[3:]) if argv[:1] == ["git"] else " ".join(argv)
        for pattern, out in self.ok.items():
            if key.startswith(pattern):
                return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()


def _fleet_row(monkeypatch, tmp_path, ok: dict[str, str], **kw):
    monkeypatch.setattr(fleet, "WORKSPACE", str(tmp_path))
    (tmp_path / "repo").mkdir(exist_ok=True)
    fake = _FakeGit(ok)
    monkeypatch.setattr(fleet, "_RUN", fake)
    return fleet.inspect("repo", "owner/repo", "an-app", **kw), fake


_GIT_DIR = {"rev-parse --git-dir": ".git"}


def test_a_directory_that_is_not_a_repo_is_an_error_not_a_row(monkeypatch, tmp_path):
    """🔴 It previously reported checked_out='(detached)' and dirty_files='0' —
    and '0 dirty' is exactly the value that makes a caller proceed."""
    monkeypatch.setattr(fleet, "WORKSPACE", str(tmp_path))
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(fleet, "_RUN", _FakeGit({}))
    row = fleet.inspect("repo", "owner/repo", "an-app")
    assert row["error"] == "not a git repository", row
    assert "dirty_files" not in row and "checked_out" not in row, row


def test_an_unreadable_lockfile_is_not_reported_as_no_lockfile(monkeypatch, tmp_path):
    """🔴 The highest-consequence column. `lockfile()` returned the literal
    "none" on git failure, so a repo with a pnpm-lock.yaml it could not read
    reported as having none — the input that makes the manifest/lockfile
    agreement check wrong in the direction that breaks a platform build."""
    row, _ = _fleet_row(monkeypatch, tmp_path, _GIT_DIR)
    assert row["lockfile"] == fleet.UNREADABLE, row
    assert row["lockfile"] != "none"


def test_any_unreadable_column_sets_error_and_a_nonzero_exit(monkeypatch, tmp_path):
    """🔴 The exit code promised 'non-zero if any repo could not be inspected'
    while the flag was set only for a MISSING DIRECTORY."""
    row, _ = _fleet_row(monkeypatch, tmp_path, _GIT_DIR)
    assert "error" in row and "unreadable" in row["error"], row
    assert "lockfile" in row["error"] and "default_branch" in row["error"], row


def test_a_failed_fetch_is_recorded_not_shrugged_off(monkeypatch, tmp_path):
    """A fetch that failed leaves every column describing a STALE ref, so it is
    recorded rather than shrugged off — as a `fetch` value, NOT as an `error`.

    An earlier revision of this test asserted `"fetch failed" in row["error"]`,
    which pinned the over-correction rather than the behaviour: folding it into
    `error` made `main()` print `!!` INSTEAD of the row. See
    `test_a_failed_fetch_does_not_discard_the_row` for the half that matters.
    """
    ok = {**_GIT_DIR, "branch --show-current": "main", "status --porcelain": ""}
    row, _ = _fleet_row(monkeypatch, tmp_path, ok)
    assert row["fetch"] == "FAILED", row


def test_no_fetch_does_not_write_to_the_clone(monkeypatch, tmp_path):
    """The clones are shared; a fetch is a write to a tree other sessions are
    standing in, so it must be opt-out and must actually not happen."""
    row, fake = _fleet_row(monkeypatch, tmp_path, _GIT_DIR, fetch=False)
    assert row["fetch"] == "skipped", row
    assert not any("fetch" in c for c in fake.calls), fake.calls


def test_fetch_happens_by_default(monkeypatch, tmp_path):
    """Positive control for the test above — otherwise it would pass against a
    function that never fetches at all."""
    _row, fake = _fleet_row(monkeypatch, tmp_path, _GIT_DIR)
    assert any("fetch" in c for c in fake.calls), fake.calls


def test_vitest_projects_distinguishes_single_from_multi(monkeypatch):
    """🔴 The first version of this test asserted the result was in
    {"1","2+","?"} — every value the function can return, so it was
    tautological — plus a spelled check on the docstring. An audit mutated the
    body to `return "1"` unconditionally and it SURVIVED a green suite, which is
    exactly the `--project node` trap the module docstring describes: a wrong
    filter matches nothing and RUNS nothing, reading like a passing filter.
    """
    multi = _FakeGit({**_GIT_DIR, "show r:vite.config.ts": "test: { projects: [a, b] }"})
    monkeypatch.setattr(fleet, "_RUN", multi)
    assert fleet.vitest_projects("/x", "r") == "2+"

    single = _FakeGit({**_GIT_DIR, "show r:vite.config.ts": "test: { environment: 'node' }"})
    monkeypatch.setattr(fleet, "_RUN", single)
    assert fleet.vitest_projects("/x", "r") == "1"

    monkeypatch.setattr(fleet, "_RUN", _FakeGit(_GIT_DIR))
    assert fleet.vitest_projects("/x", "r") == fleet.UNREADABLE


def test_checked_out_and_dirty_are_never_fabricated(monkeypatch, tmp_path):
    """Both survived mutation after round 2 claimed to have fixed them.
    `dirty_files: "0"` is the value `fleet.py` itself calls 'precisely the value
    that makes a caller proceed'."""
    ok = {**_GIT_DIR, "symbolic-ref": "refs/remotes/origin/main"}
    row, _ = _fleet_row(monkeypatch, tmp_path, ok)
    assert row["checked_out"] == fleet.UNREADABLE, row
    assert row["dirty_files"] == fleet.UNREADABLE, row
    assert row["lockstep_guard"] == fleet.UNREADABLE, row


def test_a_failed_fetch_does_not_discard_the_row(monkeypatch, tmp_path):
    """🔴 THE OVER-CORRECTION. Round 2 folded a failed fetch into `error`, and
    an error row prints as a bare `!!` INSTEAD of the row — so one unreachable
    remote collapsed all seven rows and the inventory emitted nothing. A failed
    fetch is a staleness warning: the columns WERE read."""
    ok = {
        **_GIT_DIR,
        "symbolic-ref": "refs/remotes/origin/main",
        "branch --show-current": "main",
        "status --porcelain": "",
        "ls-tree --name-only origin/main:src": "manifest.test.ts",
        "ls-tree --name-only origin/main": "pnpm-lock.yaml",
        "show origin/main:block.manifest.json": '{"version": "1.0.0", "buildCommand": "pnpm run build"}',
        "show origin/main:package.json": '{"version": "1.0.0"}',
        "show origin/main:vite.config.ts": "projects: []",
    }
    row, _ = _fleet_row(monkeypatch, tmp_path, ok)  # every read succeeds; only fetch fails
    assert row["fetch"] == "FAILED", row
    assert "error" not in row, row
    assert row["lockfile"] == "pnpm-lock.yaml", row


def test_main_exit_code_is_nonzero_when_a_row_could_not_be_read(monkeypatch, tmp_path, capsys):
    """🔴 `main()` had NO test, so a mutant returning 0 unconditionally survived
    — the exact claim ('non-zero if any repo could not be inspected') that the
    error-flag fix was made to honour."""
    monkeypatch.setattr(fleet, "WORKSPACE", str(tmp_path))  # no repos exist here
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    assert fleet.main() == 1
    assert "checkout missing" in capsys.readouterr().out


_READABLE_ROW = {
    "app": "an-app", "dir": "repo", "slug": "o/r", "fetch": "ok",
    "default_branch": "main", "checked_out": "main", "dirty_files": "0",
    "manifest_version": "1.0.0", "package_version": "1.0.0",
    "build_command": "pnpm run build", "lockfile": "pnpm-lock.yaml",
    "vitest_projects": "2+", "lockstep_guard": "manifest.test",
}


def _one_row(monkeypatch, **over):
    """A single fully-readable row, so a test can exercise main() with REAL rows
    rather than REPOS=[] — an empty list cannot see anything that depends on a
    row's contents, which is how the `--no-fetch`-exits-0 half went unpinned."""
    row = dict(_READABLE_ROW)
    row.update(over)
    monkeypatch.setattr(fleet, "REPOS", [("repo", "o/r", "an-app")])
    monkeypatch.setattr(fleet, "inspect", lambda *a, **k: dict(row))
    return row


def _fleet_rows(monkeypatch, *overrides):
    """N fully-readable rows, keyed by app slug so one main() run can hold rows
    that must render DIFFERENTLY from each other — which is the only way to
    assert that two cells are distinct strings rather than the same one twice.

    Each override must carry a distinct `app`; `dir`/`slug` are derived from it
    so THIS FIXTURE'S `REPOS` and THIS FIXTURE'S fake `inspect` cannot disagree
    about which row belongs to which repo.

    🔴 THAT IS FIXTURE-INTERNAL CONSISTENCY AND NOTHING MORE. The sentence it
    replaces ("so `REPOS` and the fake `inspect` cannot drift apart") read as
    reassurance about the `inspect()` -> `main()` seam, which this fixture does
    not touch at all: it REPLACES `fleet.inspect`, so every test built on it
    pins what `main()` does with a row of the shape written HERE, never that
    `inspect()` produces a row of that shape. Measured: renaming
    `manifest_version` in `inspect()` left this file at 51 passed while a real
    `fleet.py --no-fetch --no-platform` died with `KeyError: 'manifest_version'`
    at the printer, on its first row. That seam is pinned by
    `test_the_keys_inspect_produces_cover_the_keys_every_consumer_reads` and
    `test_a_real_inspect_row_renders_through_main`, which deliberately do NOT
    use this fixture.
    """
    rows = []
    for over in overrides:
        row = dict(_READABLE_ROW)
        row.update(over)
        row["dir"] = row["app"]
        row["slug"] = f"o/{row['app']}"
        rows.append(row)
    apps = [r["app"] for r in rows]
    assert len(set(apps)) == len(apps), f"app slugs must be distinct: {apps}"
    monkeypatch.setattr(fleet, "REPOS", [(r["dir"], r["slug"], r["app"]) for r in rows])
    by_app = {r["app"]: r for r in rows}
    monkeypatch.setattr(fleet, "inspect", lambda _d, _s, app, **_k: dict(by_app[app]))
    return rows


def _with_platform(monkeypatch, tmp_path, status=STATUS):
    """Point `fleet.main()` at a captured `civitai app status` dump, so its
    platform-enrichment branch actually RUNS.

    🔴 This is the setup that did not exist. Round 3 rewrote the enrichment
    guard and an audit measured the coverage at ZERO: reverting the predicate to
    the pre-round-3 `if "error" in row:`, replacing it with `if False:`, and
    planting a bare `raise` inside the loop body EACH left the suite at 45
    passed. The `else:` branch of main()'s platform block was never executed by
    any test, because the only test that set CIVITAI_STATUS_FILE also set
    `REPOS = []` — an empty fleet cannot enter a per-row loop.

    Pinning `sys.modules["app_state"]` is the same fix as in
    `test_an_unknown_platform_state_is_not_swallowed_by_fleet`: `fleet.main()`
    does a plain `import app_state`, which would otherwise load a SECOND module
    object from the same file.
    """
    dump = tmp_path / "status.txt"
    dump.write_text(status)
    monkeypatch.setenv("CIVITAI_STATUS_FILE", str(dump))
    monkeypatch.setitem(sys.modules, "app_state", app_state)


def test_a_failed_fetch_exits_nonzero_even_though_every_column_read(monkeypatch, capsys):
    """🔴 An audit dropped `failed_fetch or` from main()'s return and the whole
    suite stayed green: a stale inventory would have exited 0, which is the
    round-1 bug this file's docstring exists to prevent."""
    _one_row(monkeypatch, fetch="FAILED")
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-platform"])
    assert fleet.main() == 1
    capsys.readouterr()


def test_no_fetch_alone_exits_zero(monkeypatch, capsys):
    """The other half, and the reason the pair must use a REAL row: `skipped` is
    deliberately NOT `FAILED` — you asked for it. Widening the failure set to
    include `skipped` would break this."""
    _one_row(monkeypatch, fetch="skipped")
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    assert fleet.main() == 0
    capsys.readouterr()


def test_the_fetch_state_appears_in_the_table_and_the_stale_note(monkeypatch, capsys):
    """The docstring claims a `fetch` column and a trailing stale note. Deleting
    either left the suite green, so both are pinned — header included, since
    dropping it silently misaligns every column after it."""
    _one_row(monkeypatch, fetch="skipped")
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    cap = capsys.readouterr()
    assert "fetch" in cap.out.splitlines()[0], cap.out
    assert "skipped" in cap.out, cap.out
    assert "may be stale" in cap.err and "an-app" in cap.err, cap.err


def test_the_stale_note_covers_skipped_as_well_as_failed(monkeypatch, capsys):
    """Narrowing the stale set to {"FAILED"} undoes F2 — `--no-fetch` produces
    byte-identical staleness — and survived a green suite."""
    _one_row(monkeypatch, fetch="skipped")
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    assert "may be stale" in capsys.readouterr().err


def test_an_unreadable_column_does_not_discard_the_readable_ones(monkeypatch, capsys):
    """🔴 The round that wrote 'discarding probably-correct data is worse than
    serving it with a caveat' applied it to the fetch axis only: one unreadable
    column still replaced the row with `!!`, losing ten readable facts."""
    _one_row(monkeypatch, vitest_projects=fleet.UNREADABLE,
             error="unreadable: vitest_projects")
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    rc = fleet.main()
    out = capsys.readouterr().out
    assert "!!" not in out, out
    assert "pnpm-lock.yaml" in out and "manifest.test" in out, out
    assert rc == 1, "an unreadable column must still exit non-zero"


def test_a_row_with_no_columns_is_still_replaced(monkeypatch, capsys):
    """The other side: a missing checkout has nothing to print, so `!!` is
    right. Without this the test above would pass against a main() that never
    prints `!!` at all."""
    monkeypatch.setattr(fleet, "REPOS", [("repo", "o/r", "an-app")])
    monkeypatch.setattr(fleet, "inspect", lambda *a, **k: {
        "app": "an-app", "dir": "repo", "slug": "o/r", "error": "checkout missing"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    assert fleet.main() == 1
    assert "!! checkout missing" in capsys.readouterr().out


def test_vitest_absent_is_distinct_from_unreadable(monkeypatch):
    """`git show <ref>:vite.config.ts` fails for a missing FILE and an
    unreadable REF alike; collapsing them made a readable non-vite repo look
    like a failed read — the conflation the sentinel exists to prevent."""
    absent = _FakeGit({**_GIT_DIR, "rev-parse --verify": "abc123"})
    monkeypatch.setattr(fleet, "_RUN", absent)
    assert fleet.vitest_projects("/x", "origin/main") == "no-vite"

    monkeypatch.setattr(fleet, "_RUN", _FakeGit(_GIT_DIR))  # ref unreadable too
    assert fleet.vitest_projects("/x", "origin/main") == fleet.UNREADABLE


def test_main_exit_code_is_zero_when_every_row_reads(monkeypatch, capsys):
    """The positive control: without it the test above passes against a main()
    that returns 1 unconditionally."""
    monkeypatch.setattr(fleet, "REPOS", [])
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    assert fleet.main() == 0
    capsys.readouterr()


def test_no_platform_never_renders_a_real_deploy_state(monkeypatch, capsys):
    """`-` is a REAL deploy state meaning 'no deploy for this row'. Rendering it
    for 'not consulted' makes the two indistinguishable — and survived."""
    monkeypatch.setattr(fleet, "REPOS", [("repo", "o/r", "an-app")])
    monkeypatch.setattr(fleet, "inspect", lambda *a, **k: {
        "app": "an-app", "fetch": "skipped", "default_branch": "main",
        "checked_out": "main", "dirty_files": "0", "manifest_version": "1.0.0",
        "package_version": "1.0.0", "build_command": "pnpm run build",
        "lockfile": "pnpm-lock.yaml", "vitest_projects": "2+",
        "lockstep_guard": "manifest.test",
    })
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    out = capsys.readouterr().out
    assert "not-consulted" in out, out


def test_an_unknown_platform_state_is_not_swallowed_by_fleet(monkeypatch, tmp_path):
    """🔴 The broad `except Exception` caught UnknownState, muting the guard
    that found `preview-live` — through the entry point the skill says to run
    first. Neutering the re-raise left the suite GREEN, so it is pinned here."""
    dump = tmp_path / "status.txt"
    dump.write_text(STATUS.replace("approved   building", "approved   teleporting"))
    monkeypatch.setenv("CIVITAI_STATUS_FILE", str(dump))
    monkeypatch.setattr(fleet, "REPOS", [])
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch"])

    # 🔴 `fleet.main()` does a plain `import app_state`, while this module loads
    # the same file under the private name `_cvt_app_state`. Without this line
    # those are TWO module objects with two distinct `UnknownState` classes, and
    # `pytest.raises(app_state.UnknownState)` does not catch the one fleet
    # raises — the test fails while the behaviour is correct. Pinning the name
    # makes both halves the same class.
    monkeypatch.setitem(sys.modules, "app_state", app_state)

    with pytest.raises(app_state.UnknownState):
        fleet.main()


# --- the table's columns must not shift, and its markers must not be invented --


def _column_starts(out: str) -> list[dict[str, int]]:
    """Where `guard`, `fetch` and `platform` begin, in the header and each row.

    Pins a RELATIONSHIP — every row agrees with the header — rather than the
    literal offsets, which a deliberate column-width change is allowed to move.
    """
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("-")]
    hdr, body = lines[0], lines[1:]
    return [
        {"guard": hdr.index("guard"), "fetch": hdr.index("fetch"),
         "platform": hdr.index("platform")}
    ] + [
        {"guard": ln.index("version-lockstep"), "fetch": ln.index("skipped"),
         "platform": ln.index("not-consulted")}
        for ln in body
    ]


def _vitest(config: str | None, *, ref_exists: bool = True) -> str:
    """Whatever `vitest_projects()` really returns for one repo shape."""
    ok = dict(_GIT_DIR)
    if config is not None:
        ok["show r:vite.config.ts"] = config
    if ref_exists:
        ok["rev-parse --verify"] = "abc123"
    saved = fleet._RUN
    fleet._RUN = _FakeGit(ok)
    try:
        return fleet.vitest_projects("/x", "r")
    finally:
        fleet._RUN = saved


def test_the_proj_column_never_shifts_the_columns_after_it(monkeypatch, capsys):
    """🔴 REGRESSION. `no-vite` is 7 characters and the cell was `:>4`; Python's
    width specifier PADS but never TRUNCATES, so that one row pushed `guard`,
    `fetch` and `platform` three columns right while every other row and the
    header stayed put. Measured before the fix: `guard` at 112 in the header and
    in a `2+` row, at 115 in a `no-vite` row.

    The values are taken from `vitest_projects()` ITSELF rather than from a
    literal list, so a new sentinel added to that function is covered here
    without anyone remembering to update this test — the case that produced the
    defect, since `no-vite` was itself a new sentinel.
    """
    every_value = {
        _vitest("test: { projects: [a, b] }"),   # "2+"
        _vitest("test: { environment: 'node' }"),  # "1"
        _vitest(None, ref_exists=True),          # "no-vite"
        _vitest(None, ref_exists=False),         # "?"
    }
    assert len(every_value) == 4, every_value  # each branch really is distinct

    _fleet_rows(monkeypatch, *[
        {"app": f"app{i}", "fetch": "skipped", "vitest_projects": value,
         "lockstep_guard": "version-lockstep"}
        for i, value in enumerate(sorted(every_value))
    ])
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    out = capsys.readouterr().out

    starts = _column_starts(out)
    assert len(starts) == 5, out  # header + one row per sentinel
    assert all(s == starts[0] for s in starts), (starts, out)


def test_an_unreadable_version_does_not_print_a_lockstep_break_marker(monkeypatch, capsys):
    """🔴 REGRESSION. `!` is documented one line above its own cell as 'lockstep
    broken — loud', and it was printed whenever the two strings DIFFERED — but
    `?` differs from every real version, so a row whose manifest version could
    not be read rendered `?/5.2.9!`, asserting a version-lockstep violation from
    a comparison that never happened. Both readings are still shown; only the
    assertion is dropped.
    """
    _fleet_rows(
        monkeypatch,
        {"app": "unread-mf", "manifest_version": fleet.UNREADABLE,
         "package_version": "5.2.9"},
        {"app": "unread-pkg", "manifest_version": "6.1.4",
         "package_version": fleet.UNREADABLE},
        # The positive control, and the reason this is not just "no `!` ever":
        # two READ versions that disagree must still shout.
        {"app": "real-break", "manifest_version": "2.4.1",
         "package_version": "3.7.0"},
    )
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    out = capsys.readouterr().out

    assert "?/5.2.9" in out and "?/5.2.9!" not in out, out
    assert "6.1.4/?" in out and "6.1.4/?!" not in out, out
    assert "2.4.1/3.7.0!" in out, out


# --- the platform-enrichment loop, which had ZERO tests executing it ----------


def test_platform_state_is_enriched_onto_a_row_the_platform_knows(monkeypatch, tmp_path, capsys):
    """INVARIANT GUARD, not regression coverage — this passes at the pre-fix
    commit too. It exists because NOTHING executed the loop it covers: with a
    bare `raise` planted in the loop body the suite still reported 45 passed, so
    every other claim about this branch was vacuous.

    `sensei 0.1.21` resolves to `approved/deploying` in the STATUS fixture, a
    state no other assertion in this file names.
    """
    _with_platform(monkeypatch, tmp_path)
    _fleet_rows(monkeypatch, {"app": "sensei", "manifest_version": "0.1.21",
                              "package_version": "0.1.21"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch"])
    fleet.main()
    out = capsys.readouterr().out
    assert "approved/deploying" in out, out
    assert "not-consulted" not in out, out


def test_an_error_row_with_a_readable_version_still_gets_platform_state(monkeypatch, tmp_path, capsys):
    """INVARIANT GUARD for the round-3 delta itself, which shipped unexecuted —
    it is GREEN at that commit, because the behaviour there is already right.
    What was missing is any test that ran it: the predicate used to be `if
    "error" in row:`, denying platform state to a row with ONE unreadable
    column, and reverting that one expression left the suite at 45 passed. This
    is regression coverage against the PRE-round-3 predicate, not against
    round 3.

    `gen-matrix 0.8.8` is `approved/failed` in the fixture; the row carries an
    unreadable `vitest_projects` and the matching `error`, exactly the shape
    `test_an_unreadable_column_does_not_discard_the_readable_ones` uses.
    """
    _with_platform(monkeypatch, tmp_path)
    _fleet_rows(monkeypatch, {"app": "gen-matrix", "manifest_version": "0.8.8",
                              "package_version": "0.8.8",
                              "vitest_projects": fleet.UNREADABLE,
                              "error": "unreadable: vitest_projects"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch"])
    assert fleet.main() == 1  # the unreadable column still exits non-zero
    out = capsys.readouterr().out
    assert "approved/failed" in out, out
    assert "version-unread" not in out, out


def _platform_cells(out: str) -> list[str]:
    """The platform cell of every printed ROW, exactly.

    🔴 A SUBSTRING CHECK CANNOT TELL THESE SENTINELS APART: `version-unread`
    CONTAINS `unread`, so `"unread" in out` is satisfied by the wrong answer and
    an assertion built on it is walkable. The platform cell is the last column
    and none of its four values contains a space, so splitting is lossless.
    `!!` lines are excluded — a row with no columns never reaches the cell.
    """
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("-")]
    return [ln.split()[-1] for ln in lines[1:] if " !! " not in ln]


def test_the_four_platform_answers_are_four_distinct_strings(monkeypatch, tmp_path, capsys):
    """🔴 REGRESSION for two arms, INVARIANT GUARD for two — labelled per arm
    below, because counting all four as regression coverage would overclaim.

    `not-consulted` acquired a second meaning: the printer's `!!` discriminator
    and the loop's version skip are DIFFERENT predicates, so a row could reach
    the table and still be skipped — and it then printed `not-consulted` on a
    run where the platform WAS consulted. The cell's own comment says `-` "is a
    REAL deploy state ... so it must never also mean 'not consulted'"; this is
    the same principle on the same cell, and it applies to all four answers.

    Not fixable by deleting the skip: `resolve(parsed, app, "?")` returns
    `none/-`, a real-looking platform state for a version nobody read.

    🔴 THE NAME USED TO CLAIM MORE THAN THE BODY DELIVERED. An earlier revision
    was called "three platform answers" and asserted TWO of them; the `unread`
    arm — the `except Exception` branch, the case where the CLI is absent or
    unauthed — was entered by no test in this file, and three isolated mutants
    survived a fully green suite because of it: `unread` -> `not-consulted`
    (which makes a failed CLI indistinguishable from `--no-platform`, the exact
    double meaning this taxonomy exists to remove), `unread` -> `version-unread`,
    and the floor's `unread` -> `none`. Every arm is exercised here now.
    """
    seen = {}

    # ARM 1 (REGRESSION, red before the round that added `version-unread`):
    # consulted, this row WAS inspected, its manifest version was not read.
    _with_platform(monkeypatch, tmp_path)
    _fleet_rows(
        monkeypatch,
        {"app": "custom-generators", "manifest_version": fleet.UNREADABLE,
         "package_version": "0.6.5", "error": "unreadable: manifest_version"},
        {"app": "sensei", "manifest_version": "0.1.21", "package_version": "0.1.21"},
    )
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch"])
    fleet.main()
    consulted = capsys.readouterr().out
    assert _platform_cells(consulted) == ["version-unread", "approved/deploying"], consulted
    # ... and the skipped row must not borrow a REAL state either.
    assert "none/-" not in consulted, consulted
    seen["consulted, row inspected, version unreadable"] = _platform_cells(consulted)[0]

    # ARM 2 (INVARIANT GUARD — green before this round; nothing ran it):
    # consulted, and the CLI failed for EVERY row. A status file that does not
    # exist makes `read_status` raise FileNotFoundError, which is not
    # `UnknownState`, so it lands in the `except Exception` arm — the same place
    # an absent or unauthed `civitai` binary lands. Both keys the arm writes are
    # asserted, because the floor is unanswerable here for the same reason the
    # state is: nothing was read.
    monkeypatch.setenv("CIVITAI_STATUS_FILE", str(tmp_path / "no-such-dump.txt"))
    monkeypatch.setitem(sys.modules, "app_state", app_state)
    _fleet_rows(monkeypatch, {"app": "sensei", "manifest_version": "0.1.21",
                              "package_version": "0.1.21"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--json"])
    fleet.main()
    cap = capsys.readouterr()
    row = json.loads(cap.out)[0]
    assert row["platform"] == "unread", row
    assert row["submit_floor"] == "unread", row
    assert "platform state unread" in cap.err, cap.err
    seen["consulted, the CLI failed for every row"] = row["platform"]

    # ARM 3 (REGRESSION, red before this round): consulted, but the row returned
    # early from `inspect()` and has no columns at all. It used to fall into the
    # version guard and be labelled `version-unread`, claiming a manifest was
    # consulted and found unreadable on a row where no file was ever opened.
    # Table mode never showed it — such a row prints `!!` — but `--json` did.
    _with_platform(monkeypatch, tmp_path)
    monkeypatch.setattr(fleet, "REPOS", [("repo", "o/r", "gen-matrix")])
    monkeypatch.setattr(fleet, "inspect", lambda *a, **k: {
        "app": "gen-matrix", "dir": "repo", "slug": "o/r", "error": "checkout missing"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--json"])
    fleet.main()
    row = json.loads(capsys.readouterr().out)[0]
    assert row["platform"] == "not-inspected", row
    # The floor IS answerable — it is keyed on the app slug alone — so it stays.
    assert row["submit_floor"] == "0.8.8", row
    seen["consulted, row never inspected"] = row["platform"]

    # ARM 4 (INVARIANT GUARD): the platform genuinely was not asked.
    _one_row(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    unconsulted = capsys.readouterr().out
    assert _platform_cells(unconsulted) == ["not-consulted"], unconsulted
    seen["--no-platform, never asked"] = _platform_cells(unconsulted)[0]

    # The ledger: four cases, four answers, no two of them the same string.
    assert seen == {
        "consulted, row inspected, version unreadable": "version-unread",
        "consulted, the CLI failed for every row": "unread",
        "consulted, row never inspected": "not-inspected",
        "--no-platform, never asked": "not-consulted",
    }, seen
    assert len(set(seen.values())) == 4, seen


def test_the_submit_floor_is_still_read_for_a_row_with_no_version(monkeypatch, tmp_path, capsys):
    """The floor is keyed on the app slug alone, so it is answerable even when
    the version is not — and reporting it as unavailable would be the same
    fabrication in the other direction. `custom-generators`' highest version on
    record is `0.6.5` (a withdrawn row, which still occupies the floor)."""
    _with_platform(monkeypatch, tmp_path)
    _fleet_rows(monkeypatch, {"app": "custom-generators",
                              "manifest_version": fleet.UNREADABLE,
                              "package_version": "0.6.5",
                              "error": "unreadable: manifest_version"})
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--json"])
    fleet.main()
    row = json.loads(capsys.readouterr().out)[0]
    assert row["submit_floor"] == "0.6.5", row
    assert row["platform"] == "version-unread", row


def test_an_absent_json_field_is_distinct_from_an_unreadable_one(monkeypatch, tmp_path):
    """`-` means the file parsed and the field is genuinely absent; `?` means it
    could not be read. Collapsing them would hide a missing buildCommand — the
    defect that broke a real platform build."""
    ok = {**_GIT_DIR, "show origin/main:block.manifest.json": '{"version": "1.0.0"}'}
    monkeypatch.setattr(fleet, "_RUN", _FakeGit(ok))
    assert fleet._json_field("/x", "origin/main", "block.manifest.json", "buildCommand") == "-"
    assert fleet._json_field("/x", "origin/main", "nope.json", "buildCommand") == fleet.UNREADABLE


# --- the inspect() -> main() seam, which every fixture above REPLACES ----------
#
# 🔴 EVERY `main()` TEST ABOVE INSTALLS A FAKE `inspect`, so all of them are
# scoped to ONE side of this seam: they pin what `main()` does with a row of the
# shape the fixture writes, never that `inspect()` writes that shape. Measured
# before these two guards existed: renaming `manifest_version` in `inspect()`
# left this file at 51 passed, while `fleet.py --no-fetch --no-platform` against
# the real workspace died with `KeyError: 'manifest_version'` at the printer on
# its first row — a tool dead on every invocation, with a fully green suite.
#
# Neither guard below uses `_one_row`, `_fleet_rows` or a fake `inspect`.

# The keys `inspect()` writes on a row it read in full. A LEDGER: it fails when
# the set grows, shrinks OR is renamed, which is the whole point — a rename is
# what nothing could see.
_INSPECT_KEYS = {
    "app", "dir", "slug", "fetch", "default_branch", "checked_out",
    "dirty_files", "manifest_version", "package_version", "build_command",
    "lockfile", "vitest_projects", "lockstep_guard",
}

# `error` is conditional: set when a column could not be read, absent otherwise.
_INSPECT_CONDITIONAL_KEYS = {"error"}

# Keys `main()` writes onto a row itself, so they are legitimately read without
# `inspect()` ever producing them.
_MAIN_AUTHORED_KEYS = {"platform", "submit_floor"}

# Every row key read anywhere in fleet.py OUTSIDE `inspect()`. Derived from the
# AST below; pinned here so the derivation itself cannot quietly return nothing.
_KEYS_CONSUMED = {
    "app", "error", "fetch", "default_branch", "checked_out", "dirty_files",
    "manifest_version", "package_version", "build_command", "lockfile",
    "vitest_projects", "lockstep_guard", "platform", "submit_floor",
}


def _row_keys_read_outside_inspect() -> set[str]:
    """Every string row-key any CONSUMER in fleet.py looks up.

    Walks the whole module and skips only the `inspect()` subtree — the
    producer — so a consumer added in a new helper (`_inspected` is exactly
    that) is picked up without anyone remembering to list it here. Covers
    `r["k"]` / `row["k"]`, `.get("k")`, `.setdefault("k", …)` and `"k" in row`.
    """
    import ast

    with open(os.path.join(SKILL_DIR, "fleet.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in list(ast.walk(tree)):
        if isinstance(node, ast.FunctionDef) and node.name == "inspect":
            producer = node
            break
    else:  # pragma: no cover - the positive control for the skip itself
        raise AssertionError("fleet.inspect() not found — this walk is scoped to nothing")
    skip = set(map(id, ast.walk(producer)))

    names = {"r", "row"}
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in names and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            found.add(node.slice.value)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "setdefault"}
                and isinstance(node.func.value, ast.Name) and node.func.value.id in names
                and node.args and isinstance(node.args[0], ast.Constant)):
            found.add(node.args[0].value)
        elif (isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                and any(isinstance(c, ast.Name) and c.id in names for c in node.comparators)):
            found.add(node.left.value)
    return found


_REAL_REPO = {
    # Every value distinct from every other, and none of them equal to a literal
    # this test's assertion names for a DIFFERENT cell — a fixture whose fields
    # collide cannot see a mutant that returns the wrong one.
    "rev-parse --git-dir": ".git",
    "symbolic-ref": "refs/remotes/origin/trunk",
    "branch --show-current": "zach/wip",
    "status --porcelain": " M a\n M b\n M c",
    # `:src` first: `_FakeGit` matches on prefix and the bare ref is a prefix of it.
    "ls-tree --name-only origin/trunk:src": "version-lockstep.test.ts",
    "ls-tree --name-only origin/trunk": "yarn.lock",
    # The two versions DISAGREE on purpose. Equal ones would be the fixture
    # collision the mutation rules warn about — a mutant that reads the manifest
    # for BOTH `manifest_version` and `package_version` produces byte-identical
    # output and survives. A real lockstep break is also a state this fleet
    # actually reaches, so the row stays realistic.
    "show origin/trunk:block.manifest.json":
        '{"version": "0.9.1", "buildCommand": "pnpm run build"}',
    "show origin/trunk:package.json": '{"version": "0.9.0"}',
    "show origin/trunk:vite.config.ts": "test: { environment: 'node' }",
}


def test_the_keys_inspect_produces_cover_the_keys_every_consumer_reads(monkeypatch, tmp_path):
    """INVARIANT GUARD, not regression coverage — the seam is correct today; it
    was simply pinned by nothing.

    Pins a RELATIONSHIP, not a component: the ledger of keys `inspect()` writes
    against the ledger of keys the rest of `fleet.py` reads. Fails when either
    set grows OR shrinks, and on a rename of any member of either.

    Limits, stated rather than implied: this checks key NAMES and which shape
    produces them. It cannot see a key whose VALUE is wrong, and it cannot see a
    consumer that reaches a row through a name other than `r` or `row`.
    `test_a_real_inspect_row_renders_through_main` is the behavioural half that
    covers the value axis.
    """
    monkeypatch.setattr(fleet, "WORKSPACE", str(tmp_path))
    (tmp_path / "repo").mkdir()

    monkeypatch.setattr(fleet, "_RUN", _FakeGit(_REAL_REPO))
    readable = fleet.inspect("repo", "o/r", "an-app", fetch=False)
    assert set(readable) == _INSPECT_KEYS, sorted(set(readable) ^ _INSPECT_KEYS)
    assert "error" not in readable, readable  # nothing failed, so nothing is flagged

    monkeypatch.setattr(fleet, "_RUN", _FakeGit(_GIT_DIR))  # every read after git-dir fails
    unreadable = fleet.inspect("repo", "o/r", "an-app", fetch=False)
    assert set(unreadable) == _INSPECT_KEYS | _INSPECT_CONDITIONAL_KEYS, sorted(unreadable)

    early = fleet.inspect("does-not-exist", "o/r", "an-app", fetch=False)
    assert set(early) == {"app", "dir", "slug", "error"}, sorted(early)

    consumed = _row_keys_read_outside_inspect()
    assert consumed == _KEYS_CONSUMED, sorted(consumed ^ _KEYS_CONSUMED)

    # THE SEAM ITSELF: everything a consumer reads is either produced by
    # `inspect()` or written by `main()`.
    orphans = consumed - _MAIN_AUTHORED_KEYS - _INSPECT_KEYS - _INSPECT_CONDITIONAL_KEYS
    assert not orphans, f"read by a consumer, produced by nothing: {sorted(orphans)}"


def test_a_real_inspect_row_renders_through_main(monkeypatch, tmp_path, capsys):
    """INVARIANT GUARD, not regression coverage — green before this round too.

    The behavioural half of the seam: `fleet.inspect` is NOT replaced, so the
    row `main()` prints is the row `inspect()` actually built, and every cell is
    pinned as a whole normalised line rather than by substring. A structural
    ledger type-checks past a wrong VALUE; this does not.
    """
    monkeypatch.setattr(fleet, "WORKSPACE", str(tmp_path))
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(fleet, "_RUN", _FakeGit(_REAL_REPO))
    monkeypatch.setattr(fleet, "REPOS", [("repo", "o/r", "an-app")])
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])

    assert fleet.main() == 0, "every column read and --no-fetch was asked for"
    out = capsys.readouterr().out
    body = [ln for ln in out.splitlines() if ln.startswith("an-app")]
    assert len(body) == 1, out
    assert body[0].split() == [
        "an-app", "trunk", "zach/wip", "3",
        "0.9.1/0.9.0!",                  # both versions read, and they disagree
        "pnpm", "run", "build",          # build_command, as the table splits it
        "yarn.lock", "1", "version-lockstep", "skipped", "not-consulted",
    ], body[0]


def test_a_long_branch_overflows_its_column_rather_than_being_truncated(monkeypatch, capsys):
    """INVARIANT GUARD, not regression coverage — this is the behaviour today
    and it is deliberate; what was wrong was the COMMENT describing it.

    The note beside `ver` framed it as THE exceptional unbounded cell ("Unlike
    `proj`, whose value set is closed and short, a version string has no
    bound"), which reads as exhaustive and is not: `branch` comes from
    `origin/HEAD`, is equally unbounded, and its `:<7` cell pads without
    truncating for exactly the same reason — truncating a read branch name would
    fabricate a different one. Measured here: `platform` starts at the same
    column in the header and in a `main` row, and `len("development") - 7`
    further right in a `development` row.

    Zero blast radius today (every default branch in REPOS is `main` or
    `trunk`), so nothing is widened. If someone ever DOES bound this cell, this
    test goes red and the comment beside `ver` must move with it.
    """
    _fleet_rows(
        monkeypatch,
        {"app": "short-branch", "fetch": "skipped", "default_branch": "main"},
        {"app": "long-branch", "fetch": "skipped", "default_branch": "development"},
    )
    monkeypatch.setattr(sys, "argv", ["fleet.py", "--no-fetch", "--no-platform"])
    fleet.main()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("-")]
    hdr, short, long_ = lines[0], lines[1], lines[2]

    # The value is READ IN FULL — the half that must never regress.
    assert "development" in long_, long_
    assert short.index("not-consulted") == hdr.index("platform"), (short, hdr)
    assert long_.index("not-consulted") == hdr.index("platform") + len("development") - 7, (
        long_, hdr)
