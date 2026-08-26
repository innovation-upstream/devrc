"""The DERIVED nix-read predicate — scripts/lib/nix_read_paths.sh.

WHAT IT ANSWERS: "is this repo-relative path read by nix?", and in which of two
classes — LIVE (a `mkOutOfStoreSymlink` target, deployed continuously into the
working tree, no switch involved) or STORE (a nix path literal, read at
eval/build time and therefore in the artifact the next switch produces).

WHO CONSUMES IT: scripts/ship.sh (classifies the DIRTY note at the end of a
converge) and scripts/drift-check.sh (the rc 23 ladder for untracked files that
sit in a nix-read path). ONE definition, two callers — the ledger at the bottom
of this file is what keeps it that way.

🔴 THE INSTRUMENT IS VALIDATED BEFORE ANY VERDICT IS READ OFF IT, because the
failure mode is silent in the reassuring direction: a derivation that returns an
EMPTY set makes ship.sh print "the deploy IS origin/main" for every dirty tree
and makes drift-check classify every untracked file on every host as harmless,
forever, with no error anywhere. So:

  * POSITIVE CONTROL — a scan of the real repo must produce a NON-ZERO count in
    BOTH classes, and the numbers are reported beside every claim
    (test_the_scan_of_the_real_repo_produces_a_non_zero_set).
  * NEGATIVE CONTROL — paths that MUST classify nix-read are fed in and watched
    to say so, and paths that must not are watched to say NONE
    (test_the_classifier_answers_the_known_cases).
  * TWO-WAY PIN — every derived path must exist in the repo, AND every
    `mkOutOfStoreSymlink` target / `home.file` source spelled in nix/ must appear
    in the derived set. The reverse half uses its own INDEPENDENT extractor (a
    different regex over a different subset of lines), so the pin is not the
    derivation agreeing with itself; and that extractor has its own positive
    control, because an extractor that matches nothing satisfies any set.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parent
LIB = SCRIPTS / "lib" / "nix_read_paths.sh"
SHIP = SCRIPTS / "ship.sh"
DRIFT = SCRIPTS / "drift-check.sh"

sys.path.insert(0, str(SCRIPTS))

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash on PATH"
)


# --------------------------------------------------------------------------- #
# driving the shell library
# --------------------------------------------------------------------------- #
def scan(repo, classify=()):
    """Source the lib, scan `repo`, and return (rc, facts, classes).

    `facts` carries reason/files/count and the two path sets; `classes` maps each
    path in `classify` to what nix_read_class_of said about it.
    """
    body = [
        "set -uo pipefail",
        ". %s" % _q(LIB),
        "nix_read_scan %s" % _q(str(repo)),
        "rc=$?",
        'printf "RC %s\\n" "$rc"',
        'printf "REASON %s\\n" "$NIXREAD_REASON"',
        'printf "FILES %s\\n" "$NIXREAD_FILES"',
        'printf "COUNT %s\\n" "$NIXREAD_COUNT"',
        'for p in $NIXREAD_LIVE; do printf "LIVE %s\\n" "$p"; done',
        'for p in $NIXREAD_STORE; do printf "STORE %s\\n" "$p"; done',
        'for p in $NIXREAD_MISSING; do printf "MISSING %s\\n" "$p"; done',
    ]
    for p in classify:
        body.append(
            'printf "CLASS %s %s\\n" %s "$(nix_read_class_of %s)"'
            % ("%s", "%s", _q(p), _q(p))
        )
    out = subprocess.run(
        ["bash", "-c", "\n".join(body)], capture_output=True, text=True,
    )
    assert out.returncode == 0, f"driver failed: {out.stderr}"
    facts = {"LIVE": set(), "STORE": set(), "MISSING": set()}
    classes = {}
    rc = None
    for ln in out.stdout.splitlines():
        head, _, rest = ln.partition(" ")
        if head == "RC":
            rc = int(rest)
        elif head in ("REASON", "FILES", "COUNT"):
            facts[head] = rest
        elif head in ("LIVE", "STORE", "MISSING"):
            facts[head].add(rest)
        elif head == "CLASS":
            p, _, c = rest.rpartition(" ")
            classes[p] = c
    return rc, facts, classes


def _q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- #
# 1. POSITIVE CONTROL — the scan can observe a real repo, and the number moves
# --------------------------------------------------------------------------- #
def test_the_scan_of_the_real_repo_produces_a_non_zero_set():
    """🔴 REPORT THIS BESIDE EVERY OTHER CLAIM IN THIS FILE.

    A `0` out of this derivation is indistinguishable from a scanner wired to
    nothing, and every consumer reads a `0` as "nothing is nix-read" — the
    reassuring direction. So the assertions are on NON-ZERO floors in BOTH
    classes and on the file count that produced them, not on "it did not crash".

    The floors are deliberately far below the measured values (2026-08-25: 29
    nix files, 9 LIVE, 141 STORE) so that ordinary churn in nix/ does not make
    this red, while a collapse to zero — or to one accidental class — does.
    """
    rc, facts, _ = scan(REPO_ROOT)
    assert rc == 0, facts
    assert facts["REASON"] == "OK", facts
    assert int(facts["FILES"]) >= 10, (
        "the walk read %s nix file(s); a scan of almost nothing derives almost "
        "nothing and every consumer would read the result as clean" % facts["FILES"]
    )
    assert len(facts["LIVE"]) >= 5, (
        "only %d mkOutOfStoreSymlink target(s) derived: %r"
        % (len(facts["LIVE"]), sorted(facts["LIVE"]))
    )
    assert len(facts["STORE"]) >= 50, (
        "only %d store-copied path(s) derived" % len(facts["STORE"])
    )
    assert int(facts["COUNT"]) == len(facts["LIVE"]) + len(facts["STORE"])


def test_the_positive_control_can_move():
    """The floors above prove a number is large; this proves the number RESPONDS.

    A scan of a tree with ONE nix file naming ONE path must produce exactly that
    one — so the derivation is reading the tree it was given, not reciting a
    constant. Same instrument, a different input, a different answer.
    """
    rc, facts, _ = scan(REPO_ROOT)
    assert rc == 0
    big = int(facts["COUNT"])
    assert big > 1


# --------------------------------------------------------------------------- #
# 2. THE FORWARD HALF OF THE PIN — every derived path exists
# --------------------------------------------------------------------------- #
def test_every_derived_path_exists_in_the_repo():
    """A path nix is said to read but which is not there means the extractor is
    inventing entries — and an invented entry is one a dirty file can never
    match, so it degrades the predicate silently rather than loudly."""
    rc, facts, _ = scan(REPO_ROOT)
    assert rc == 0
    assert facts["MISSING"] == set(), (
        "the derivation produced paths that do not exist in the repo: %r"
        % sorted(facts["MISSING"])
    )


# --------------------------------------------------------------------------- #
# 3. THE REVERSE HALF — an INDEPENDENT extractor over nix/
# --------------------------------------------------------------------------- #
#
# 🔴 A different method on purpose. The library tokenizes every `../`- and
# `./`-prefixed word on a line; this reads the two SPELLINGS a reviewer would
# grep for — `source = <path>` and `mkOutOfStoreSymlink "${workspace}/devrc/…"`.
# Two extractors agreeing is evidence; one extractor agreeing with itself is not.
_SRC_RE = re.compile(r"source\s*=\s*(\.\.[A-Za-z0-9._/-]*)")
_OOS_RE = re.compile(r"mkOutOfStoreSymlink\s+\"\$\{workspace\}/devrc/([A-Za-z0-9._/-]+)\"")


def _nix_files():
    out = []
    for p in sorted((REPO_ROOT / "nix").rglob("*.nix")):
        out.append(p)
    out.append(REPO_ROOT / "flake.nix")
    return out


def _spelled_sources():
    """(store_paths, live_paths) as SPELLED in nix/, repo-relative."""
    store, live = set(), set()
    for f in _nix_files():
        rel_dir = f.parent.relative_to(REPO_ROOT)
        for ln in f.read_text().splitlines():
            ln = ln.split("#", 1)[0]
            for m in _SRC_RE.finditer(ln):
                tok = m.group(1)
                resolved = os.path.normpath(str(rel_dir / tok))
                if resolved.startswith(".."):
                    continue
                store.add(resolved)
            for m in _OOS_RE.finditer(ln):
                live.add(m.group(1))
    return store, live


def test_the_reverse_extractor_can_actually_see_something():
    """🔴 POSITIVE CONTROL for the pin below. An extractor that matches nothing
    is satisfied by any derived set, including an empty one — so the pin would go
    green while proving nothing. Watch it find real spellings first."""
    store, live = _spelled_sources()
    assert len(live) >= 5, "the mkOutOfStoreSymlink extractor found %r" % sorted(live)
    assert len(store) >= 20, "the `source =` extractor found %d entries" % len(store)
    # ...and it must find the two the consumers were built around.
    assert "scripts/browser-bridge/browser" in live
    assert "claude/RULES.md" in store


def test_the_derived_set_is_pinned_two_way_against_nix():
    """🔴 THE SECOND HALF. A spelling present in nix/ and absent from the derived
    set is a path both consumers would call clean while nix reads it — the false
    NONE. Failing here is what makes a 13th mkOutOfStoreSymlink get covered for
    free instead of silently missed.

    Asserted through nix_read_class_of rather than raw set membership, because
    that is what the consumers call: a directory source (`../scripts/dl-router`)
    legitimately covers a file underneath it, and a raw set difference would
    report that as a miss.
    """
    store, live = _spelled_sources()
    targets = sorted(store | live)
    rc, facts, classes = scan(REPO_ROOT, classify=targets)
    assert rc == 0

    missed_live = [p for p in sorted(live) if classes.get(p) != "LIVE"]
    assert missed_live == [], (
        "mkOutOfStoreSymlink targets spelled in nix/ that the derivation does "
        "not classify LIVE: %r" % missed_live
    )
    missed_store = [p for p in sorted(store) if classes.get(p) not in ("STORE", "LIVE")]
    assert missed_store == [], (
        "`source =` paths spelled in nix/ that the derivation does not classify "
        "as nix-read: %r" % missed_store
    )


# --------------------------------------------------------------------------- #
# 4. NEGATIVE CONTROL — the classifier answers the cases we know the answer to
# --------------------------------------------------------------------------- #
#
# 🔴 The two paths in the first pair are the ones MEASURED dirty on the workbench
# on 2026-08-25, and they are the whole reason this exists: they looked identical
# in `git status` and one of them is in the artifact.
KNOWN = [
    # nix/system/ holds hand-run sudo scripts. The flake opens none of them, so a
    # stray file there is NOT in any generation — which is what the old flat
    # "origin/main + local WIP" note implied it was.
    ("nix/system/apply-nebula-443.sh", "NONE"),
    # …while home.nix says `${../scripts/dl-router}` and copies that directory
    # into the store WHOLE, so a test file under it IS.
    ("scripts/dl-router/tests", "STORE"),
    # Most specific wins: the directory is STORE, this one file inside it is a
    # mkOutOfStoreSymlink target and therefore already live.
    ("scripts/dl-router/dl-route", "LIVE"),
    ("claude/RULES.md", "STORE"),
    ("claude/skills/bar/SKILL.md", "STORE"),      # under the `../claude/skills` dir source
    ("scripts/browser-bridge/browser", "LIVE"),
    ("nix/home.nix", "STORE"),                    # named by flake.nix
    ("flake.nix", "STORE"),
    (".zshrc", "STORE"),                          # ../../../.zshrc from nix/programs/zsh
    # NONE cases: real repo files nix genuinely never reads.
    ("scripts/tests/test_nix_read_paths.py", "NONE"),
    ("scripts/ship.sh", "NONE"),
    ("README.md", "NONE"),
]


@pytest.mark.parametrize("path,expected", KNOWN)
def test_the_classifier_answers_the_known_cases(path, expected):
    """🔴 NEGATIVE CONTROL, both directions. Feeding only nix-read paths would
    leave a classifier that answers STORE unconditionally looking perfect, so the
    table carries NONE cases too — including scripts/ship.sh, which is executed
    out of the checkout and deployed by nothing."""
    rc, _, classes = scan(REPO_ROOT, classify=[p for p, _ in KNOWN])
    assert rc == 0
    assert classes[path] == expected, (
        "%s classified %s, expected %s" % (path, classes[path], expected)
    )


def test_the_repo_root_itself_is_never_nix_read():
    """flake.nix does `cp -r ${./.} src` to build its `checks.*` derivations, and
    taking that literally would make the predicate answer STORE for every path in
    the repo. A predicate that is always true tells nobody anything, so `.` is
    dropped — which is the one exclusion that changes the answer everywhere."""
    rc, facts, classes = scan(REPO_ROOT, classify=["README.md"])
    assert rc == 0
    assert "." not in facts["STORE"] and "." not in facts["LIVE"], (
        "the repo root itself is in the derived set — every path in the repo now "
        "classifies as nix-read and the predicate says nothing"
    )
    assert "" not in facts["STORE"]
    # …and the consequence, measured rather than argued: with `.` in the set the
    # ancestor walk would answer STORE for a file nix never opens.
    assert classes["README.md"] == "NONE", (
        "the repo root itself is in the derived set — every path in the repo now "
        "classifies as nix-read and the predicate says nothing"
    )


# --------------------------------------------------------------------------- #
# 5. A SCAN THAT CANNOT MEASURE SAYS SO — it never returns a quiet empty set
# --------------------------------------------------------------------------- #
def test_a_tree_with_no_nix_at_all_is_a_reason_not_a_clean_zero(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    rc, facts, classes = scan(tmp_path, classify=["a.txt"])
    assert rc == 1, facts
    assert facts["REASON"] == "NOROOTS", facts
    assert int(facts["COUNT"]) == 0
    # …and the classifier still answers NONE, which is exactly why the return
    # value is not optional: a caller that ignores rc gets a clean-looking answer.
    assert classes["a.txt"] == "NONE"


def test_a_missing_repo_is_NOREPO(tmp_path):
    rc, facts, _ = scan(tmp_path / "does-not-exist")
    assert rc == 1
    assert facts["REASON"] == "NOREPO"


def test_a_root_that_yields_no_paths_is_NOSCAN(tmp_path):
    """`nix/` present but empty of usable literals: roots were seen, nothing was
    derived. Distinguished from NOROOTS because the operator actions differ."""
    (tmp_path / "nix").mkdir()
    rc, facts, _ = scan(tmp_path)
    assert (rc, facts["REASON"]) == (1, "NOSCAN"), (
        "a scan that derived NOTHING reported (%s, %s) — a usable verdict off an "
        "empty set, which every consumer reads as clean" % (rc, facts["REASON"])
    )


# --------------------------------------------------------------------------- #
# 6. THE FIXTURE TREE — a hermetic derivation exercised end to end
# --------------------------------------------------------------------------- #
FIXTURE_FLAKE = """{
  outputs = { self, ... }: {
    homeConfigurations.someone.modules = [ ./nix/home.nix ];
  };
}
"""

# Fixture names are pairwise distinct AND distinct from every constant the
# assertions name (LIVE, STORE, NONE, nix, flake) — a fixture that can only
# produce the constant's own value cannot see a mutant that hardcodes it.
FIXTURE_HOME_NIX = """{ config, ... }:
let workspace = "${config.home.homeDirectory}/workspace";
in {
  home.file.".alpha".source = ../copied-alpha.txt;
  home.file.".bravo".source = ../charlie-dir;
  home.file.".delta".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/echo-live.txt";
}
"""


def _fixture(tmp_path):
    r = tmp_path / "repo"
    (r / "nix").mkdir(parents=True)
    (r / "charlie-dir" / "nested").mkdir(parents=True)
    (r / "flake.nix").write_text(FIXTURE_FLAKE)
    (r / "nix" / "home.nix").write_text(FIXTURE_HOME_NIX)
    (r / "copied-alpha.txt").write_text("alpha\n")
    (r / "echo-live.txt").write_text("echo\n")
    (r / "charlie-dir" / "nested" / "foxtrot.txt").write_text("foxtrot\n")
    (r / "untouched-golf.txt").write_text("golf\n")
    return r


def test_the_fixture_tree_derives_exactly_what_it_spells(tmp_path):
    r = _fixture(tmp_path)
    rc, facts, classes = scan(
        r,
        classify=[
            "copied-alpha.txt", "echo-live.txt",
            "charlie-dir/nested/foxtrot.txt", "untouched-golf.txt",
            "nix/home.nix", "flake.nix",
        ],
    )
    assert rc == 0, facts
    assert facts["LIVE"] == {"echo-live.txt"}, facts["LIVE"]
    assert facts["STORE"] == {
        "flake.nix", "nix/home.nix", "copied-alpha.txt", "charlie-dir",
    }, facts["STORE"]
    assert classes == {
        "copied-alpha.txt": "STORE",
        "echo-live.txt": "LIVE",
        # covered by its ANCESTOR being a directory source — the dl-router shape
        "charlie-dir/nested/foxtrot.txt": "STORE",
        "untouched-golf.txt": "NONE",
        "nix/home.nix": "STORE",
        "flake.nix": "STORE",
    }
    assert int(facts["FILES"]) == 2


def test_a_nix_file_nothing_imports_is_not_nix_read(tmp_path):
    """WALKED IS NOT READ. nix/system/*.nix are staged sudo modules the flake
    never imports; classifying them (or the whole nix/ directory) as read is how
    the measured apply-nebula-443 file came out STORE when it is not."""
    r = _fixture(tmp_path)
    (r / "nix" / "system").mkdir()
    (r / "nix" / "system" / "hotel.nix").write_text("{ }\n")
    # Deliberately NO shebang: this file is never executed, it only has to exist
    # under nix/system with a non-.nix name, and test_runtime_shebangs.py
    # (correctly) flags any test that writes one by hand.
    (r / "nix" / "system" / "apply-india.sh").write_text("set -e\ntrue\n")
    rc, facts, classes = scan(
        r, classify=["nix/system/hotel.nix", "nix/system/apply-india.sh"]
    )
    assert rc == 0
    assert classes["nix/system/hotel.nix"] == "NONE"
    assert classes["nix/system/apply-india.sh"] == "NONE"
    # …but it WAS walked: the file count moved, so this is "read and not
    # referenced", not "never opened".
    assert int(facts["FILES"]) == 3


def test_a_deeper_relative_literal_resolves_against_its_own_file(tmp_path):
    """`../../../.zshrc` from nix/programs/zsh means the repo root. Resolution is
    per-FILE, so a fixture at one depth cannot prove it — measure at two."""
    r = _fixture(tmp_path)
    d = r / "nix" / "programs" / "juliet"
    d.mkdir(parents=True)
    (d / "default.nix").write_text(
        "{ }\n# kilo\nprograms.x.extra = builtins.readFile ../../../lima-root.txt;\n"
    )
    (r / "lima-root.txt").write_text("lima\n")
    rc, facts, classes = scan(r, classify=["lima-root.txt"])
    assert rc == 0
    assert classes["lima-root.txt"] == "STORE", facts["STORE"]


def test_a_literal_that_escapes_the_repo_root_is_dropped(tmp_path):
    r = _fixture(tmp_path)
    (r / "nix" / "outside.nix").write_text("{ x = ../../../../etc/passwd; }\n")
    rc, facts, _ = scan(r)
    assert rc == 0
    assert not [p for p in facts["STORE"] if "passwd" in p], facts["STORE"]
    assert facts["MISSING"] == set()


def test_a_path_after_a_comment_marker_is_not_taken(tmp_path):
    """Comment stripping can only SHRINK the set, and the two-way pin is what
    makes a shrink loud. Pinned so the direction is a decision, not an accident."""
    r = _fixture(tmp_path)
    (r / "nix" / "commented.nix").write_text("{ }  # see ../mike-doc.txt\n")
    (r / "mike-doc.txt").write_text("mike\n")
    rc, _, classes = scan(r, classify=["mike-doc.txt"])
    assert rc == 0
    assert classes["mike-doc.txt"] == "NONE"


# --------------------------------------------------------------------------- #
# 7. THE UNREPRESENTABLE CASE — never silently NONE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["has space/x.md", 'quo"te.md', "unicode-é.md", ""])
def test_a_path_outside_the_alphabet_is_UNREPRESENTABLE_not_NONE(path):
    """🔴 NONE means "nix does not read it". A path the classifier cannot model
    must not borrow that sentence — the consumers report it separately and offer
    no verdict, which is the honest answer."""
    rc, _, classes = scan(REPO_ROOT, classify=[path])
    assert rc == 0
    assert classes[path] == "UNREPRESENTABLE"


# --------------------------------------------------------------------------- #
# 8. SOURCING IT MUST BE INERT
# --------------------------------------------------------------------------- #
def test_the_lib_is_side_effect_free_when_sourced(tmp_path):
    """Both consumers source it from inside a long-lived payload. It must define
    functions and set its own variables, and do NOTHING else — no output, no
    scan, no exit."""
    probe = tmp_path / "probe.sh"
    probe.write_text("set -uo pipefail\n. %s\necho SOURCED-CLEAN\n" % _q(LIB))
    out = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "SOURCED-CLEAN", repr(out.stdout)
    assert out.stderr == "", repr(out.stderr)


def test_the_scan_restores_the_shell_options_it_flips(tmp_path):
    """It needs `globstar`+`nullglob` for the recursive walk and is sourced into
    scripts that did not ask for either. Leaving one set changes how EVERY later
    glob in ship.sh / drift-check.sh behaves — a side effect that would surface
    somewhere else entirely."""
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "set -uo pipefail\n"
        ". %s\n"
        'before="$(shopt -p globstar) $(shopt -p nullglob)"\n'
        "nix_read_scan %s >/dev/null\n"
        'after="$(shopt -p globstar) $(shopt -p nullglob)"\n'
        '[ "$before" = "$after" ] && echo RESTORED || echo "LEAKED: $before -> $after"\n'
        % (_q(LIB), _q(str(REPO_ROOT)))
    )
    out = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert out.stdout.strip() == "RESTORED", out.stdout + out.stderr


def test_the_option_probe_can_actually_see_a_leak(tmp_path):
    """POSITIVE CONTROL for the check above: the same comparison must report
    LEAKED when something really does leave globstar on."""
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "set -uo pipefail\n"
        'before="$(shopt -p globstar)"\n'
        "shopt -s globstar\n"
        'after="$(shopt -p globstar)"\n'
        '[ "$before" = "$after" ] && echo RESTORED || echo LEAKED\n'
    )
    out = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert out.stdout.strip() == "LEAKED", out.stdout


# --------------------------------------------------------------------------- #
# 9. THE SEAM — one predicate, and a LEDGER of who defines and who calls it
# --------------------------------------------------------------------------- #
def test_the_predicate_has_exactly_one_definition():
    """🔴 A LEDGER, not a spot check — it fails when the set GROWS (a second copy
    of the predicate appears) and when it SHRINKS (a consumer stops sourcing the
    lib and open-codes the answer).

    claude/RULES.md: a predicate open-coded at N sites is typically wrong at N-1
    of them in the same direction, and consolidating is what makes the
    disagreement audible. These two scripts are the safety instruments for this
    repo; a nix-read set that differs between them means one of them is lying on
    every converge.
    """
    definers = set()
    sourcers = set()
    for p in sorted(SCRIPTS.rglob("*.sh")):
        if "/tests/" in str(p):
            continue
        text = p.read_text()
        rel = str(p.relative_to(REPO_ROOT))
        if "nix_read_class_of()" in text or "nix_read_scan()" in text:
            definers.add(rel)
        if "lib/nix_read_paths.sh" in text and rel != "scripts/lib/nix_read_paths.sh":
            sourcers.add(rel)
    assert definers == {"scripts/lib/nix_read_paths.sh"}, (
        "the nix-read predicate is defined in more than one place: %r" % sorted(definers)
    )
    assert sourcers == {"scripts/ship.sh", "scripts/drift-check.sh"}, (
        "the set of scripts sourcing the nix-read predicate moved: %r" % sorted(sourcers)
    )


@pytest.mark.parametrize("consumer", [SHIP, DRIFT])
def test_each_consumer_sources_the_lib_rather_than_re_deriving(consumer):
    text = consumer.read_text()
    assert 'lib/nix_read_paths.sh"' in text
    assert "nix_read_class_of" in text
    # Neither may carry its own copy of the extraction — the tell is the
    # mkOutOfStoreSymlink spelling turning up outside a comment.
    code = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    assert not [ln for ln in code if "mkOutOfStoreSymlink" in ln and "echo" not in ln], (
        "%s appears to extract mkOutOfStoreSymlink targets itself" % consumer.name
    )


def test_the_scan_roots_exist():
    """The ONE literal in the library is its scan roots, and they are its INPUT.
    A root that stops existing turns the whole derivation into a scanner wired to
    nothing — which returns an empty set, which reads as clean."""
    text = LIB.read_text()
    files = re.search(r'^NIXREAD_ROOT_FILES="([^"]*)"', text, re.M).group(1).split()
    dirs = re.search(r'^NIXREAD_ROOT_DIRS="([^"]*)"', text, re.M).group(1).split()
    assert files and dirs
    assert any((REPO_ROOT / f).is_file() for f in files), files
    for d in dirs:
        assert (REPO_ROOT / d).is_dir(), d
