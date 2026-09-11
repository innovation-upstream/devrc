"""Unit tests for scripts/i3status-scratchpads — the scratchpad colour legend.

Drives the pure `render` formatter and the two fetchers (`load_slots`,
`fetch_sessions`) against FIXTURE data — no real tmux, no real slot table
except where a test is deliberately pinning the real one.

🔴 THE TESTS THAT MATTER MOST ARE THE DISCRIMINANT ONES. This block has a real
reading that looks like nothing — "no scratchpad sessions running" renders the
full legend with every slot dim — so a reading that COULD NOT BE TAKEN must be
visibly different (`scratch ?`) or the pill lies quietly. Four ways to fail to
measure are pinned below: an unloadable slot table, a missing `tmux`
executable, and a non-zero `tmux` exit that is NOT `no server running`; the
fourth, `no server running` itself, is the one that must NOT render `?`.
"""
import html
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")
_REPO = Path(_HERE).resolve().parents[1]
_TABLE = os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")
_GRAPHICAL = _REPO / "nix" / "graphical.nix"
_TMUX_NIX = _REPO / "nix" / "programs" / "tmux" / "default.nix"
_PICKER = os.path.join(_SCRIPTS, "tmux-scratch-picker.sh")


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load("i3status-scratchpads", "i3status_scratchpads")

# Fixture slots. 🔴 PAIRWISE DISTINCT ON EVERY FIELD, and distinct from every
# constant the assertions name: no two window counts are equal, none of them is
# 0 or 1 (values a mutant could hardcode and still look right), and no fixture
# colour is `blk.DIM`. A fixture that can only ever produce a constant's own
# value cannot see a mutant that hardcodes that constant.
_SLOTS = [
    ("scratchA", "a", "#111111", "Alpha"),
    ("scratchB", "b", "#222222", "Bravo"),
    ("scratchC", "c", "#333333", "Charlie"),
]
_SESSIONS = {"scratchA": 7, "scratchC": 4}      # B absent; 7 != 4, neither is 1


# --------------------------------------------------------------------------- #
# The real slot table
# --------------------------------------------------------------------------- #
def _bash_slot_entries():
    """The real table as BASH sees it — an INDEPENDENT parser.

    🔴 This exists so the expected slot COUNT is not derived from the regex
    under test. `len(slots) == 20` compared the parser's output to a literal
    that was itself read off that parser's output, so a 21st slot the regex
    silently dropped left the assertion green. bash `source` is the third
    consumer of this file and shares no code with the regex, which is exactly
    what makes it usable as the yardstick."""
    out = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s\\n" "${SCRATCH_SLOTS[@]}"',
         "_", _TABLE],
        capture_output=True, text=True, check=True)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_the_REAL_slot_table_parses_to_twenty_slots_with_the_expected_entry():
    """Entries pinned as LITERALS; the COUNT derived from bash, not from us.

    A parser that dropped a field would otherwise agree with itself."""
    slots = blk.load_slots([_TABLE])
    assert slots is not None
    expected = len(_bash_slot_entries())
    # positive control: the independent parser actually read something
    assert expected >= 20, expected
    assert len(slots) == expected, (
        "the regex and bash disagree about how many slots this file has: "
        "regex=%d bash=%d — %r" % (len(slots), expected, [s[0] for s in slots]))
    assert ("scratch4", "V", "#83a598", "Vapor") in slots
    assert ("scratch", "g", "#b8bb26", "grove") in slots
    assert ("scratch20", "Y", "#79740e", "Yarrow") in slots
    # CASE IS SIGNIFICANT in this table: M-v and M-V are different sessions.
    assert ("scratch3", "v", "#b16286", "violet") in slots
    # ORDER is the table's order — the legend renders in it.
    assert slots[0][0] == "scratch" and slots[-1][0] == "scratch20"


def test_the_sibling_deployed_leg_is_tried_FIRST():
    """🔴 On a live host this script is a lone nix-store symlink in
    ~/.config/i3status-rust/scripts and the slot table is symlinked BESIDE it as
    `scratch-slots.sh`. That leg must win, or a stale $DEVRC_DIR checkout
    silently overrides the table home-manager just deployed."""
    paths = blk._SLOT_PATHS
    assert len(paths) == 3, paths
    here = os.path.dirname(
        os.path.abspath(os.path.join(_SCRIPTS, "i3status-scratchpads")))
    assert paths[0] == os.path.join(here, "scratch-slots.sh")
    # leg 2 resolves inside ANY checkout without $DEVRC_DIR being right
    assert paths[1] == os.path.join(here, "tmux-scratch-slots.sh")
    assert os.path.exists(paths[1]), paths[1]


def test_load_slots_falls_through_a_missing_path_to_a_real_one(tmp_path):
    """The expected count comes from bash, never from a literal — the same
    reason `_bash_slot_entries` exists. This line read `== 20` until round 2,
    which is precisely the self-agreeing assertion the docstring above warns
    about: a 21st slot the regex silently dropped would have left it green."""
    missing = str(tmp_path / "nope" / "scratch-slots.sh")
    slots = blk.load_slots(
        [missing, os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")])
    assert slots is not None and len(slots) == len(_bash_slot_entries())


def test_load_slots_returns_NONE_not_empty_when_nothing_parses(tmp_path):
    """None, NOT []. [] renders as an empty legend — a reading nobody can take,
    because the table always has slots in it."""
    junk = tmp_path / "scratch-slots.sh"
    junk.write_text("# no slot entries here at all\n", encoding="utf-8")
    assert blk.load_slots([str(junk)]) is None
    assert blk.load_slots([str(tmp_path / "absent.sh")]) is None


# --------------------------------------------------------------------------- #
# ONE GRAMMAR, THREE READERS  (the slot-entry regex)
# --------------------------------------------------------------------------- #
#: Lines that separate the grammars that USED to be in this repo. Each carries
#: the reason it is here; every one is classified by nix, by Python and (where
#: applicable) by bash in the tests below.
_GRAMMAR_CORPUS = [
    # (line, is_a_slot, why)
    ('    "scratch4:V:#83a598:Vapor"', True, "the real shape"),
    ('#    "scratch21:z:#123456:zed"', False,
     "COMMENTED OUT: nix skipped it (whole-line match), the legend's unanchored "
     "regex matched it and advertised a hotkey bound to nothing"),
    ('    "scratch21:z:#123456789:zed"', True,
     "9 hex digits IS valid pango; nix's `+` took it, the legend's {3,8} dropped it"),
    ('    "scratch21:z:#123456789abc:zed"', True, "12 hex digits, valid pango"),
    ('    "scratch21:z:#abc:zed"', True, "3 hex digits, valid pango"),
    ('    "scratch21:z:#12345:zed"', False, "5 hex digits: no pango parser accepts it"),
    ('    "scratch21:z:#1234567:zed"', False, "7 hex digits: same"),
    ('    "scratch21:z:#12345678:zed"', False,
     "8 hex digits: the legend's old {3,8} upper bound, not a pango colour"),
    ('"scratch21:z:#123456:zed"', True, "no indent"),
    ('  "scratch21:z:#123456:zed"  ', True, "trailing spaces"),
    ('\t"scratch21:z:#123456:zed"', True,
     "🔴 TAB-INDENTED. The grammar was `[ ]*` — space only — until round 2, so "
     "this was a slot to NOBODY while bash still put it in SCRATCH_SLOTS. "
     "MEASURED before the fix, one real entry re-indented with a tab: bash 20, "
     "the legend 19 with no `?`, nix 19 bindings with no throw"),
    ('\t "scratch21:z:#123456:zed" \t', True, "mixed leading/trailing tab+space"),
    ('    "scratch21:z:#123456:zed" # trailing comment', False,
     "🔴 NOT the whole line — and the reason given here used to read `bash "
     "would not put it in the array either`, which is FALSE. MEASURED: a "
     "3-entry fixture whose middle line carried a trailing comment yields "
     "${#SCRATCH_SLOTS[@]} = 3, with the element being the quoted string. So "
     "this shape IS a real three-way disagreement. It is deliberately NOT a "
     "slot to the grammar — but it IS a candidate (see _CANDIDATE_CORPUS), so "
     "the shortfall floor fires and the table is refused LOUDLY rather than "
     "half-read"),
    ('SCRATCH_SLOTS=(', False, "the array opener"),
]

#: 🔴 THE CANDIDATE PATTERNS ARE THE FLOOR'S YARDSTICK, AND THEY MUST BE WIDER
#: THAN THE GRAMMAR. A yardstick that narrows in lockstep with the grammar
#: cannot see a dropped entry — both counts fall together and the floor stays
#: quiet, which is exactly how the TAB case above shipped silently. Expected
#: candidate verdicts, classified by BOTH readers below.
_CANDIDATE_CORPUS = [
    # (line, is_a_candidate, why)
    ('    "scratch4:V:#83a598:Vapor"', True, "a real entry"),
    ('\t"scratch21:z:#123456:zed"', True, "tab-indented: leading whitespace"),
    ('    "scratch21:z:#12345:zed"', True,
     "a CORRUPT colour is still a declared entry — that is the whole point"),
    ('    "scratch21:z:#123456:zed" # trailing comment', True,
     "🔴 WIDER THAN THE GRAMMAR ON PURPOSE: bash accepts this line, the "
     "grammar does not, so counting it is what turns a silent disagreement "
     "into a refused table"),
    ('#    "scratch21:z:#123456:zed"', False,
     "commented out — not declared to anybody"),
    ('SCRATCH_SLOTS=(', False, "the array opener"),
    ('# SLOT_ENTRY_RE: [ ]*"x"', False, "the marker line is not an entry"),
]


def _marker_lines():
    text = open(_TABLE, encoding="utf-8").read()
    return re.findall(r"^# SLOT_ENTRY_RE: (\S.*?)[ \t]*$", text, re.M)


# --------------------------------------------------------------------------- #
# The nix side, EVALUATED. Everything below asks nix for a VALUE — never for a
# spelling. The round-1 guard on this property was a regex over default.nix's
# source text (`^\s*slotRe\s*=\s*$` OR NOT `^\s*slotRe\s*=\s*"`), whose first
# disjunct matched the then-current formatting, so the second never ran: two
# mutants that put a string literal back — one of them carrying the original
# DISAGREEING grammar, i.e. a full regression of the bug this PR exists to fix —
# SURVIVED the whole 47-test suite. A spelled guard cannot see a value.
# --------------------------------------------------------------------------- #
_SLOT_TABLE_NIX = _REPO / "nix" / "programs" / "tmux" / "slot-table.nix"
#: A `pkgs` with exactly the attributes nix/programs/tmux/default.nix touches.
#: Plain strings: `mkBind` and the plugin `extraConfig`s only interpolate them.
_PKGS_STUB = ('{ tmuxPlugins = { continuum = "/STUB/continuum"; '
              'resurrect = "/STUB/resurrect"; tmux-fzf = "/STUB/tmux-fzf"; }; }')

_NIX_MISSING = pytest.mark.skipif(
    shutil.which("nix-instantiate") is None,
    reason="nix-instantiate absent — `pkgs.nix` is in the flake's gateTools, "
           "so this skipping means the environment is not a gate environment")


def _nix_eval(expr):
    """Evaluate a pure-`builtins` nix expression to a Python value.

    No `<nixpkgs>`, no channel, no network. Raises with nix's own stderr so a
    broken expression reads as a broken expression, not as a wrong verdict."""
    proc = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "--expr", expr],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout)


