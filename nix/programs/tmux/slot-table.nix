# THE NIX READER FOR THE CANONICAL SCRATCHPAD SLOT TABLE.
#
# 🔴 WHY THIS IS ITS OWN FILE AND NOT A `let` BINDING IN default.nix.
# The entry grammar has ONE home — the `# SLOT_ENTRY_RE:` marker line in
# scripts/tmux-scratch-slots.sh — and two readers: this one (which generates the
# `bind -n M-<key>` popup toggles) and scripts/i3status-scratchpads (the bar's
# colour legend). The guard that they had not drifted apart used to be a REGEX
# OVER default.nix's SOURCE TEXT, and it was walkable: it matched the current
# formatting rather than the grammar, so two mutants that put a string literal
# back — one of them carrying the original DISAGREEING grammar — survived the
# whole suite. A spelled guard cannot see a value.
#
# So the reader lives here, as a plain function of a path, with NO nixpkgs
# dependency (pure `builtins`, no `lib`, no channel, no network). That makes the
# grammar it actually USES an evaluable value: `nix-instantiate --eval` can ask
# for `.slotRe` and compare it to the string Python compiled, and can run this
# reader over a synthetic corpus and compare its verdicts line by line with
# Python's. See scripts/tests/test_scratchpads_block.py.
#
# Callers: nix/programs/tmux/default.nix (the only production caller).
{ path }:
let
  text = builtins.readFile path;
  # `lib.splitString "\n"` without lib: `builtins.split` returns the pieces
  # interleaved with match-group lists, so the strings ARE the lines.
  lines = builtins.filter builtins.isString (builtins.split "\n" text);

  # 🔴 TRAILING WHITESPACE ON THE MARKER LINE IS STRIPPED, and it has to be.
  # The Python side reads the marker with `(\S.*?)[ \t]*$`, which strips it; a
  # bare `(.*)` here does not, so ONE trailing space made nix compile a grammar
  # ending in `[ <TAB>]* ` — whose whole-line match then failed on every entry.
  # MEASURED before this was fixed: `declares=20 matched=0`, and the shortfall
  # message blamed the twenty perfectly correct colour fields while the legend
  # rendered all 20 slots. Fails closed, but misdiagnoses — in the mechanism
  # built to end disagreements.
  markerMatches = builtins.filter (m: m != null)
    (map (l: builtins.match "# SLOT_ENTRY_RE: (.*[^ \t])[ \t]*" l) lines);

  slotRe =
    if builtins.length markerMatches == 1
    then builtins.elemAt (builtins.head markerMatches) 0
    else throw ("${toString path} must carry EXACTLY ONE `# SLOT_ENTRY_RE:` "
      + "marker line — it is the only copy of the slot entry grammar, shared "
      + "with scripts/i3status-scratchpads. Found "
      + toString (builtins.length markerMatches) + ".");

  slotLines = builtins.filter (l: builtins.match slotRe l != null) lines;

  # What the file DECLARES: a quoted string at the start of a line. Deliberately
  # blind to the fields, so a slot whose colour was corrupted still counts here
  # — that is what makes the shortfall check in default.nix able to see it.
  #
  # 🔴 DELIBERATELY WIDER THAN `slotRe`, ON EVERY AXIS IT CAN BE. A candidate
  # pattern that narrows in lockstep with the grammar cannot detect a dropped
  # entry: both counts fall together and the floor stays quiet. That is exactly
  # what happened when both were spelled `[ ]*` and a TAB-indented entry
  # appeared — 19 of 20, no throw. So: `[[:space:]]` rather than the grammar's
  # space-or-tab, and a trailing `.*` so an entry with a trailing comment (which
  # bash DOES put in the array, measured) counts as declared even though the
  # grammar rejects it. Same yardstick as `_CANDIDATE_RE` in
  # scripts/i3status-scratchpads, which is wider on the same two axes.
  #
  # A commented-out line does not start with `"`, so it is not declared —
  # matching what the grammar and a bash `source` both do with it.
  candidateLines = builtins.filter
    (l: builtins.match "[[:space:]]*\"[^\"]*\".*" l != null) lines;

  # 🔴 THE GROUP-INDEX CONTRACT IS CHECKED, NOT ASSUMED. The marker carries the
  # grammar but not which capture group means what; this side reads
  # `elemAt m 4` for the name and the Python side reads `group(5)` for the same
  # field. A grammar edited to four groups would make one of them throw
  # "list index out of bounds" and the other `IndexError` — both honest, neither
  # named. So the arity is asserted here (and mirrored by `slot_pattern`'s
  # `rx.groups != 5` check on the Python side).
  #
  # Group 4 is the colour's hex digits (the length alternation), so `name` is
  # group 5 -> index 4.
  groupCount = 5;
  parse = l:
    let m = builtins.match slotRe l; in
    if builtins.length m != groupCount
    then throw ("the `# SLOT_ENTRY_RE:` grammar in ${toString path} has "
      + toString (builtins.length m) + " capture groups; both readers require "
      + toString groupCount
      + " (session, key, colour, the colour's hex digits, name). "
      + "scripts/i3status-scratchpads reads group 5 for the name.")
    else {
      sess = builtins.elemAt m 0;
      key = builtins.elemAt m 1;
      color = builtins.elemAt m 2;
      name = builtins.elemAt m 4;
    };
in
{
  inherit slotRe lines slotLines candidateLines groupCount;
  slots = map parse slotLines;
}
