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
# 8 words, measured rather than picked: at 6 the corpus yields ordinary technical
# English ("the image tag in the deployment is") that this repo writes on its own
# and would fail on innocently; at 8 every surviving phrase in a manual read was
# specific to one entry. Raising it weakens the guard, lowering it makes it noisy —
# if you change it, re-read a sample of what it then matches before believing the
# new number.
PHRASE_WORDS = 8

# Tokenisation. A `word` keeps dots/slashes/dashes so `foo.bar/baz` stays one
# token rather than becoming three that collide with unrelated prose.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_CODE_FENCE = re.compile(r"^\s*```")


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    keep = {".py", ".md", ".sh", ".nix", ".json", ".toml", ".yaml", ".yml", ".txt"}
    return [ROOT / line for line in out.splitlines()
            if Path(line).suffix in keep and (ROOT / line).is_file()]


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
            hits.append(
                f"  {f.relative_to(ROOT)} shares an {PHRASE_WORDS}-word phrase "
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
    planted = ROOT / "scripts" / "tests" / "_planted_leak_probe.tmp.md"
    planted.write_text(f"A doc that quotes it: {secret}.\n", encoding="utf-8")
    try:
        monkeypatch.setattr("test_store_content_not_copied.STORE", scope.parent)
        monkeypatch.setattr("test_store_content_not_copied._own_scope", lambda: "")
        monkeypatch.setattr("test_store_content_not_copied._tracked_text_files",
                            lambda: [planted])
        hits = live_check()
    finally:
        planted.unlink(missing_ok=True)
    assert len(hits) == 1 and "some-scope/svc.md" in hits[0], hits


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