def _nix_try(expr):
    """`builtins.tryEval expr` → (success, value). For the fail-closed cases."""
    r = _nix_eval("builtins.tryEval (%s)" % expr)
    return r["success"], r["value"]


def _slot_table_expr(path, attr):
    return 'let t = import %s { path = %s; }; in t.%s' % (
        json.dumps(str(_SLOT_TABLE_NIX)), json.dumps(str(path)), attr)


def _tmux_module_extra_config(table_path=None):
    """`.extraConfig` of the REAL tmux module — the text home-manager ships."""
    args = "{ pkgs = %s;%s }" % (
        _PKGS_STUB,
        "" if table_path is None else " slotTablePath = %s;" % json.dumps(
            str(table_path)))
    return '(import %s %s).extraConfig' % (json.dumps(str(_TMUX_NIX)), args)


_GENERATED_MARKER = "# --- generated scratchpad popup toggles"


def _generated_bindings(extra_config):
    """The generated `bind -n M-<key>` block, parsed back into slot tuples.

    Only the lines AFTER the generator's own banner: `.tmux.conf` carries
    hand-written `bind -n M-…` lines of its own and they are not slots."""
    i = extra_config.index(_GENERATED_MARKER)
    out = []
    for ln in extra_config[i:].splitlines():
        m = re.match(
            r"bind -n M-(?P<key>\S+) if-shell -F '#\{==:#\{session_name\},"
            r"(?P<sess>[^}]+)\}'.*-S 'fg=(?P<color>[^']+)' -T ' (?P<name>.*) ' ",
            ln)
        if m:
            out.append((m.group("sess"), m.group("key"), m.group("color"),
                        m.group("name")))
    return out


@_NIX_MISSING
def test_the_grammar_NIX_USES_is_the_MARKER_STRING_ITSELF_evaluated(tmp_path):
    """🔴 THE FLAGSHIP PROPERTY, ASSERTED AS A VALUE.

    Ask nix for the grammar it actually binds — `slotRe`, evaluated out of the
    reader nix/programs/tmux/default.nix imports — and compare it to the string
    Python compiled from the same marker line. Not "the source text looks like
    it reads the marker": the string itself.

    A mutant that binds `slotRe` to a literal fails here whatever the literal
    says, because the comparison is against the FILE's marker. A mutant that
    binds it to a literal EQUAL to today's marker also fails the sibling
    `…CANNOT_CLASSIFY_A_LINE_ITSELF` below, which is what covers the case a
    value comparison structurally cannot see."""
    marker = _marker_lines()
    assert len(marker) == 1, marker
    nix_re = _nix_eval(_slot_table_expr(_TABLE, "slotRe"))
    assert nix_re == marker[0], (
        "nix compiles a slot grammar that is NOT the table's marker line.\n"
        "  nix:    %r\n  marker: %r" % (nix_re, marker[0]))

    py_rx = blk.slot_pattern(open(_TABLE, encoding="utf-8").read())
    assert py_rx is not None
    assert py_rx.pattern == "^" + nix_re + "$", (
        "the legend compiled a different grammar from the one nix uses.\n"
        "  python: %r\n  nix:    %r" % (py_rx.pattern, nix_re))
    # …and the grammar is not empty or trivially-everything.
    assert len(nix_re) > 20 and nix_re != ".*", nix_re

    # 🔴 THE DECISIVE HALF: nix must TRACK the marker, not happen to equal it
    # today. A literal that matches the current marker byte for byte passes
    # every comparison above — so change the marker in a synthetic table and
    # require the value to follow. A hardcoded grammar cannot.
    moved = marker[0].replace("([^\"]+)\"", "([^\"]+)!\"")
    assert moved != marker[0]
    table2 = tmp_path / "scratch-slots.sh"
    table2.write_text(
        open(_TABLE, encoding="utf-8").read().replace(marker[0], moved),
        encoding="utf-8")
    assert _nix_eval(_slot_table_expr(table2, "slotRe")) == moved, (
        "nix's slot grammar did NOT follow the table's marker line — it is "
        "bound to something other than what the file says")
    assert blk.slot_pattern(table2.read_text()).pattern == "^" + moved + "$"


def test_default_nix_CANNOT_CLASSIFY_A_LINE_ITSELF():
    """🔴 THE SINGLE-COPY HALF, and the only one that cannot be a value check.

    A second grammar that AGREES with the first today is invisible to every
    behavioural test — it only bites when someone edits one copy. So assert the
    property that makes a second copy impossible: default.nix holds no regex
    PRIMITIVE. `builtins.match` and `builtins.split` are the only two ways nix
    can classify a string against a pattern, so a file containing neither
    cannot carry an entry grammar; the verdicts have to come from the reader it
    imports.

    ⚠ WHAT THIS DOES NOT COVER, stated rather than implied: nix could in
    principle classify lines with `substring`/`stringLength` comparisons by
    hand. Nobody has, it would be far more obvious in review than a regex, and
    the behavioural tests below would catch it the moment it disagreed.

    Comments are stripped first — both files discuss the primitives in prose,
    and a guard a comment can trip is a guard a comment can also satisfy."""
    src = _TMUX_NIX.read_text()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for prim in ("builtins.match", "builtins.split"):
        assert prim not in code, (
            "nix/programs/tmux/default.nix uses %s — it has grown a way to "
            "classify slot table lines itself. The grammar's one home is the "
            "`# SLOT_ENTRY_RE:` line in scripts/tmux-scratch-slots.sh, read by "
            "nix/programs/tmux/slot-table.nix." % prim)
    # Negative control: the stripper did not simply delete everything, and the
    # primitives ARE findable where they legitimately live.
    assert "scratchBindings" in code and "mkBind" in code, code[:400]
    assert "builtins.match" in _SLOT_TABLE_NIX.read_text()
    # …and default.nix imports the one reader, exactly once.
    assert code.count("import ./slot-table.nix") == 1, code

    blk_src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
                   encoding="utf-8").read()
    assert not hasattr(blk, "_SLOT_RE"), (
        "scripts/i3status-scratchpads defines a module-level entry regex again; "
        "the grammar's one home is the `# SLOT_ENTRY_RE:` line in "
        "scripts/tmux-scratch-slots.sh, read by `slot_pattern`")
    for name in ("_MARKER_RE", "_CANDIDATE_RE"):
        pat = getattr(blk, name).pattern
        assert "0-9a-fA-F" not in pat, (
            "%s has grown a colour grammar: %r" % (name, pat))
    assert "SLOT_ENTRY_RE" in blk_src, blk_src[:200]
    assert len(_marker_lines()) == 1, _marker_lines()


@_NIX_MISSING
def test_the_BINDINGS_NIX_GENERATES_are_exactly_the_LEGENDS_OWN_PARSE():
    """🔴 THE RELATIONSHIP, END TO END, THROUGH BOTH REAL ARTIFACTS.

    Evaluate the tmux module home-manager actually ships, read the
    `bind -n M-<key>` block it GENERATED, and compare session/key/colour/name
    against what the bar legend parses out of the same table. This is what
    `slotRe` being shared is FOR, and it also pins the group-INDEX contract the
    marker cannot carry: nix reads `elemAt m 4` for the name, Python reads
    `group(5)`, and a swap shows up here as mismatched names/colours."""
    cfg = _nix_eval(_tmux_module_extra_config())
    generated = _generated_bindings(cfg)
    slots = blk.load_slots([_TABLE])
    assert slots is not None
    # positive control: the parse really read something out of the nix output
    assert len(generated) >= 20, (len(generated), cfg[:200])
    assert generated == slots, (
        "the tmux key bindings and the bar legend disagree about the real "
        "table.\n  nix-only:    %r\n  legend-only: %r"
        % (sorted(set(generated) - set(slots)),
           sorted(set(slots) - set(generated))))
    # …and the colours reached the binding as the popup border, not as a name.
    assert all(c.startswith("#") for _s, _k, c, _n in generated), generated


