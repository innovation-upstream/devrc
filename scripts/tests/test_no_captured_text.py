#!/usr/bin/env python3
"""REPO-WIDE structural guard: no CAPTURED FREE TEXT in a committed JSON/JSONL.

The third sibling of `test_no_public_ips.py` (IP literals) and
`test_no_client_hostnames.py` (client subdomains). Those two gate the values
`CLAUDE.md`'s PUBLIC-repo rule names; this one gates the class that rule did not
name until the clause this PR added — **message bodies, prompts and transcript
excerpts frozen into a fixture**.

The scan is `scripts/testlib/captured_text_scan.py`, whose docstring explains —
with the measured counts — why this is a STRUCTURAL rule (json × message-ish key
× free text) rather than an attempt to tell captured prose from authored prose by
reading it.

HOW TO SATISFY IT
-----------------
Ask what the fixture is FOR.

  * **A frozen capture backing an eval or a report that has already been
    written** — delete it. Its findings belong in `claudedocs/`, which is where
    they get read; the capture itself is a liability with no reader. That is what
    happened to `scripts/initiatives/dspy-eval/`, the exposure this gate was
    written for: a report-only harness whose verdict was fully recorded in
    `claudedocs/dspy-recap-eval-2026-07-23.md` and whose code no longer even ran.

  * **A fixture something still consumes** — regenerate it with SYNTHETIC
    content. A test that needs a message-shaped payload needs the SHAPE, never a
    real person's words.

Adding an ALLOWLIST entry is the rare third option, for a JSON file whose long
free text is genuinely authored — and note it is keyed on `(path, keypath)` plus
a COUNT, never on the value, because spelling the value here would commit the
disclosure this gate exists to prevent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import captured_text_scan as C  # noqa: E402
from testlib import public_ip_scan as P  # noqa: E402

# --- THE PINNED (PATH, KEYPATH) -> (COUNT, WHY) ALLOWLIST -----------------------
# 🔴 KEYED ON (PATH, KEYPATH) AND PINNED TO A COUNT — deliberately unlike both
# siblings, which key on `(relpath, value)`. The value cannot appear here: it is
# the disclosure. The COUNT is what stops the pin from being a rubber stamp, since
# without it an exemption at `messages[].text` would pre-approve every future
# message added at that key.
#
# Accounting runs in BOTH directions, the same discipline as the sibling gates:
#   * a hit matching no entry        -> FAIL (something new landed);
#   * an entry matching no hit       -> FAIL (the site went away; delete the pin);
#   * a hit whose count moved        -> FAIL (the fixture GREW under a live pin).
ALLOWLIST: dict[tuple[str, str], tuple[int, str]] = {}


def _hits():
    return C.scan_repo(REPO)


def _unpinned(hits):
    out = []
    for relpath, keypath, count, max_len in hits:
        pin = ALLOWLIST.get((relpath, keypath))
        if pin is None or pin[0] != count:
            out.append((relpath, keypath, count, max_len))
    return out


def _fmt(hits):
    return "\n  ".join(
        f"{p}: {k}  ({n} free-text values, longest {m} chars)"
        for p, k, n, m in hits)


# --- the scan ------------------------------------------------------------------

def test_no_captured_free_text_is_committed():
    unpinned = _unpinned(_hits())
    assert not unpinned, (
        "CAPTURED FREE TEXT is committed to a PUBLIC repo — message bodies, "
        "prompts or transcript excerpts frozen into a fixture. If the capture "
        "backs a report that is already written, DELETE it; if something still "
        "consumes the fixture, regenerate it with SYNTHETIC content (a test "
        "needs the SHAPE, not a real person's words). Counts and key paths "
        "only below — this gate never prints the text:\n  " + _fmt(unpinned))


def test_every_allowlist_entry_still_matches_something():
    """Stale-pin accounting — the complement of the scan above.

    INVARIANT GUARD while `ALLOWLIST` is empty: it asserts nothing about today's
    tree and is labelled as such, exactly like its sibling in
    `test_no_client_hostnames.py`. It exists so the first entry anyone adds is
    accounted for in both directions from the moment it is added.
    """
    seen = {(p, k) for p, k, _n, _m in _hits()}
    stale = [f"{p}: {k} ({why})" for (p, k), (_n, why) in ALLOWLIST.items()
             if (p, k) not in seen]
    assert not stale, (
        "ALLOWLIST entries match nothing — the fixture went away or the key "
        "moved. Delete or repoint the pin:\n  " + "\n  ".join(stale))


def test_the_tree_has_no_unscanned_json():
    """A tracked JSON this scan could not PARSE was not scanned at all.

    A silent skip is indistinguishable from a clean file, which is the failure
    mode `claude/RULES.md` calls a zero from a harness wired to nothing. Pinned
    at zero so a new unparseable fixture is visible rather than exempt.
    """
    bad = C.unparseable_files(REPO)
    assert bad == [], f"tracked JSON/JSONL that was NOT scanned: {bad}"


# --- negative controls: the gate must GO RED on the real shape ------------------
# 🔴 Built from a REALISTIC fixture, not a textbook one. RULES.md: a scanner
# validated against its own canonical example proves nothing, because scanners
# allowlist canonical examples. This reproduces the ACTUAL structure of the
# exposure that motivated the gate — `by_repo.<repo>[].recent_messages[].text`,
# an array of sentence-like strings — with invented content.

def _realistic_capture() -> dict:
    """The shape of an `initiative-scan --days 30 --json` dump, invented content.

    Sentences are fabricated but have the properties that matter: >60 chars,
    whitespace, and the same nesting (a DATA-keyed repo map -> a list of
    initiatives -> a message list -> a `text` leaf).
    """
    def msg(n: int) -> dict:
        return {"ts": f"2026-01-0{n}T00:00:00Z",
                "text": ("we should probably roll the staging deploy back before "
                         f"the sync window closes, attempt number {n} today")}
    return {
        "days": 30,
        "by_repo": {
            "/home/someone/workspace/some-client-repo": [
                {"slug": "a-slug", "momentum": "active",
                 "recent_messages": [msg(1), msg(2), msg(3)]},
            ],
        },
    }


def test_negative_control_the_scan_goes_RED_on_a_realistic_capture(tmp_path):
    f = tmp_path / "scripts" / "someeval" / "scan-days30.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(_realistic_capture()), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    relpath, keypath, count, max_len = hits[0]
    assert relpath == "scripts/someeval/scan-days30.json"
    assert keypath == "by_repo.*[].recent_messages[].text", keypath
    assert count == 3 and max_len > C.MIN_FREE_TEXT_CHARS


def test_negative_control_the_gates_ASSERTION_fails_not_just_the_scan(tmp_path):
    """The scan FINDING something and the gate REPORTING it are different things.

    An over-broad allowlist, or a `_unpinned` that swallowed everything, would
    leave the scan correct and the gate inert.
    """
    fabricated = [("scripts/x/dump.json", "messages[].body", 7, 210)]
    assert _unpinned(fabricated) == fabricated
    assert "7 free-text values" in _fmt(fabricated)


def test_negative_control_a_bare_string_list_under_a_message_key(tmp_path):
    """🔴 The shape `eval_set.json` used: `recent_messages: ["...", "..."]`.

    Bare strings DIRECTLY under the message key, with no `text` leaf. A scan that
    only inspects the key immediately above a leaf sees the enclosing key as
    `None` here and reports nothing — MEASURED as the reason list inheritance
    exists in `scan_obj`.
    """
    f = tmp_path / "eval_set.json"
    f.write_text(json.dumps({"train": [{"ctx": {"recent_messages": [
        "the migration finished but the follower is still lagging by six hours",
        "can you take a look at the queue depth when you get a moment tomorrow",
    ]}}]}), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [
        ("train[].ctx.recent_messages[]", 2)], hits


def test_negative_control_a_jsonl_transcript_is_scanned(tmp_path):
    """JSONL is the native format of every transcript export, and a `.json`-only
    scan would miss the likeliest future shape of this exposure entirely."""
    f = tmp_path / "sessions.jsonl"
    f.write_text("\n".join(json.dumps({"role": "user", "content": c}) for c in (
        "please go ahead and restart the ingest workers on the primary cluster",
        "that did not help, the lag is still climbing steadily since the deploy",
    )), encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert [(h[1], h[2]) for h in hits] == [("content", 2)], hits


def test_negative_control_a_GROWN_fixture_under_a_live_pin_still_fails():
    """The count half of the allowlist, driven rather than asserted about.

    Without it, one exemption at a message key pre-approves every message added
    there afterwards — the rubber stamp the sibling gates' M7 incident is about.
    """
    pin = ("docs/x.json", "messages[].text")
    hits = [("docs/x.json", "messages[].text", 5, 100)]
    saved = dict(ALLOWLIST)
    try:
        ALLOWLIST[pin] = (3, "control")
        assert _unpinned(hits) == hits, "a GROWN fixture passed under its pin"
        ALLOWLIST[pin] = (5, "control")
        assert _unpinned(hits) == [], "an exact-count pin did not exempt"
    finally:
        ALLOWLIST.clear()
        ALLOWLIST.update(saved)


# --- positive controls: the gate must stay GREEN on legitimate content ----------
# 🔴 A gate that fires on everything gets disabled, and `claude/RULES.md` is
# explicit that a permanently-red gate is worse than none. These pin the
# false-positive half that keeps this one green.

def test_positive_control_the_repo_scans_clean_today():
    """The zero this gate reports must be a zero from a scan that WALKED.

    Reported as a PAIR with the negative control above, never alone: that test
    proves the scan can return non-zero on the real shape, this one proves the
    real tree is clean.
    """
    files = C._candidates(REPO)
    assert len(files) >= 15, (
        f"only {len(files)} JSON/JSONL candidates under REPO={REPO} — the scan "
        "walked (almost) nothing and its zero means nothing")
    assert _unpinned(_hits()) == []


def test_positive_control_authored_metadata_is_not_a_finding(tmp_path):
    """`description` is EXCLUDED BY NAME, not allowlisted.

    The repo's three browser-extension `manifest.json` files carry a long
    authored `description`. Allowlisting them would be a scanner pre-approving
    its own canonical examples; excluding the key is the honest version, and the
    cost — captured text hidden under `description` — is named in the scan
    module's blind-spot list.
    """
    f = tmp_path / "manifest.json"
    f.write_text(json.dumps({"manifest_version": 3, "description":
        "Routes downloads into the right library folder based on the page they "
        "came from, rather than the filename the server happened to send."}),
        encoding="utf-8")
    assert C.scan_repo(tmp_path) == []
    assert "description" in C._EXCLUDED_KEYS


def test_positive_control_long_non_prose_values_are_not_findings(tmp_path):
    """A hash, a path, a token and a URL are long but are not free text.

    Whitespace is the discriminator; without it this gate would fire on every
    lockfile, every base64 blob and every absolute path in the repo.
    """
    f = tmp_path / "d.json"
    f.write_text(json.dumps({
        "text": "a" * 300,
        "body": "/nix/store/" + "b" * 120 + "-some-derivation-name/bin/thing",
        "content": "sha256-" + "c" * 80,
        "prompt": "https://example.test/" + "d" * 90,
    }), encoding="utf-8")
    assert C.scan_repo(tmp_path) == [], "a non-prose long value was a finding"


def test_positive_control_a_short_message_is_not_a_finding(tmp_path):
    """The threshold, pinned in both directions so it cannot silently drift."""
    short = "restart the workers"
    long = "restart the ingest workers on the primary cluster when you get a chance"
    assert not C.is_free_text(short) and len(short) < C.MIN_FREE_TEXT_CHARS
    assert C.is_free_text(long) and len(long) >= C.MIN_FREE_TEXT_CHARS
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"messages": [short]}), encoding="utf-8")
    assert C.scan_repo(tmp_path) == []


def test_positive_control_an_unknown_key_is_not_a_finding(tmp_path):
    """The enumeration is the rule. Pinned so the blind spot is a MEASURED,
    documented trade rather than an assumption — a producer calling its field
    `utterance` walks through, and the scan module says so in its own docstring.
    """
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"utterance":
        "this is a long sentence of captured text under a key nobody enumerated"}),
        encoding="utf-8")
    assert C.scan_repo(tmp_path) == []


# --- guarding the guard's inputs -----------------------------------------------

def test_the_scan_actually_reads_the_repo():
    """A wrong REPO root yields an empty file set and the zero above passes
    vacuously — the exact bug the IP gate shipped and caught."""
    files = C.repo_files(REPO)
    assert len(files) > 300, f"REPO={REPO} produced only {len(files)} files"
    assert any(f.name == "flake.nix" for f in files), "not the devrc root"


def test_the_skip_dir_test_is_relative_to_the_root(tmp_path):
    """🔴 The harness bug the IP gate shipped, inherited here rather than
    re-derived: every agent worktree lives under `…/worktrees/<id>/`, so an
    ABSOLUTE-parts skip test is true for every file and skips the whole repo
    while reporting a clean zero. This gate reaches that logic through the
    delegation below, so the pin belongs here too.
    """
    root = tmp_path / "worktrees" / "agent-x"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "d.json").write_text(json.dumps({"messages": [
        "the primary is still refusing writes after the failover completed"]}),
        encoding="utf-8")
    hits = C.scan_repo(root)
    assert len(hits) == 1, (
        f"the repo was skipped because its own PATH contains a skip-dir: {hits}")


def test_the_file_enumeration_is_the_ip_gates_not_a_third_copy():
    """🔴 ONE RULE, ONE PLACE — the seam, pinned rather than trusted.

    `captured_text_scan` imports `repo_files` from `public_ip_scan` instead of
    growing a third copy of the git-ls-files/fallback/skip-dir logic. Identity,
    not equality: a divergent copy that happened to behave the same today would
    satisfy an equality check and rot apart later.
    """
    assert C.repo_files is P.repo_files


def test_a_data_shaped_map_key_is_never_echoed(tmp_path):
    """🔴 THE REPORT ITSELF MUST NOT LEAK.

    `by_repo` was keyed by CLIENT REPO PATH. A gate that printed its key paths
    verbatim would put that path into every failure message and CI log — a
    smaller version of the disclosure it exists to stop. Data-shaped keys render
    as `*`; schema-shaped keys survive so the report stays useful.
    """
    secret = "/home/someone/workspace/a-client-repo"
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"by_repo": {secret: {"messages": [
        "the deploy went out but the health check never turned green again"]}}}),
        encoding="utf-8")
    hits = C.scan_repo(tmp_path)
    assert len(hits) == 1
    assert hits[0][1] == "by_repo.*.messages[]", hits[0][1]
    assert secret not in _fmt(hits), "the client path leaked into the report"


def test_the_scan_never_returns_a_captured_VALUE(tmp_path):
    """The structural reason the failure message is safe to print.

    Every element of a hit is a path, a key path or an int — pinned by TYPE, so
    a future field carrying an excerpt "for context" fails this test.
    """
    f = tmp_path / "d.json"
    sentence = "the queue depth alarm has been flapping every twenty minutes since noon"
    f.write_text(json.dumps({"messages": [sentence]}), encoding="utf-8")
    hits = C.scan_repo(f.parent)
    assert len(hits) == 1
    relpath, keypath, count, max_len = hits[0]
    assert isinstance(relpath, str) and isinstance(keypath, str)
    assert isinstance(count, int) and isinstance(max_len, int)
    assert sentence not in str(hits), "the scan returned the captured text"


def test_the_key_enumeration_and_the_exclusions_are_disjoint_and_explained():
    """Both lists are DATA, and every entry must carry its reason in the source.

    `claude/RULES.md`: an allowlist is an enumeration with each entry's reason,
    not a pattern — and not env-overridable. This pins that neither list can grow
    a bare entry, and that a key cannot be in both.
    """
    assert not (set(C.MESSAGE_KEYS) & set(C._EXCLUDED_KEYS)), "a key is in both"
    for name, table in (("MESSAGE_KEYS", C.MESSAGE_KEYS),
                        ("_EXCLUDED_KEYS", C._EXCLUDED_KEYS)):
        for key, why in table.items():
            assert why and len(why) > 15, f"{name}[{key!r}] has no reason"


def test_the_threshold_is_not_env_overridable(monkeypatch):
    """The constant is a reviewed edit, not a runtime knob — the same rule the
    per-target test floors follow. An env-tunable threshold is a gate anyone can
    turn off for one run."""
    import importlib
    monkeypatch.setenv("MIN_FREE_TEXT_CHARS", "100000")
    importlib.reload(C)
    assert C.MIN_FREE_TEXT_CHARS == 60
