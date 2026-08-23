"""Fail if prose from the client-confidential subsystem store has been copied
into this PUBLIC repo.

WHY THIS EXISTS, and why it is derived rather than enumerated
------------------------------------------------------------
PR #505 added the `OPEN:` / `RESOLVED <sha>:` marker and, to test the detector,
used the two real corpus bullets that motivated it as fixtures. Its author then
"verified" the absence of client content with a hand-written grep:

    git diff | grep -iE 'oauth2-proxy|CSRF|svc\\.cluster\\.local|github-pat|ankane'

That returned nothing and was reported as clean. It was not clean. The strings
that actually survived were a service name WITHOUT the prefix the pattern
required, and two fragments of ordinary English that no hand-written pattern
would have contained. The zero was a fact about the pattern, not about the diff —
`claude/RULES.md`: "a grep is an instrument: give it a positive control", and
"an empty match set means 'possibly the wrong pattern', not 'nothing there'".

A denylist of the leaked phrases would have the same defect twice over: it
restates what someone already thought of (so it cannot catch what it was written
for), and committing it would put the confidential phrases into this repo in the
name of keeping them out.

So the phrases are DERIVED FROM THE STORE at run time. Nothing confidential is
committed here; what is committed is the comparison.

HOW IT IS SPLIT, AND WHY IT NEVER SKIPS
---------------------------------------
The real store lives at `~/.claude/analyze-service-index` and cannot exist in the
nix sandbox. The obvious shape — one pytest that skips when the store is absent —
is the shape `run-tests.sh`'s `EXPECTED_SKIPS` ledger records itself REMOVING
TWICE, in its own words: "a check keyed to a path outside the repo is
structurally unobservable in the tier that gates merges, so it means one thing on
a dev host and nothing at all here." Both were re-pointed at synthetic fixtures
tracked in this repo so they never skip. This follows that precedent rather than
adding a third pin:

  * the PYTEST half below uses only synthetic fixtures and ALWAYS runs, in every
    tier. It proves the comparison machinery can see a copy and can tell one from
    unrelated prose — which is the part a merge gate can meaningfully hold.
  * the LIVE half is `python3 scripts/tests/test_store_content_not_copied.py`
    run directly, on the host that actually holds the store. It exits non-zero
    with the offending files named. That is the only machine where the copy can
    physically happen, so it is the only machine where the check means anything.

🔴 Run the live half before pushing anything that touches store tooling. The
pytest half passing is NOT evidence that no content was copied.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE = Path(os.environ.get("SUBSYSTEM_STORE_ROOT",
                            Path.home() / ".claude" / "analyze-service-index"))

# A phrase long enough that sharing it with the store is copying, not coincidence.
#
# 8 words. Shared-phrase counts between the store and this repo, counted as
# distinct (file, phrase) pairs using THIS module's own `_phrases` and
# `_tracked_text_files` — 825 tracked files against 35 store entries with the own
# scope excluded, measured 2026-08-15 at the PR tip:
#
#     N=4 → ~480 (VOLATILE)   N=5 → 49   N=6 → 3   N=7 → 1   N=8 → 0
#
# ⚠ THE N=4 ROW IS PINNED TO A CORPUS THAT GROWS and is given a tilde for that
# reason: two measurements the same day, on the same tree, read 477 and 491 —
# the store gained a bullet between them. At N=4 the count tracks ordinary
# English and moves with every write to the store, so an exact figure there
# would be stale within a day and would read as a discrepancy to whoever
# re-measured. N≥5 is stable and is what the choice rests on.
#
# 🔴 THE METHOD IS STATED BECAUSE TWO EARLIER VERSIONS OF THIS COMMENT WERE
# WRONG. The first justified 8 by claiming "at 8 every surviving phrase in a
# manual read was specific to one entry" — impossible, since nothing survives at
# 8. The second quoted 348/44/3/2/0, which an audit could not reproduce against
# any tree-and-filter combination; re-measured with the method above it is the
# row printed here. A distribution with no stated denominator or tokenizer is not
# a measurement, it is a memory.
#
# 8 is the FIRST value that yields zero — tuned to a noise floor, not to a
# measured precision point. ≤5 is unusable (ordinary technical English this repo
# writes on its own).
#
# ⚠ The residual N=6 and N=7 hits are in `claude/skills/analyze-service/SKILL.md`
# and `claude/skills/clawgate/reference/task-api.md` — files this work does not
# touch, so they are pre-existing and out of its scope. They are named rather
# than left as an anonymous "3" that a future reader would have to re-derive.
#
# ⚠ The corollary, which is the real caveat: a copy of SEVEN words or fewer is
# invisible here, and one such fixture did survive the first fix round at exactly
# 7. Lowering the threshold trades that for false positives; the honest position
# is that this catches wholesale copying, not fragments. If you change it, re-read
# a sample of what it then matches before believing the new number.
PHRASE_WORDS = 8

# Tokenisation. A `word` keeps dots/slashes/dashes so `foo.bar/baz` stays one
# token rather than becoming three that collide with unrelated prose.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_CODE_FENCE = re.compile(r"^\s*```")


# Extensions that cannot carry prose. Everything else is scanned, INCLUDING
# extensionless files.
#
# 🔴 THIS WAS AN ALLOWLIST AND THAT WAS THE BUG. A delta re-audit measured the
# allowlist version scanning 632 of 839 tracked files — 207 never compared,
# among them 62 `.mjs`, 48 extensionless scripts, 22 `.js`, 18 `.html`. The
# module's own documentation claimed it reported "any tracked repo file", so a
# copy into the browser extension or `scripts/bar-status-poll` was invisible
# while the check reported clean. An allowlist answers "did I think of this
# extension?"; a denylist answers "can this file hold prose?", which is the
# question. New file types are then covered by default rather than by memory.
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz", ".pdf", ".zip",
    ".gz", ".xz", ".zst", ".tar", ".woff", ".woff2", ".ttf", ".otf", ".mp4",
    ".webm", ".wasm", ".so", ".bin", ".pyc",
}


def is_scannable(path: Path) -> bool:
    """Can this file hold prose? PURE — no git, no repo layout.

    Separated from `_tracked_text_files` so the DECISION is testable in every
    tier. The first version tested the filter by calling `git ls-files` over the
    real repo, which fails in the nix sandbox (`/build/src` has no `.git`) — the
    same "keyed to something the gating tier does not have" class that
    `run-tests.sh`'s EXPECTED_SKIPS ledger records removing twice. Pinning the
    predicate against synthetic files is also a stronger guard: it asserts the
    rule rather than the repo's current contents.
    """
    # ⚠ BOTH HALVES OF THIS LINE ARE UNKILLED BY MUTATION, and that is stated
    # rather than left to be rediscovered: the PNG fixture carries a NUL so the
    # extension check is redundant for it, and `open()` on a directory raises
    # OSError so `is_file()` is redundant too. They are kept as cheap early exits
    # — a NUL sniff on every tracked file is the slower path — not as guards.
    if path.suffix.lower() in _BINARY_EXT or not path.is_file():
        return False
    try:
        # A NUL byte in the first 8 KiB is git's own heuristic for binary.
        # Cheaper and more honest than guessing from the name.
        return b"\0" not in path.open("rb").read(8192)
    except OSError:
        # ⚠ SILENTLY DROPPED, and so is a UTF-16 file (its NULs read as binary).
        # The module docstring says it compares "any tracked repo file"; these two
        # cases are the exception. Both are vanishingly rare in this repo and
        # neither can hold store prose in a form the tokenizer would match, but
        # the claim is narrowed here rather than left overbroad.
        return False


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if is_scannable(ROOT / line)]


def _phrases(text: str) -> set[str]:
    """Normalised n-grams of `PHRASE_WORDS` words, fences and code spans dropped.

    Fences are dropped because a shared YAML snippet is a shared FORMAT, not
    shared prose, and matching one would fire on any file that documents the same
    schema. Prose is where the confidential content is.
    """
    body: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            body.append(line)
    words = _WORD.findall(" ".join(body).lower())
    return {
        " ".join(words[i:i + PHRASE_WORDS])
        for i in range(max(0, len(words) - PHRASE_WORDS + 1))
    }


def _own_scope() -> str:
    """This repo's OWN scope name, derived the way the writer derives it.

    🔴 EXCLUDED FROM THE COMPARISON, and this is a provenance rule rather than an
    exclusion list. The `devrc/*` entries are notes ABOUT THIS REPO: their prose
    was written FROM the public files, so overlap runs repo → store and is
    expected. Measured when this guard was first run: 15 of its 17 hits were that
    direction (`CLAUDE.md`, `nix/home.nix`, `docs/LAYOUT.md` … against
    `devrc/skills.md` and `devrc/agent-ledger.md`). Comparing against them makes
    the guard fire on its own repo's documentation forever, which is how a gate
    becomes one everyone clicks through.

    Derived, never hardcoded: a literal `"devrc"` here would keep excluding the
    wrong scope the day the directory is renamed, and would silently start
    comparing nothing if it stopped matching.

    ⚠ What this gives up, stated rather than hidden: content that is genuinely
    confidential but filed under this repo's own scope is invisible to this
    check. That scope is notes about a public repo, so the exposure is small —
    but it is not zero, and it is the first thing to revisit if the scope's
    subject ever widens.
    """
    sys.path.insert(0, str(ROOT / "scripts" / "lib"))
    from subsystem_touch import scope_for_repo  # noqa: PLC0415

    return scope_for_repo(ROOT)


def _store_entry_files() -> list[Path]:
    if not STORE.is_dir():
        return []
    own = _own_scope()
    return [p for scope in sorted(STORE.iterdir())
            if scope.is_dir() and scope.name != own
            for p in sorted(scope.glob("*.md")) if p.name != "README.md"]


def live_check() -> list[str]:
    """Compare the REAL store against this repo. Returns the offending lines.

    Not a pytest test — see the module docstring. Raises `FileNotFoundError` when
    the store is absent, because a caller that asked for the live check and got a
    silent empty list would read "nothing was compared" as "nothing was copied".
    """
    entries = _store_entry_files()
    if not entries:
        raise FileNotFoundError(
            f"SUBSYSTEM STORE NOT PRESENT at {STORE} — nothing was compared, "
            f"which is NOT a clean bill of health. Run this on the host that "
            f"holds the store."
        )

    store_phrases: dict[str, str] = {}
    for p in entries:
        for phrase in _phrases(p.read_text(encoding="utf-8", errors="replace")):
            store_phrases.setdefault(phrase, f"{p.parent.name}/{p.name}")

    # 🔴 THE DENOMINATOR. A zero over an empty phrase set is the confident zero
    # this whole module exists to prevent, so the size of what was compared is
    # asserted before the comparison is believed.
    if len(store_phrases) <= 100:
        raise RuntimeError(
            f"only {len(store_phrases)} phrases extracted from {len(entries)} entry "
            f"file(s) — the extractor is not reading the store properly, so a clean "
            f"result below would mean nothing"
        )

    hits: list[str] = []
    for f in _tracked_text_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - ls-files just listed it
            continue
        for phrase in _phrases(text) & store_phrases.keys():
            # `relative_to` RAISES for a path outside ROOT, and a caller may
            # legitimately hand in one (the tests do). A crash there would turn a
            # real finding into a stack trace, so the display degrades instead.
            try:
                shown = f.relative_to(ROOT)
            except ValueError:
                shown = f
            hits.append(
                f"  {shown} shares an {PHRASE_WORDS}-word phrase "
                f"with store entry {store_phrases[phrase]}"
            )

    return sorted(set(hits))


def test_the_guard_can_actually_fire(tmp_path, monkeypatch):
    """POSITIVE CONTROL. A guard that has never been watched to go red is a claim
    about nothing — and this one's whole purpose is to report a zero.

    Runs on EVERY host, including CI where the real store is absent, because it
    builds its own store: the point is that the comparison machinery works, not
    that the real store is clean.
    """
    fake_store = tmp_path / "store" / "some-scope"
    fake_store.mkdir(parents=True)
    secret = ("the quarterly widget reconciler silently drops every third batch "
              "whenever the upstream ledger is paused")
    (fake_store / "svc.md").write_text(
        f"---\nservice: svc\n---\n\n## Nuance / work-history\n- 2026-01-01: {secret}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("test_store_content_not_copied.STORE", fake_store.parent)

    phrases = _phrases((fake_store / "svc.md").read_text())
    assert phrases, "the extractor produced nothing from a non-empty entry"

    # The copy: the same prose, as it would appear in a repo file.
    copied = _phrases(f"A test fixture that says: {secret}.")
    assert copied & phrases, (
        "the extractor cannot see identical prose as a shared phrase — the real "
        "check above would report a clean zero for a genuine copy"
    )

    # And the negative half: unrelated prose must NOT collide.
    unrelated = _phrases(
        "This module maps changed repository paths onto index entries using "
        "exact normalised component equality rather than substring matching."
    )
    assert not (unrelated & phrases), (
        "unrelated prose collided with the store's phrases — the threshold is too "
        "low and the guard would fire on innocent files"
    )


def test_the_live_check_refuses_to_report_a_clean_result_with_no_store(tmp_path,
                                                                      monkeypatch):
    """An absent store must RAISE, not return an empty list.

    This is the whole reason the live half is not a skipping pytest: "nothing was
    compared" and "nothing was copied" are the same empty value, and only one of
    them is good news.
    """
    monkeypatch.setattr("test_store_content_not_copied.STORE", tmp_path / "nope")
    try:
        live_check()
    except FileNotFoundError as exc:
        assert "NOT a clean bill of health" in str(exc)
    else:  # pragma: no cover - the assertion below is the failure report
        raise AssertionError("live_check returned instead of raising on an absent store")


def test_the_live_check_finds_a_planted_copy(tmp_path, monkeypatch):
    """END-TO-END over the real machinery, with a synthetic store AND a synthetic
    repo file — so it runs in every tier, including the nix sandbox."""
    scope = tmp_path / "store" / "some-scope"
    scope.mkdir(parents=True)
    secret = ("the quarterly widget reconciler silently drops every third batch "
              "whenever the upstream ledger is paused")
    # Padded past the >100-phrase denominator guard rather than weakening it: the
    # guard is what stops a clean result being reported over an empty comparison,
    # so a fixture that has to dodge it would be testing a different function.
    filler = "\n".join(
        f"- 2026-01-{d:02d}: filler bullet number {d} about an unrelated subject "
        f"that shares no wording with anything this repository contains anywhere."
        for d in range(2, 20)
    )
    (scope / "svc.md").write_text(
        f"---\nservice: svc\n---\n\n## Nuance / work-history\n"
        f"- 2026-01-01: {secret}\n{filler}\n",
        encoding="utf-8",
    )
    # Written under tmp_path, never inside ROOT. The first version planted the
    # file in `scripts/tests/` and removed it in a `finally`; an interrupted run
    # would strand an untracked file in a repo whose CLAUDE.md says new files get
    # `git add`ed — a test that can leave a mess in the tree it is protecting.
    planted = tmp_path / "planted.md"
    planted.write_text(f"A doc that quotes it: {secret}.\n", encoding="utf-8")
    monkeypatch.setattr("test_store_content_not_copied.STORE", scope.parent)
    monkeypatch.setattr("test_store_content_not_copied._own_scope", lambda: "")
    monkeypatch.setattr("test_store_content_not_copied._tracked_text_files",
                        lambda: [planted])
    hits = live_check()
    assert len(hits) == 1 and "some-scope/svc.md" in hits[0], hits


def test_the_denominator_guard_fires_on_a_too_small_store(tmp_path, monkeypatch):
    """An audit found this guard reachable and correct but never watched to fire —
    it survived deletion against a green suite. It is the thing standing between
    a clean result and a comparison over almost nothing."""
    scope = tmp_path / "store" / "sc"
    scope.mkdir(parents=True)
    (scope / "svc.md").write_text("---\nservice: svc\n---\n\n- tiny.\n", encoding="utf-8")
    monkeypatch.setattr("test_store_content_not_copied.STORE", scope.parent)
    monkeypatch.setattr("test_store_content_not_copied._own_scope", lambda: "")
    try:
        live_check()
    except RuntimeError as exc:
        assert "not reading the store properly" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a 1-bullet store did not trip the denominator guard")


def test_the_own_scope_exclusion_actually_excludes(tmp_path, monkeypatch):
    """The provenance rule the module spends 20 lines defending survived deletion
    against a green suite. Without it the guard fires forever on this repo's own
    documentation, which is how a gate becomes one everyone clicks through."""
    root = tmp_path / "store"
    (root / "mine").mkdir(parents=True)
    (root / "theirs").mkdir(parents=True)
    for d in ("mine", "theirs"):
        (root / d / "svc.md").write_text(
            "---\nservice: svc\n---\n\n## Nuance / work-history\n"
            + "\n".join(f"- 2026-01-{i:02d}: scope {d} distinct filler sentence "
                        f"number {i} with plenty of unique words in it here."
                        for i in range(1, 20)),
            encoding="utf-8",
        )
    monkeypatch.setattr("test_store_content_not_copied.STORE", root)
    monkeypatch.setattr("test_store_content_not_copied._own_scope", lambda: "mine")
    names = {p.parent.name for p in _store_entry_files()}
    assert names == {"theirs"}, f"own scope was not excluded: {names}"


def test_the_scan_covers_extensionless_and_js_files(tmp_path):
    """🔴 The allowlist version skipped 207 of 839 tracked files — .mjs, .js,
    .html and every extensionless script — while claiming to report "any tracked
    repo file". This pins the denylist behaviour that replaced it, against
    synthetic files so it runs in every tier.
    """
    for name in ("bar-status-poll", "ext.mjs", "app.js", "page.html", "init.lua",
                 "notes.md", "mod.py"):
        f = tmp_path / name
        f.write_text("some prose here\n", encoding="utf-8")
        assert is_scannable(f) is True, f"{name} would not be compared"

    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert is_scannable(binary) is False, "a real binary must be skipped"

    # ⚠ The NUL sniff is the half that catches an unlisted binary EXTENSION.
    # Without it a new binary type is scanned as text until someone remembers to
    # add it — which is the allowlist failure again, wearing a denylist.
    unlisted = tmp_path / "blob.unknownext"
    unlisted.write_bytes(b"text then a nul\x00 and more")
    assert is_scannable(unlisted) is False, (
        "the NUL sniff is not running — an unlisted binary extension is being "
        "compared as prose"
    )


if __name__ == "__main__":
    import sys as _sys

    try:
        offenders = live_check()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"store-content check DID NOT RUN: {exc}")
        raise SystemExit(2) from exc
    if offenders:
        print("client-confidential store prose has been copied into this PUBLIC repo:")
        for line in offenders:
            print(line)
        print(
            f"\n{len(offenders)} site(s). A push is irreversible. Reword with INVENTED "
            f"content that preserves only the SHAPE under test — never the wording. "
            f"Do NOT add the phrase to an exclusion list: the phrase itself is the "
            f"thing that must not be committed."
        )
        raise SystemExit(1)
    print("store-content check: no shared phrases. (Live comparison ran.)")
    _sys.exit(0)