def test_the_colour_grammar_is_the_ONE_PANGO_ACCEPTS():
    """3 / 6 / 9 / 12 hex digits in, everything else out.

    MEASURED (i3status-rs 0.36.1): the bar passes `foreground="#fffffff"`
    through verbatim and neither validates nor escapes it, so a 5- or 7-digit
    colour reaches pango as-is. This is the always-runnable half of the grammar
    check — it needs no nix."""
    text = open(_TABLE, encoding="utf-8").read()
    rx = blk.slot_pattern(text)
    assert rx is not None
    for line, want, why in _GRAMMAR_CORPUS:
        got = rx.match(line) is not None
        assert got == want, "%r -> %s, expected %s (%s)" % (line, got, want, why)


def _corpus_table(tmp_path, lines, name="scratch-slots.sh"):
    """A synthetic slot table: the REAL marker line plus the corpus lines.

    The marker is copied verbatim from the real file, so both readers get the
    production grammar and the only variable is the corpus."""
    marker = [ln for ln in open(_TABLE, encoding="utf-8").read().splitlines()
              if ln.startswith("# SLOT_ENTRY_RE: ")]
    assert len(marker) == 1
    p = tmp_path / name
    p.write_text("\n".join(marker + ["SCRATCH_SLOTS=("] + list(lines) + [")"])
                 + "\n", encoding="utf-8")
    return p


@_NIX_MISSING
def test_the_NIX_and_PYTHON_slot_grammars_CLASSIFY_THE_CORPUS_IDENTICALLY(
        tmp_path):
    """🔴 THE CROSS-ARTIFACT COMPARISON, THROUGH THE REAL READERS.

    Both sides read the same pattern string, but they run it through DIFFERENT
    regex engines — nix's POSIX ERE (`builtins.match`, whole-string) and
    Python's `re` (anchored with `^…$` under MULTILINE). "Same bytes" is not
    "same verdict" until something evaluates both.

    🔴 AND IT MUST BE THE REAL READERS. This test used to build its own
    `map (l: builtins.match p l != null) c` expression, which evaluated the
    MARKER rather than the reader — so it never opened default.nix at all and
    passed happily off a dead binding. It now imports
    nix/programs/tmux/slot-table.nix, the file default.nix derives every one of
    its bindings from, and feeds it a table on disk."""
    lines = [c[0] for c in _GRAMMAR_CORPUS]
    table = _corpus_table(tmp_path, lines)
    nix_slot_lines = _nix_eval(_slot_table_expr(table, "slotLines"))
    nix_says = [ln in nix_slot_lines for ln in lines]

    rx = blk.slot_pattern(table.read_text())
    assert rx is not None
    py_says = [rx.match(ln) is not None for ln in lines]

    # Positive control: the corpus is not all-False (a nix expression wired to
    # nothing would return all False and agree with a Python regex that also
    # matched nothing) and not all-True.
    assert any(nix_says) and any(py_says), (nix_says, py_says)
    assert not all(nix_says), nix_says

    disagreements = [(ln, p, n) for ln, p, n in zip(lines, py_says, nix_says)
                     if p != n]
    assert not disagreements, (
        "the tmux key bindings and the bar legend disagree about what a slot "
        "IS: %r" % disagreements)
    assert py_says == [c[1] for c in _GRAMMAR_CORPUS]


@_NIX_MISSING
def test_the_CANDIDATE_yardsticks_are_WIDER_than_the_grammar_on_BOTH_sides(
        tmp_path):
    """🔴 F(round-2)-2. THE FLOOR CANNOT FIRE IF ITS YARDSTICK NARROWS WITH THE
    THING IT MEASURES.

    The shortfall check compares "entries the grammar accepted" against
    "entries the file declares". When both patterns were spelled `[ ]*`, a
    TAB-indented entry fell out of BOTH counts at once — 19 of 19 — so nix shipped
    19 bindings with no throw and the legend rendered 19 slots with no `?`,
    while bash put 20 in `SCRATCH_SLOTS`. The floor was structurally blind to
    the whole class of defects it exists for.

    So both candidate patterns are now deliberately wider than the grammar, and
    this pins BOTH directions: every grammar match is a candidate (or the floor
    reads negative and means nothing), and specific non-matches ARE candidates
    (or the floor cannot see them go missing)."""
    lines = [c[0] for c in _CANDIDATE_CORPUS]
    table = _corpus_table(tmp_path, lines)
    nix_cands = _nix_eval(_slot_table_expr(table, "candidateLines"))
    nix_says = [ln in nix_cands for ln in lines]
    py_cands = set(blk._CANDIDATE_RE.findall(table.read_text()))
    py_says = [ln in py_cands for ln in lines]

    assert any(nix_says) and not all(nix_says), nix_says      # controls
    assert nix_says == [c[1] for c in _CANDIDATE_CORPUS], list(
        zip(lines, nix_says))
    assert py_says == [c[1] for c in _CANDIDATE_CORPUS], list(
        zip(lines, py_says))

    # WIDTH, as an implication over the grammar corpus: slot ⇒ candidate.
    gtable = _corpus_table(tmp_path, [c[0] for c in _GRAMMAR_CORPUS],
                           name="g.sh")
    rx = blk.slot_pattern(gtable.read_text())
    g_cands = set(blk._CANDIDATE_RE.findall(gtable.read_text()))
    narrower = [c[0] for c in _GRAMMAR_CORPUS
                if rx.match(c[0]) and c[0] not in g_cands]
    assert not narrower, (
        "the Python candidate yardstick is NARROWER than the grammar on %r — "
        "the floor would read negative" % narrower)
    nix_g_slots = set(_nix_eval(_slot_table_expr(gtable, "slotLines")))
    nix_g_cands = set(_nix_eval(_slot_table_expr(gtable, "candidateLines")))
    assert not (nix_g_slots - nix_g_cands), (
        "the nix candidate yardstick is NARROWER than the grammar on %r"
        % sorted(nix_g_slots - nix_g_cands))
    # …and STRICTLY wider: at least one declared line is not a slot, or the
    # floor can never fire at all.
    assert nix_g_cands - nix_g_slots, (nix_g_cands, nix_g_slots)


@_NIX_MISSING
def test_a_TAB_INDENTED_entry_is_a_slot_to_ALL_THREE_readers(tmp_path):
    """🔴 F(round-2)-2, the behavioural half, on a table shaped like the real one.

    BEFORE (measured on ffe4e5d0, one real entry re-indented with a tab):
    bash 20 entries, the legend 19 slots and NO `?`, nix `nSlots=19
    nCandidates=19` and NO throw — 19 key bindings shipped for a 20-entry table.
    AFTER: all three read 20."""
    real = open(_TABLE, encoding="utf-8").read()
    tabbed = real.replace('    "scratch5:p:', '\t"scratch5:p:', 1)
    assert tabbed != real and "\t\"scratch5:p:" in tabbed
    p = tmp_path / "scratch-slots.sh"
    p.write_text(tabbed, encoding="utf-8")

    from_bash = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s\\n" "${SCRATCH_SLOTS[@]}"', "_",
         str(p)], capture_output=True, text=True, check=True)
    bash_n = len([ln for ln in from_bash.stdout.splitlines() if ln.strip()])

    slots = blk.load_slots([str(p)])
    assert slots is not None, "the legend refused a table bash reads fine"
    assert len(slots) == bash_n == len(_bash_slot_entries())
    assert blk.render(slots, {})["text"] != blk.UNMEASURED

    cfg = _nix_eval(_tmux_module_extra_config(p))
    assert len(_generated_bindings(cfg)) == bash_n
    # the tab-indented entry specifically got its binding
    assert ("scratch5", "p", "#cc241d", "poppy") in _generated_bindings(cfg)


@_NIX_MISSING
def test_nix_REFUSES_a_table_whose_colour_the_OLD_grammar_would_have_taken(
        tmp_path):
    """🔴 FAIL-CLOSED, on the line that DISCRIMINATES the grammars.

    A 5-hex-digit colour is not a pango colour and is not a slot. The grammar
    this file used to carry on the nix side (`#[0-9a-fA-F]+`) accepted it, so a
    regression to that grammar generates a binding here where the shared one
    refuses the whole table. Run through the real module: the build must THROW,
    not ship 20 bindings one of which is wrong.

    `tryEval` rather than an expected exception, so the failure message is
    nix's own."""
    entries = ['    "scratch:g:#b8bb26:grove"',
               '    "scratch2:G:#12345:Gold"']        # 5 hex digits
    table = _corpus_table(tmp_path, entries)
    ok, _v = _nix_try(_tmux_module_extra_config(table))
    assert not ok, (
        "nix generated bindings for a table containing a malformed colour "
        "instead of refusing it — the shortfall floor did not fire")
    # …and the legend refuses it the same way, for the same reason.
    assert blk.load_slots([str(table)]) is None

    # 🔴 CONTROL, and it is the whole reason this test is not vacuous: the SAME
    # shape with a legal colour must BUILD. Without it, a module that threw
    # unconditionally would pass the assertion above.
    good = _corpus_table(tmp_path, ['    "scratch:g:#b8bb26:grove"',
                                    '    "scratch2:G:#123456:Gold"'],
                         name="good.sh")
    ok2, cfg = _nix_try(_tmux_module_extra_config(good))
    assert ok2, "the control table did not build"
    assert len(_generated_bindings(cfg)) == 2


