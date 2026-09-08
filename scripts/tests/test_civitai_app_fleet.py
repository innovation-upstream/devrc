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
    """A fetch that failed leaves every column describing a STALE ref, while the
    module docstring claims the inventory cannot rot."""
    ok = {**_GIT_DIR, "branch --show-current": "main", "status --porcelain": ""}
    row, _ = _fleet_row(monkeypatch, tmp_path, ok)
    assert row["fetch"] == "FAILED", row
    assert "fetch failed" in row["error"], row


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


def test_vitest_projects_reports_a_class_not_an_invented_count(monkeypatch, tmp_path):
    """The body tests for a `projects:` key, which cannot tell two from three.
    Reporting `2` for a three-project repo would be a number asserted rather
    than counted."""
    ok = {**_GIT_DIR, "show origin/?:vite.config.ts": "projects: [a,b,c]"}
    assert fleet.vitest_projects.__doc__ and "2+" in fleet.vitest_projects.__doc__
    monkeypatch.setattr(fleet, "_RUN", _FakeGit(ok))
    assert fleet.vitest_projects("/x", "origin/?") in {"1", "2+", fleet.UNREADABLE}


def test_an_absent_json_field_is_distinct_from_an_unreadable_one(monkeypatch, tmp_path):
    """`-` means the file parsed and the field is genuinely absent; `?` means it
    could not be read. Collapsing them would hide a missing buildCommand — the
    defect that broke a real platform build."""
    ok = {**_GIT_DIR, "show origin/main:block.manifest.json": '{"version": "1.0.0"}'}
    monkeypatch.setattr(fleet, "_RUN", _FakeGit(ok))
    assert fleet._json_field("/x", "origin/main", "block.manifest.json", "buildCommand") == "-"
    assert fleet._json_field("/x", "origin/main", "nope.json", "buildCommand") == fleet.UNREADABLE