@_NIX_MISSING
def test_an_entry_with_a_TRAILING_COMMENT_is_refused_LOUDLY_by_both(tmp_path):
    """⚠ THE SHAPE BASH AND THE GRAMMAR GENUINELY DISAGREE ABOUT.

    `"a:b:#c:d" # note` is NOT a slot to the grammar. The corpus used to
    justify that with "bash would not put it in the array either", which is
    FALSE — MEASURED: `${#SCRATCH_SLOTS[@]}` counts it, and the element is the
    quoted string. So it is a real disagreement, and what this pins is that it
    is now LOUD: the wider candidate yardstick counts the line as declared, the
    shortfall floor fires, nix throws and the legend renders `?`. It used to be
    silently dropped by two of the three readers."""
    entries = ['    "scratch:g:#b8bb26:grove"',
               '    "scratch2:G:#d79921:Gold" # a trailing comment']
    table = _corpus_table(tmp_path, entries)

    from_bash = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s\\n" "${SCRATCH_SLOTS[@]}"', "_",
         str(table)], capture_output=True, text=True, check=True)
    assert from_bash.stdout.split() == ["scratch:g:#b8bb26:grove",
                                        "scratch2:G:#d79921:Gold"], (
        "bash no longer accepts a trailing-comment entry — the corpus's stated "
        "reason depends on this measurement: %r" % from_bash.stdout)

    assert blk.load_slots([str(table)]) is None
    ok, _v = _nix_try(_tmux_module_extra_config(table))
    assert not ok


@_NIX_MISSING
def test_TRAILING_WHITESPACE_on_the_marker_line_is_stripped_by_BOTH(tmp_path):
    """🟢 One trailing space used to break the nix build with a message blaming
    twenty correct entries.

    Python read the marker with `(\\S.*?)[ ]*$` — stripping — while nix read
    `(.*)`, which does not. MEASURED: the compiled nix grammar then ended in
    `[ ]* ` and matched nothing, so the shortfall threw `declares=20 matched=0`
    and told the reader to fix the colour fields, while the legend rendered all
    20 slots fine. Fails closed, but misdiagnoses — inside the mechanism built
    to end disagreements. Both sides now strip trailing spaces AND tabs."""
    real = open(_TABLE, encoding="utf-8").read()
    marker = [ln for ln in real.splitlines()
              if ln.startswith("# SLOT_ENTRY_RE: ")][0]
    for pad in ("   ", "\t", " \t "):
        p = tmp_path / ("m%d.sh" % len(pad))
        p.write_text(real.replace(marker, marker + pad), encoding="utf-8")
        nix_re = _nix_eval(_slot_table_expr(p, "slotRe"))
        assert nix_re == _marker_lines()[0], (pad, nix_re)
        slots = blk.load_slots([str(p)])
        assert slots is not None and len(slots) == len(_bash_slot_entries()), (
            "the legend lost entries to %r of trailing whitespace" % pad)
        ok, cfg = _nix_try(_tmux_module_extra_config(p))
        assert ok, "a trailing %r on the marker line broke the nix build" % pad
        assert len(_generated_bindings(cfg)) == len(_bash_slot_entries())


@_NIX_MISSING
def test_the_GROUP_INDEX_contract_is_CHECKED_on_both_sides(tmp_path):
    """🟢 The marker carries the grammar but NOT which group means what.

    nix reads `elemAt m 4` for the name, Python reads `group(5)`. Nothing in the
    marker expresses that, and a four-group grammar made `load_slots` raise
    `IndexError: no such group` — falsifying its own "Never raises" docstring.
    Both sides now assert the arity: Python returns None (the `?` discriminant)
    and nix throws with a message that names the contract."""
    assert blk._GROUP_COUNT == 5
    real = open(_TABLE, encoding="utf-8").read()
    marker = [ln for ln in real.splitlines()
              if ln.startswith("# SLOT_ENTRY_RE: ")][0]
    # A four-group grammar of the same shape: the colour's OUTER group (the one
    # carrying the `#`) is dropped, so the alternation keeps its own capture and
    # the arity falls to 4. Entries still match, so arity is the ONLY defect —
    # and the edit uses no construct outside the POSIX-ERE/Python intersection
    # (`(?:` is not POSIX, and would make nix fail for the wrong reason).
    four = marker.replace(
        "(#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{9}|[0-9a-fA-F]{12}))",
        "#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{9}|[0-9a-fA-F]{12})")
    assert four != marker
    p = tmp_path / "scratch-slots.sh"
    p.write_text(real.replace(marker, four), encoding="utf-8")

    # control: the grammar still MATCHES the entries — arity is the only defect
    body = four.split("# SLOT_ENTRY_RE: ", 1)[1]
    probe = re.compile("^" + body + "$", re.M)
    assert probe.groups == 4
    assert probe.match('    "scratch4:V:#83a598:Vapor"')

    assert blk.slot_pattern(p.read_text()) is None
    assert blk.load_slots([str(p)]) is None          # and it does NOT raise
    ok, _v = _nix_try(_tmux_module_extra_config(p))
    assert not ok


def test_BASH_and_the_regex_agree_on_the_REAL_table():
    """The third reader. bash `source`s this file; the other two read it with
    the marker's regex. A slot bash puts in `SCRATCH_SLOTS` that the regex drops
    is a scratchpad with a HUD entry, no key binding and no legend entry."""
    from_bash = _bash_slot_entries()
    slots = blk.load_slots([_TABLE])
    assert slots is not None
    rebuilt = ["%s:%s:%s:%s" % s for s in slots]
    assert rebuilt == from_bash, (
        "bash and the slot grammar disagree about the real table:\n"
        "  bash-only:  %r\n  regex-only: %r"
        % (sorted(set(from_bash) - set(rebuilt)),
           sorted(set(rebuilt) - set(from_bash))))


def test_load_slots_needs_EXACTLY_ONE_grammar_marker(tmp_path):
    """Zero markers means this is not the canonical table; two means a stray
    copy could silently win. Either way the honest answer is `?`, never a guess
    at the grammar — a guessed grammar is how the two readers drifted apart in
    the first place."""
    real = open(_TABLE, encoding="utf-8").read()
    marker_line = [ln for ln in real.splitlines()
                   if ln.startswith("# SLOT_ENTRY_RE: ")][0]

    none = tmp_path / "none.sh"
    none.write_text(real.replace(marker_line + "\n", ""), encoding="utf-8")
    assert blk.load_slots([str(none)]) is None

    two = tmp_path / "two.sh"
    two.write_text(real.replace(marker_line, marker_line + "\n" + marker_line),
                   encoding="utf-8")
    assert blk.load_slots([str(two)]) is None

    # control: the untouched file still parses, so the two above failed for the
    # marker and not because the fixture was mangled
    good = tmp_path / "good.sh"
    good.write_text(real, encoding="utf-8")
    assert blk.load_slots([str(good)]) is not None


def test_a_PARTIALLY_parseable_table_is_UNMEASURED_not_a_short_legend(tmp_path):
    """🔴 F2. `if slots:` accepted ANY non-empty parse, so a table whose colour
    fields had mostly rotted rendered a short, plausible, wrong legend.

    MEASURED on the shipped code: corrupt 17 of the 20 colour fields and it
    rendered `g3 G v` — three slots, no `?`, indistinguishable from a design
    choice. The floor compares what the grammar ACCEPTED against what the file
    DECLARES (quoted strings alone on a line), so it needs no literal 20."""
    real = open(_TABLE, encoding="utf-8").read()
    lines = real.splitlines(keepends=True)
    corrupted, n = [], 0
    for ln in lines:
        m = re.match(r'^(\s*)"([^":]+):([^":]+):#[0-9a-fA-F]+:([^"]+)"(\s*)$', ln)
        if m and n < 17:
            n += 1
            corrupted.append('%s"%s:%s:#zzz:%s"%s\n'
                             % (m.group(1), m.group(2), m.group(3), m.group(4),
                                m.group(5).rstrip("\n")))
        else:
            corrupted.append(ln)
    assert n == 17, n                      # the fixture really did corrupt 17

    broken = tmp_path / "scratch-slots.sh"
    broken.write_text("".join(corrupted), encoding="utf-8")

    # the grammar still accepts the 3 survivors — that is the whole hazard
    rx = blk.slot_pattern(broken.read_text())
    assert len(rx.findall(broken.read_text())) == 3

    assert blk.load_slots([str(broken)]) is None, (
        "a table that parsed to 3 of 20 entries was accepted as a reading")
    assert blk.render(blk.load_slots([str(broken)]), {})["text"] == blk.UNMEASURED


def test_the_floor_lets_a_LATER_path_rescue_a_corrupt_earlier_one(tmp_path):
    """The floor is per-file: a corrupt deployed copy must fall through to a
    good one, not blank the pill."""
    real = open(_TABLE, encoding="utf-8").read()
    broken = tmp_path / "scratch-slots.sh"
    broken.write_text(real.replace("#b8bb26", "#zz"), encoding="utf-8")
    assert blk.load_slots([str(broken)]) is None          # alone: unmeasured
    slots = blk.load_slots([str(broken), _TABLE])         # with a fallback: fine
    assert slots is not None and len(slots) == len(_bash_slot_entries())


# --------------------------------------------------------------------------- #
# render()
# --------------------------------------------------------------------------- #
def test_render_colours_and_bolds_present_slots_and_dims_absent_ones():
    out = blk.render(_SLOTS, _SESSIONS)
    assert out["state"] == "Idle"
    text = out["text"]
    assert text == (
        '<span foreground="#111111" weight="bold">a7</span>'
        ' <span foreground="#504945">b</span>'   # DIM, NOT the slot's own colour
        ' <span foreground="#333333" weight="bold">c4</span>'
    ), text


def test_render_dims_an_absent_slot_and_gives_it_NO_count():
    out = blk.render(_SLOTS, _SESSIONS)
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    spans = {s.text[0]: s for s in tree}
    assert spans["b"].get("foreground") == blk.DIM
    assert spans["b"].get("weight") is None
    assert spans["b"].text == "b"          # key alone — no digits
    # …and the present ones DO carry their own colour + count
    assert spans["a"].get("foreground") == "#111111"
    assert spans["a"].get("weight") == "bold"
    assert spans["a"].text == "a7"
    assert spans["c"].text == "c4"


def test_render_keeps_slot_table_ORDER_and_single_space_separation():
    out = blk.render(_SLOTS, _SESSIONS)
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    assert [s.text for s in tree] == ["a7", "b", "c4"]
    # exactly one space between spans, none leading or trailing
    assert "  " not in out["text"]
    assert out["text"] == out["text"].strip()
    assert out["text"].count("</span> <span") == len(_SLOTS) - 1


def test_render_with_zero_live_sessions_is_a_FULL_DIM_LEGEND_not_a_question_mark():
    """🔴 THE READING THAT LOOKS LIKE NOTHING. An empty session map is a REAL
    measurement — the operator has no scratchpads up — and must render every
    slot, dim. If this ever renders `?` the pill has stopped distinguishing
    "measured zero" from "could not measure" in the other direction."""
    out = blk.render(_SLOTS, {})
    assert out["state"] == "Idle"
    assert out["text"] != blk.UNMEASURED
    assert "?" not in out["text"]
    tree = ET.fromstring("<r>" + out["text"] + "</r>")
    assert [s.text for s in tree] == ["a", "b", "c"]
    assert {s.get("foreground") for s in tree} == {blk.DIM}
    assert all(s.get("weight") is None for s in tree)


def test_render_unmeasured_when_the_slot_table_is_None():
    out = blk.render(None, _SESSIONS)
    assert out["text"] == blk.UNMEASURED
    assert out["text"].endswith("?")
    assert out["state"] == "Idle"
    assert out["text"] != blk.render(_SLOTS, {})["text"]


def test_render_unmeasured_when_the_session_map_is_None():
    out = blk.render(_SLOTS, None)
    assert out["text"] == blk.UNMEASURED
    assert out["text"] != blk.render(_SLOTS, {})["text"], (
        "an unmeasured read rendered as a measured zero")


def test_render_branches_on_IS_NONE_not_on_truthiness():
    """{} and None are both falsy. Branching on truthiness is the single edit
    that collapses "measured zero" into "could not measure"."""
    assert blk.render(_SLOTS, {})["text"] != blk.UNMEASURED
    assert blk.render(_SLOTS, None)["text"] == blk.UNMEASURED
    assert blk.render([], _SESSIONS)["text"] == blk.UNMEASURED


# --------------------------------------------------------------------------- #
# fetch_sessions() — the tool-output parsing seam
# --------------------------------------------------------------------------- #
def test_fetch_sessions_parses_names_and_window_counts():
    got = blk.fetch_sessions(
        run=lambda: (0, "scratchA 7\nscratchC 4\nmain 12\n", ""))
    assert got == {"scratchA": 7, "scratchC": 4, "main": 12}


_NO_SOCKET = lambda: (None, None)                    # noqa: E731
_SOCKET_FOUND = lambda: ("/run/user/1000",           # noqa: E731
                         "/run/user/1000/tmux-1000/default")


def test_fetch_sessions_ignores_malformed_lines():
    """Malformed lines are skipped — but only while SOMETHING parsed. The
    all-garbage case is a different claim and is pinned separately below."""
    got = blk.fetch_sessions(
        run=lambda: (0, "scratchA 7\ngarbage\nscratchB notanumber\n", ""),
        socket_finder=_NO_SOCKET)
    assert got == {"scratchA": 7}


def test_fetch_sessions_rc_ZERO_with_unreadable_output_is_UNMEASURED():
    """🔴 F4: THE FALSE ZERO ON THE SUCCESS PATH. The docstring promised that a
    tmux format change would cost a visible `?`; that was true only of the
    NON-ZERO-exit path. `tmux` exiting 0 with output this parser cannot read a
    single line of used to yield `{}` — the MEASURED-ZERO value — and render the
    full dim legend while every scratchpad was live."""
    for out in ("some new format tmux invented\n",
                "scratchA|7\nscratchB|4\n",
                "   \tnot-a-pair\n",
                "a b c\nd e f\n"):
        got = blk.fetch_sessions(run=lambda o=out: (0, o, ""),
                                 socket_finder=_NO_SOCKET)
        assert got is None, (out, got)
        assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_rc_ZERO_with_EMPTY_output_is_still_a_REAL_ZERO():
    """The control on the rule above, and the case it must NOT swallow: a
    server holding no sessions prints nothing at all. That is a reading of zero
    and renders the full dim legend."""
    for out in ("", "\n", "   \n\n"):
        got = blk.fetch_sessions(run=lambda o=out: (0, o, ""),
                                 socket_finder=_NO_SOCKET)
        assert got == {}, (out, got)
        assert got is not None
        assert blk.render(_SLOTS, got)["text"] != blk.UNMEASURED


def test_fetch_sessions_no_server_running_is_a_REAL_ZERO():
    """🔴 The one non-zero exit that IS a reading. tmux exits 1 with `no server
    running on /tmp/tmux-1000/default` when nothing is up — that is genuinely
    "zero sessions", and it must render the legend, not `?`.

    Conditioned on there being no socket anywhere: see the next test."""
    got = blk.fetch_sessions(
        run=lambda: (1, "", "no server running on /tmp/tmux-1000/default\n"),
        socket_finder=_NO_SOCKET)
    assert got == {}
    assert got is not None
    out = blk.render(_SLOTS, got)
    assert out["text"] != blk.UNMEASURED
    assert "?" not in out["text"]


def test_no_server_running_is_a_real_zero_ONLY_WHEN_NO_SOCKET_EXISTS():
    """🔴 F5: `no server running on <path>` names the path tmux LOOKED AT, which
    is not necessarily where the socket is.

    ⚠ THIS IS DEFENCE AGAINST A STATE NOT CURRENTLY OBSERVED, and the round-1
    version of this docstring said otherwise. It claimed the bar's environment
    HAS no `TMUX_TMPDIR` because the variable is exported by the interactive
    shell. MEASURED 2026-09-11 on the LIVE `i3status-rs` process
    (`/proc/<pid>/environ`): it carries `TMUX_TMPDIR=/run/user/1000`, and under
    exactly that environment a bare `tmux list-sessions` answers correctly. The
    old measurement was taken with the variable UNSET — a condition the bar is
    not in. No live bug was demonstrated.

    What IS true: `TMUX_TMPDIR` is undeclared runtime state whose source is
    unestablished, so nothing guarantees the next process inherits it; and both
    failure modes are real if it ever goes missing. MEASURED with it stripped,
    same tmux 3.7c: tmux looks at `/tmp/tmux-1000/default` while the sessions
    are on `/run/user/1000/tmux-1000/default` (a persistent `?`), and a stale
    socket left in /tmp by any process turns that into the `no server running`
    spelling — a FALSE ZERO, a full dim legend over a full house. That second
    one is what this test pins."""
    err = "no server running on /tmp/tmux-1000/default\n"
    assert blk.fetch_sessions(run=lambda: (1, "", err),
                              socket_finder=_SOCKET_FOUND) is None
    # and the discriminating control: same stderr, no socket anywhere -> zero
    assert blk.fetch_sessions(run=lambda: (1, "", err),
                              socket_finder=_NO_SOCKET) == {}


def test_fetch_sessions_no_server_running_match_is_case_insensitive():
    assert blk.fetch_sessions(
        run=lambda: (1, "", "NO SERVER RUNNING on /tmp/x\n"),
        socket_finder=_NO_SOCKET) == {}


def test_socket_roots_name_run_user_LITERALLY_not_only_via_XDG(monkeypatch):
    """🔴 `$XDG_RUNTIME_DIR` is not guaranteed in a bar block's environment
    either, so `/run/user/<uid>` is listed as a literal. Measured with BOTH
    variables stripped, which is the case that matters."""
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    roots = blk._socket_roots()
    assert "/run/user/%d" % os.getuid() in roots, roots
    assert roots[-1] == "/tmp", roots          # tmux's own fallback, last


def test_an_explicit_TMUX_TMPDIR_wins_the_OUTCOME_not_merely_the_ORDER(
        monkeypatch):
    """🟢 The comment here used to read "an explicit `TMUX_TMPDIR` still wins",
    and it asserted `_socket_roots()[0]` — which is a claim about the ORDER.

    Being first in a fall-through list is not winning. If the declared root held
    no socket, `find_socket` fell past it and `_run_tmux` then OVERRODE the
    operator's own declaration with `/tmp`, reporting a DIFFERENT server's
    sessions as the answer — the false-reading class this whole block exists to
    refuse. A declaration now ENDS the search: it is the only root, and a
    declared root with no socket yields (None, None) so the ambient environment
    is passed through untouched."""
    monkeypatch.setenv("TMUX_TMPDIR", "/somewhere/else")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    assert blk._socket_roots() == ["/somewhere/else"], blk._socket_roots()

    # THE OUTCOME: a declared root with no socket must not resolve to another.
    assert blk.find_socket(exists=lambda p: p.startswith("/tmp/")) == (
        None, None)
    # control: the same lookup DOES find one under the declared root
    root, sock = blk.find_socket(
        exists=lambda p: p.startswith("/somewhere/else/"))
    assert root == "/somewhere/else" and sock.startswith("/somewhere/else/")

    # …and `_run_tmux` leaves the declaration alone when nothing was found.
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        captured["env"] = kw.get("env")
        return _P()

    monkeypatch.setattr(blk.subprocess, "run", fake_run)
    monkeypatch.setattr(blk, "find_socket", lambda: (None, None))
    blk._run_tmux()
    assert captured["env"]["TMUX_TMPDIR"] == "/somewhere/else", captured["env"]


def test_find_socket_returns_the_FIRST_root_that_has_one():
    seen = []

    def exists(p):
        seen.append(p)
        return p == "/run/user/%d/tmux-%d/default" % (os.getuid(), os.getuid())

    root, sock = blk.find_socket(roots=["/tmp", "/run/user/%d" % os.getuid()],
                                 exists=exists)
    assert root == "/run/user/%d" % os.getuid()
    assert sock == "/run/user/%d/tmux-%d/default" % (os.getuid(), os.getuid())
    # negative control: nothing anywhere -> (None, None), not a crash
    assert blk.find_socket(roots=["/tmp"], exists=lambda p: False) == (None, None)


def test_run_tmux_POINTS_TMUX_AT_THE_SOCKET_IT_FOUND(monkeypatch):
    """🔴 Finding the socket is worth nothing unless the subprocess is told.
    This is the seam between `find_socket` and the tmux invocation — each side
    is individually plausible and neither test above can see it."""
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "scratch 1\n", ""

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env")
        return _P()

    monkeypatch.setattr(blk.subprocess, "run", fake_run)
    monkeypatch.setattr(blk, "find_socket", lambda: ("/run/user/4242", "/s"))
    rc, out, err = blk._run_tmux()
    assert rc == 0 and out == "scratch 1\n"
    assert captured["argv"][0] == "tmux"
    assert captured["env"]["TMUX_TMPDIR"] == "/run/user/4242", captured["env"]

    # …and when nothing is found the ambient environment is left alone rather
    # than being pointed somewhere arbitrary.
    monkeypatch.setenv("TMUX_TMPDIR", "/ambient")
    monkeypatch.setattr(blk, "find_socket", lambda: (None, None))
    blk._run_tmux()
    assert captured["env"]["TMUX_TMPDIR"] == "/ambient", captured["env"]


def test_fetch_sessions_an_UNEXPECTED_nonzero_exit_is_unmeasured():
    """🔴 The fallback direction is deliberately toward `?`. A tmux that is
    wedged, a broken socket, a permissions error — none of those is a reading of
    zero, and calling them zero is the quiet lie this block exists to avoid."""
    got = blk.fetch_sessions(
        run=lambda: (1, "", "error connecting to /tmp/tmux-1000/default "
                            "(Permission denied)\n"))
    assert got is None
    assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_missing_tmux_executable_is_unmeasured():
    def boom():
        raise FileNotFoundError(2, "No such file or directory", "tmux")

    got = blk.fetch_sessions(run=boom)
    assert got is None
    assert blk.render(_SLOTS, got)["text"] == blk.UNMEASURED


def test_fetch_sessions_any_other_exception_is_unmeasured():
    def boom():
        raise RuntimeError("timeout expired")

    assert blk.fetch_sessions(run=boom) is None


# --------------------------------------------------------------------------- #
# JSON + pango well-formedness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slots,sessions", [
    (_SLOTS, _SESSIONS),
    (_SLOTS, {}),
    (None, _SESSIONS),
    (_SLOTS, None),
    (None, None),
])
def test_every_render_is_valid_json_and_always_Idle(slots, sessions):
    parsed = json.loads(json.dumps(blk.render(slots, sessions)))
    assert parsed["state"] == "Idle"
    assert isinstance(parsed["text"], str) and parsed["text"] != ""


def test_the_emitted_text_is_WELL_FORMED_PANGO_MARKUP():
    """🔴 i3status-rust passes `text` through `$text.pango-str()`, so the string
    IS markup, and it does NOT sanitise it.

    MEASURED 2026-09-11 (i3status-rs 0.36.1, throwaway TOML, `timeout 6`): a
    block emitting `a < b & c` reaches the i3bar line as `full_text":" a < b &
    c "` with `"markup":"pango"` — verbatim, unescaped, unvalidated, and the
    other blocks on the line are still emitted. ⚠ SO THE SCOPE OF THE DAMAGE IS
    NOT MEASURED HERE: what pango and i3bar do with invalid markup, and whether
    it costs one block or the line, was NOT observed — checking it means
    rendering the operator's real bar. An earlier version of this docstring
    asserted "breaks the whole bar line, not just this block"; that was never
    verified and is withdrawn. What IS established is that nothing between this
    script and pango will escape a stray `<` for us, which is reason enough to
    escape it here. Parse the output as XML so a future unescaped character
    fails here rather than on the operator's screen."""
    ET.fromstring("<r>" + blk.render(_SLOTS, _SESSIONS)["text"] + "</r>")
    ET.fromstring("<r>" + blk.render(_SLOTS, {})["text"] + "</r>")
    # …and against the REAL table, so a hostile character landing in the slot
    # file is caught too.
    real = blk.load_slots([os.path.join(_SCRIPTS, "tmux-scratch-slots.sh")])
    ET.fromstring("<r>" + blk.render(real, {"scratch": 3})["text"] + "</r>")


def test_a_hostile_key_is_ESCAPED_not_interpolated_raw():
    """🔴 THE GUARD'S REACHABILITY CASE, not a hypothetical: feed a slot whose
    key really does contain `<` and `&` and watch it come back escaped. Without
    `html.escape` the XML parse below raises and the bar line is corrupt."""
    hostile = [("scratchX", "<&x>", "#444444", "Hos<tile&"),
               ("scratchY", "q", "#555555", "Quiet")]
    out = blk.render(hostile, {"scratchX": 5})
    assert "<span" in out["text"]
    # the raw hostile key must NOT appear; its escaped form must
    assert "<&x>" not in out["text"].replace("<span", "").replace("</span>", "")
    assert html.escape("<&x>") in out["text"]
    tree = ET.fromstring("<r>" + out["text"] + "</r>")          # must not raise
    assert [s.text for s in tree] == ["<&x>5", "q"]             # round-trips


def test_the_last_resort_pill_IS_the_unmeasured_pill():
    """A second hand-spelled literal is how the discriminant drifts back to an
    empty legend while every other path stays correct."""
    assert blk._UNMEASURED_PILL == blk.render(None, None)
    assert blk._UNMEASURED_PILL["text"] == blk.UNMEASURED
    assert blk._UNMEASURED_PILL["text"].endswith("?")
    src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
               encoding="utf-8").read()
    tail = src.split('if __name__ == "__main__":')[1]
    assert "_UNMEASURED_PILL" in tail, tail


# --------------------------------------------------------------------------- #
# The nix seam: the block, its click, and its sibling data file
# --------------------------------------------------------------------------- #
def _nix_block_body(name):
    nix = _GRAPHICAL.read_text()
    m = re.search(r"%s = \{(.*?)^  \};" % re.escape(name), nix, re.S | re.M)
    assert m, "%s not found in nix/graphical.nix — repin this test" % name
    return nix, m.group(1)


def test_the_scratchPickerCmd_STRING_runs_the_picker_in_a_TERMINAL():
    """🔴 HALF ONE OF TWO, and it never opens `scratchpadsBlock`.

    i3status-rust runs a click through `sh -c` with NO controlling terminal and
    the picker is an fzf TUI — MEASURED: with stdin on /dev/null it dies
    `inappropriate ioctl for device` and the click is a silent no-op. So the
    click must spawn a terminal, exactly like `syshealthCmd`.

    The sibling `…are_wired_to_scratchPickerCmd` is the other half: the repo's
    own measurement on `runawaysBlock` was that pinning the string ALONE leaves
    "a perfect string nothing calls" surviving a mutation."""
    nix = _GRAPHICAL.read_text()
    m = re.search(r'^\s*scratchPickerCmd\s*=\s*"(?P<cmd>.*)";\s*$', nix, re.M)
    assert m, "scratchPickerCmd is gone or reshaped — repin this test"
    cmd = m.group("cmd")
    assert cmd.lstrip().startswith("alacritty"), (
        "the click must run in a TERMINAL — i3status-rust spawns it with no "
        "controlling tty: %r" % cmd)
    assert "scratch-picker.sh" in cmd, cmd
    # Negative control: the regex CAN read a different binding's value, so a
    # green here is not the parser silently matching nothing.
    other = re.search(r'^\s*syshealthCmd\s*=\s*"(?P<cmd>.*)";\s*$', nix, re.M)
    assert other and "syshealth" in other.group("cmd")


def test_the_scratchpads_pill_CLICK_is_wired_to_scratchPickerCmd():
    """🔴 THE MISSING HALF. MEASURED on `runawaysBlock` in this very file's
    neighbour (`test_bar_status.py`): with only the string guard, re-pointing
    the block's click at a different binding left the suite green — a perfect
    string nothing calls. So assert what the BLOCK commands."""
    nix, body = _nix_block_body("scratchpadsBlock")
    wired = dict(re.findall(r'button = "(\w+)";\s*cmd = ([^;]+);', body))
    assert wired, "scratchpadsBlock declares no `button = …; cmd = …;`: %r" % body
    assert wired.get("left") == "scratchPickerCmd", (
        "the scratchpad legend's left-click does not run `scratchPickerCmd` — "
        "it is wired to %r. wired=%r" % (wired.get("left"), wired))
    # the identifier it names must exist
    assert re.search(r'^\s*scratchPickerCmd\s*=\s*"', nix, re.M), (
        "scratchpadsBlock commands `scratchPickerCmd` but graphical.nix defines "
        "no such binding")
    # Negative control: the same parse on runawaysBlock reads a DIFFERENT value,
    # so this is not a regex that matches whatever it is pointed at.
    _n, runaways = _nix_block_body("runawaysBlock")
    assert dict(re.findall(r'button = "(\w+)";\s*cmd = ([^;]+);',
                           runaways)).get("left") == "syshealthCmd"


def test_the_SLOT_TABLE_is_deployed_BESIDE_the_block_script():
    """🔴 THE DEPLOY NOBODY WOULD MISS. On a live host the block script is a
    lone nix-store symlink in ~/.config/i3status-rust/scripts and the slot table
    is symlinked in beside it as `scratch-slots.sh` — leg 1 of `_SLOT_PATHS`,
    and the only leg that is true there.

    Delete that `home.file` entry and leg 1 fails, leg 2 is absent (there is no
    repo `scripts/` next to a deployed symlink) and LEG 3 SILENTLY SUCCEEDS off
    ~/workspace/devrc — a shared, branch-switching working tree. The pill then
    renders a legend read from whatever branch that checkout happens to be on,
    which is not a failure anyone would notice."""
    nix = _GRAPHICAL.read_text()
    m = re.search(
        r'home\.file\."\.config/i3status-rust/scripts/scratch-slots\.sh"'
        r'\s*=\s*\{(.*?)\};', nix, re.S)
    assert m, (
        "nix/graphical.nix no longer deploys scratch-slots.sh beside the block "
        "script — the legend would fall back to ~/workspace/devrc's working "
        "tree (leg 3), which succeeds silently off whatever branch it is on")
    assert "tmux-scratch-slots.sh" in m.group(1), m.group(1)
    # …and the block script itself is deployed, under the name the block commands
    assert re.search(
        r'home\.file\."\.config/i3status-rust/scripts/i3status-scratchpads"',
        nix), "the block script's own home.file entry is gone"
    _n, body = _nix_block_body("scratchpadsBlock")
    assert "i3status-scratchpads" in body, body


def test_the_deployed_sibling_name_is_the_one_LEG_ONE_looks_for():
    """The two halves of leg 1 are in different files and each is plausible
    alone: the block looks for `scratch-slots.sh`, nix deploys under that name.
    A rename on either side is silent — leg 3 catches it on the dev box."""
    assert os.path.basename(blk._SLOT_PATHS[0]) == "scratch-slots.sh"
    nix = _GRAPHICAL.read_text()
    assert '.config/i3status-rust/scripts/scratch-slots.sh' in nix


def test_the_block_SPAWNS_TMUX_AND_NOTHING_ELSE():
    """🔴 THE PIN BEHIND THIS FILE'S `home-manager` ACKNOWLEDGEMENT in
    scripts/tests/test_no_real_launchers.py.

    That scanner is a TEXT scan, so the one clause of comment prose above
    `_socket_roots` naming home-manager reads as a hit. Acknowledging it is the
    repo's convention — but an acknowledgement with no pin BLINDS the guard it
    is filed under, which was MEASURED once on `tmux-reply-agent`: with the row
    in place, injecting a real `subprocess.run(["systemctl", …])` left that
    suite green.

    So: walk the AST, collect every spawn's argv[0], and pin the set BOTH
    WAYS — a new binary fails, and losing tmux fails too. A spawn built from a
    variable becomes `<computed>` and fails rather than silently leaving the
    set."""
    import ast

    src = open(os.path.join(_SCRIPTS, "i3status-scratchpads"),
               encoding="utf-8").read()
    tree = ast.parse(src)

    SPAWNERS = {"run", "Popen", "call", "check_call", "check_output",
                "getoutput", "getstatusoutput"}
    FORBIDDEN = {"system", "popen", "execv", "execve", "execvp", "execvpe",
                 "execl", "execlp", "execle", "spawnv", "spawnl", "posix_spawn"}
    argv0, forbidden_hits = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name in FORBIDDEN:
            forbidden_hits.append(name)
        if name not in SPAWNERS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            argv0.add(first.value)
        elif isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            argv0.add(head.value if isinstance(head, ast.Constant)
                      and isinstance(head.value, str) else "<computed>")
        else:
            argv0.add("<computed>")

    assert not forbidden_hits, (
        "scripts/i3status-scratchpads reaches a raw exec/system primitive: %r"
        % forbidden_hits)
    assert argv0 == {"tmux"}, (
        "the block's spawn set is %r, not {'tmux'} — the `home-manager` "
        "acknowledgement in test_no_real_launchers.py rests on this file "
        "having exactly one, read-only, tmux call site" % sorted(argv0))


# --------------------------------------------------------------------------- #
# The picker the click runs
# --------------------------------------------------------------------------- #
def test_the_picker_DETACH_branch_requires_TMUX():
    """🔴 F1. `tmux display-message -p '#{session_name}'` does NOT answer "none"
    outside a client — it answers the server's MOST-RECENTLY-USED session. So
    the `scratch*` test alone is a question about the SERVER, not about us.

    REPRODUCED on a private-socket server 2026-09-11, `$TMUX` unset, one client
    attached to the NON-scratch session `work`, `scratch2` MRU: the picker took
    its detach branch and `tmux detach-client` threw the `work` client off the
    server (client-count 1 -> 0), exiting 0 with no output. From the bar that
    reads as "the click did nothing".

    Asserted as STRUCTURE — the `$TMUX` test must GUARD the detach, not merely
    appear in the file."""
    src = open(_PICKER, encoding="utf-8").read()
    # strip comments: the incident is described at length above the code
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "detach-client" in code, code
    guard = re.search(
        r'if \[ -n "\$\{TMUX:-\}" \]; then(?P<body>.*?)\nfi', code, re.S)
    assert guard, (
        "the detach branch is no longer guarded by `[ -n \"${TMUX:-}\" ]` — "
        "without it the picker detaches a live client of whatever session the "
        "server used last:\n%s" % code)
    assert "detach-client" in guard.group("body"), (
        "`detach-client` escaped the $TMUX guard: %r" % guard.group("body"))
    # …and nothing detaches outside it
    assert code.count("detach-client") == 1, code


def test_the_picker_HOLDS_the_terminal_open_when_it_can_do_NEITHER():
    """Attach failed and create failed: with `exec` there was nothing left to
    hold the float terminal open, so it vanished — the same
    indistinguishable-from-nothing failure `syshealthCmd` carries a hold for."""
    src = open(_PICKER, encoding="utf-8").read()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "read -n 1" in code, code
    assert "exec tmux" not in code, (
        "`exec` replaces this shell, so the failure hold below it can never "
        "run: %s" % code)
    tail = code.split("new-session -s")[-1]
    assert "read -n 1" in tail, tail


#: Every external binary scripts/tmux-scratch-picker.sh may invoke. Pinned BOTH
#: WAYS by `test_the_picker_SPAWNS_these_AND_NOTHING_ELSE` below, which is the
#: PIN behind this file's `home-manager` acknowledgement in
#: scripts/tests/test_no_real_launchers.py.
_PICKER_BINARIES = {"tmux", "fzf", "grep", "sort", "id", "date"}


def _picker_sandbox(tmp_path, fzf_body, socket_root=None, tmux_env=None,
                    jail=False, session="work"):
    """Run the picker with FAKE binaries, no real tmux server.

    `jail=True` replaces `$PATH` ENTIRELY with the shim directory, so any
    binary outside `_PICKER_BINARIES` fails `command not found` — that is how
    the spawn set is observed rather than guessed. `jail=False` keeps the
    ambient PATH after the shims, for the tests that only care about tmux.

    The fake tmux records the environment it was handed, which is how the
    socket-finding half is observed at all."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    envlog = tmp_path / "tmux-env.txt"
    calllog = tmp_path / "calls.txt"
    real = {n: shutil.which(n) for n in ("grep", "sort", "id", "date")}
    assert all(real.values()), real
    for name in _PICKER_BINARIES:
        body = {
            "tmux": ('printf "%%s\\n" "TMUX_TMPDIR=${TMUX_TMPDIR-<unset>}" >> %s\n'
                     'case "$1" in\n'
                     '  display-message) printf "%s\\n" ;;\n'
                     '  list-sessions) printf "scratch9\\n" ;;\n'
                     'esac\nexit 0\n' % (json.dumps(str(envlog)), session)),
            "fzf": fzf_body,
        }.get(name, 'exec %s "$@"\n' % real.get(name))
        # 🔴 An ABSOLUTE shebang and a bash EXPANSION, not `/usr/bin/env bash`
        # and `$(basename …)`: under `jail=True` the PATH holds only these
        # shims, so `env` could not find bash and `basename` is not on it — the
        # shims would fail to start and the run would log NOTHING while every
        # "did it reach anything unexpected" assertion stayed green.
        (bindir / name).write_text(
            '#!%s\nprintf "%s\\n" "${0##*/}" >> %s\n%s'
            % (shutil.which("bash"), "%s", json.dumps(str(calllog)), body),
            encoding="utf-8")
        os.chmod(bindir / name, 0o755)

    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "TMUX_TMPDIR", "XDG_RUNTIME_DIR")}
    env["PATH"] = str(bindir) if jail else "%s:%s" % (bindir,
                                                      env.get("PATH", ""))
    if socket_root is not None:
        env["XDG_RUNTIME_DIR"] = str(socket_root)
    if tmux_env is not None:
        env["TMUX"] = tmux_env
    proc = subprocess.run([shutil.which("bash"), _PICKER],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=20)
    logged = envlog.read_text() if envlog.exists() else ""
    calls = set(calllog.read_text().split()) if calllog.exists() else set()
    return proc, logged, calls


def test_the_picker_SPAWNS_these_AND_NOTHING_ELSE(tmp_path):
    """🔴 THE PIN BEHIND THIS FILE'S `home-manager` ACKNOWLEDGEMENT in
    scripts/tests/test_no_real_launchers.py.

    That scanner is a TEXT scan, so the comment above the picker's fzf-exit-code
    branch — which names the `home-manager switch` that blanks `~/.nix-profile`
    for ~1 s, the non-hypothetical reason a bare-name `fzf` can fail to launch —
    reads as a hit. Acknowledging it is the repo's convention, and the words are
    the FINDING rather than decoration. But an acknowledgement with NO PIN
    blinds the guard it is filed under (MEASURED once on `tmux-reply-agent`).

    So: run the script in a PATH JAIL containing only `_PICKER_BINARIES`, drive
    EVERY branch, and assert both directions — nothing outside the set is
    reached (a `command not found` would name it), and the set does not silently
    shrink either.

    ⚠ The jail observes what the RUN reached, so its strength is the branch
    coverage below, not a proof about unexecuted lines. The companion assertion
    — that the comment-stripped source names no host-affecting binary at all —
    is what covers the rest."""
    sock = tmp_path / "rt"
    (sock / ("tmux-%d" % os.getuid())).mkdir(parents=True)
    (sock / ("tmux-%d" % os.getuid()) / "default").write_text("", "utf-8")

    seen, branches = set(), 0
    for kw in (
        # the bar-click path: fzf dismissed
        dict(fzf_body="exit 130\n", socket_root=sock),
        # the bar-click path: a selection, tmux attaches
        dict(fzf_body="printf 'scratch9\\n'\n", socket_root=sock),
        # the "[+ new scratchpad]" path
        dict(fzf_body="printf '[+ new scratchpad]\\n'\n", socket_root=sock),
        # fzf failed to launch -> the hold branch
        dict(fzf_body="exit 127\n", socket_root=sock),
        # inside a scratch client -> the detach branch
        dict(fzf_body="exit 130\n", tmux_env="/sock,1,0", session="scratch9"),
        # inside a NON-scratch client
        dict(fzf_body="exit 130\n", tmux_env="/sock,1,0", session="work"),
    ):
        proc, _log, calls = _picker_sandbox(tmp_path, jail=True, **kw)
        assert "command not found" not in proc.stderr, (kw, proc.stderr)
        seen |= calls
        branches += 1
    assert branches == 6
    assert seen <= _PICKER_BINARIES, sorted(seen - _PICKER_BINARIES)
    # …and it does not SHRINK unnoticed: every pinned binary was really used.
    assert seen == _PICKER_BINARIES, (
        "the picker no longer invokes %r — the acknowledgement's pinned set is "
        "stale in the other direction" % sorted(_PICKER_BINARIES - seen))

    # The source half: no host-affecting binary appears outside comment prose.
    src = open(_PICKER, encoding="utf-8").read()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for hazard in ("home-manager", "nixos-rebuild", "systemctl", "i3-msg",
                   "nix-env", "pkill"):
        assert hazard not in code, (
            "scripts/tmux-scratch-picker.sh reaches %r in CODE, not prose — "
            "its ACKNOWLEDGED_UNSTUBBED entry covers a comment only" % hazard)
    # negative control: the stripper kept the code, and the word IS in the file
    assert "detach-client" in code and "fzf" in code
    assert "home-manager" in src, (
        "the acknowledgement in test_no_real_launchers.py names this file for a "
        "word it no longer contains — drop the entry")


def test_the_picker_FINDS_THE_SAME_SOCKET_THE_LEGEND_DOES(tmp_path,
                                                          monkeypatch):
    """🔴 THE PILL AND ITS CLICK MUST TALK TO ONE SERVER.

    The legend finds the tmux socket itself (`_socket_roots`/`find_socket`);
    the picker behind its left-click called a bare `tmux list-sessions` with no
    socket-finding at all, so the two could name different servers the moment
    `TMUX_TMPDIR` went missing — a click that does not do what the thing it was
    clicked on says. Observed through a FAKE tmux that records the environment
    it was handed."""
    root = tmp_path / "rt"
    (root / ("tmux-%d" % os.getuid())).mkdir(parents=True)
    (root / ("tmux-%d" % os.getuid()) / "default").write_text("", "utf-8")

    proc, logged, _c = _picker_sandbox(tmp_path, "exit 130\n",
                                       socket_root=root)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "TMUX_TMPDIR=%s" % root in logged, (logged, str(root))

    # …and the legend, given the SAME environment, picks the SAME root. Pinned
    # by running it, not by re-reading either file's source.
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(root))
    assert blk._socket_roots()[0] == str(root), blk._socket_roots()
    assert blk.find_socket()[0] == str(root)

    # Negative control: with NO socket anywhere, the picker must NOT invent a
    # root — it leaves the ambient environment alone.
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "tmux-env.txt").unlink()
    proc2, logged2, _c2 = _picker_sandbox(tmp_path, "exit 130\n",
                                          socket_root=empty)
    assert "TMUX_TMPDIR=%s" % empty not in logged2, logged2


def test_the_picker_HOLDS_when_FZF_FAILS_TO_LAUNCH(tmp_path):
    """🔴 AN EMPTY SELECTION HAS TWO CAUSES AND ONLY ONE IS DELIBERATE.

    `nix/graphical.nix` justified having no `read -n 1` hold in its click string
    by enumerating the picker's instant exits as "long-lived, or a DELIBERATE
    instant exit (the operator dismissed fzf)". An fzf that FAILS TO LAUNCH
    yields the same empty `$selected` — and the float terminal vanishes, which
    is the indistinguishable-from-nothing failure the hold exists for.
    Non-hypothetical: a `home-manager switch` blanks `~/.nix-profile` for ~1 s,
    killing bare-name invocations, and `fzf` is a bare name here."""
    proc, _log, _c = _picker_sandbox(tmp_path, "exit 127\n")
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "fzf exited 127" in proc.stderr, proc.stderr

    # CONTROL — the two DELIBERATE empties must still exit 0, or every
    # dismissal costs the operator a keypress.
    for rc in ("130", "1"):
        p, _l, _c = _picker_sandbox(tmp_path, "exit %s\n" % rc)
        assert p.returncode == 0, (rc, p.returncode, p.stderr)
        assert "could not run" not in p.stderr, (rc, p.stderr)

    # ⚠ THE HOLD ITSELF CANNOT BE OBSERVED FROM HERE: with stdin on /dev/null
    # bash suppresses `read -p`'s prompt and returns immediately, so the run
    # above proves the BRANCH was taken and not that the terminal was held.
    # Asserted structurally instead — `read -n 1` inside that branch's body —
    # with a comment-stripped source so prose cannot satisfy it.
    code = "\n".join(ln for ln in open(_PICKER, encoding="utf-8").read()
                     .splitlines() if not ln.lstrip().startswith("#"))
    arm = re.search(r'^\s*0\|1\|130\)\s*;;\s*\n\s*\*\)(?P<body>.*?)\n\s*esac',
                    code, re.S | re.M)
    assert arm, (
        "the picker no longer branches on fzf's EXIT CODE — an empty selection "
        "is then indistinguishable from an fzf that never ran:\n%s" % code)
    assert "read -n 1" in arm.group("body"), arm.group("body")
    assert "exit 1" in arm.group("body"), arm.group("body")


def test_the_block_is_text_only_and_carries_no_icon_glyph():
    """Deliberately no nerd-font codepoint: an unverified one renders as tofu.
    Assert on the RENDERED text (structural), not on the source prose."""
    for slots, sessions in ((_SLOTS, _SESSIONS), (_SLOTS, {}), (None, None)):
        text = blk.render(slots, sessions)["text"]
        assert all(ord(ch) < 0x2000 for ch in text), text
