"""🔴 TMUX-CONTINUUM'S 15-MINUTE AUTOSAVE IS A `status-right` INTERPOLATION.

`continuum.tmux:main()` calls `add_resurrect_save_interpolation`, which PREPENDS
`#(<plugin>/scripts/continuum_save.sh)` to the `status-right` option. Every
status refresh then runs that script, which checks whether the save interval has
elapsed and, if so, invokes tmux-resurrect's save. There is no timer, no hook and
no daemon anywhere in the plugin — **that interpolation IS the timer**. Remove it
and continuum still loads, still reports "on", still accepts
`@continuum-save-interval`, and never saves again. Nothing errors.

THE OUTAGE THIS FILE EXISTS FOR. home-manager emits `run-shell <plugin>.tmux` for
every plugin FIRST and `programs.tmux.extraConfig` AFTER, and this repo's
`extraConfig` begins with the whole of the repo-root `.tmux.conf`, which carries a
plain `set -g status-right '…'`. A plain `set` REPLACES the option, so continuum's
prepend was discarded ~200 lines later, every time the config loaded.

MEASURED on the workbench 2026-09-04, on a server up since 2026-08-05:
`status-right` (and `status-left`) held ZERO occurrences of `continuum_save`, and
`@continuum-save-last-timestamp` was 1785949443 — exactly `#{start_time}` + 1s.
That timestamp is the DISCRIMINATING evidence, not a coincidence: its only two
writers in the plugin are `continuum_save.sh` (which would also have produced a
save) and `delay_saving_environment_on_first_plugin_load`, which sits inside the
SAME `if ! another_tmux_server_running` block as the interpolation. So its
presence proves the block RAN, which refutes the rival "continuum short-circuited
because another server was up" explanation and leaves only one mechanism: the
interpolation was added and then clobbered. Continuum had never saved in 30 days.

WHY THE GUARD IS SHAPED LIKE THIS — IT SIMULATES, IT DOES NOT GREP.
A guard that grepped the generated config for `continuum_save` would have PASSED
throughout the outage as soon as anyone put the string anywhere, and would pass
today on a config where the append sits BEFORE the plain `set` and is therefore
clobbered. That is precisely the bug. Presence is not the invariant; SURVIVAL is.

So this replays tmux's own option semantics over the config devrc generates, in
order — a plain `set … status-right` REPLACES the accumulated value, a `set -a …`
APPENDS to it — and asserts the interpolation is in the FINAL value. Ordering is
not a separate assertion bolted on; it is intrinsic to the simulation, so the two
mutants (delete the append / move it above the plain `set`) fail with two
DIFFERENT messages, and neither can be satisfied by a reworded line.

HOW THE GENERATED CONFIG IS RECONSTRUCTED, AND WHY IT CANNOT HIDE A STATEMENT.
The hermetic tier builds from a `cp -r` store copy with no `.git` and cannot run
nested `nix`, so evaluating the module is not available. Instead the
`extraConfig` expression is expanded textually: `builtins.readFile <path>` is
replaced by the file's real bytes, and every `let`-bound identifier — whether a
bare term or a `${…}` inside a string — by that binding's RAW SOURCE, repeatedly.
The expansion is deliberately CONSERVATIVE: it only ever ADDS text and never
reorders it, so a status-right statement can be surfaced by it but never
concealed. Comment lines are skipped, in both the nix and the tmux sense (`#`
starts a comment in each), which is what keeps the prose above from matching.

🔴 NO `git ls-files` AND NO `nix` SUBPROCESS — same constraint as the sibling
guard `test_tmux_resurrect_hook_names.py`. This file only reads text.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMUX_MODULE = REPO / "nix" / "programs" / "tmux" / "default.nix"
TMUX_CONF = REPO / ".tmux.conf"

# The interpolation continuum's autosave rides on. Matched as a `#(…)` shell
# interpolation containing the script name, so a bare mention of
# `continuum_save.sh` in a comment or a variable name is not mistaken for one.
INTERPOLATION_RE = re.compile(r"#\([^()]*continuum_save\.sh[^()]*\)")

# `set -g status-right '…'` / `set-option -ag status-right …`. `status-right`
# must be followed by whitespace, which is what keeps `status-right-length` —
# a different option, set on the very next line of .tmux.conf — out of the set.
_STATUS_RIGHT_RE = re.compile(
    r"^\s*set(?:-option)?\s+((?:-[A-Za-z]+\s+)*)status-right\s+(?P<value>.*?)\s*$"
)

MAX_EXPANSION_ROUNDS = 12


# --------------------------------------------------------------------------- #
# NIX EXPRESSION EXPANSION
# --------------------------------------------------------------------------- #


def _skip_to_expression_end(text: str, i: int) -> int:
    """Index just past the `;` that terminates the expression starting at `i`.

    A plain `text.index(";", i)` is wrong here: the module's strings contain
    semicolons (the scratchpad `display-popup … ;`-free today, but `mkBind`
    embeds `||` and tmux command lists, and a future one may not be so kind), and
    nix comments can contain anything at all. So this walks the real string and
    comment states rather than guessing.
    """
    n = len(text)
    while i < n:
        c = text[i]
        if c == "#":  # nix line comment
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith("''", i):  # indented string
            j = i + 2
            while j < n:
                if text.startswith("'''", j) or text.startswith("''$", j):
                    j += 3
                elif text.startswith("''\\", j):
                    j += 4
                elif text.startswith("''", j):
                    j += 2
                    break
                else:
                    j += 1
            i = j
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                elif text[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            i = j
        elif c == ";":
            return i
        else:
            i += 1
    return n


def _let_bindings(text: str) -> dict[str, str]:
    """`name -> raw source of its RHS` for every top-level `let` binding."""
    out: dict[str, str] = {}
    for m in re.finditer(r"^\s{0,4}([A-Za-z_][A-Za-z0-9_'-]*)\s*=\s*", text, re.M):
        name = m.group(1)
        end = _skip_to_expression_end(text, m.end())
        out.setdefault(name, text[m.end() : end])
    return out


def _extra_config_expr(text: str) -> str:
    """Raw source of the `extraConfig = …;` right-hand side."""
    m = re.search(r"^\s*extraConfig\s*=\s*", text, re.M)
    assert m, (
        f"CANNOT LOCATE `extraConfig =` IN {TMUX_MODULE.relative_to(REPO)}. This "
        f"guard reconstructs the generated tmux config from that expression; if "
        f"the attribute was renamed or restructured, update the reconstruction — "
        f"do not delete the assertion."
    )
    return text[m.end() : _skip_to_expression_end(text, m.end())]


def _expand(expr: str, bindings: dict[str, str]) -> str:
    """Textually expand readFile calls and let-bound identifiers.

    CONSERVATIVE BY CONSTRUCTION: every rule replaces a reference with MORE text
    at the same position. Nothing is ever removed or moved, so the expansion can
    reveal a `set … status-right` statement but can never hide one.
    """
    ident = "|".join(sorted(map(re.escape, bindings), key=len, reverse=True))
    interp_re = re.compile(r"\$\{(" + ident + r")\}") if ident else None
    bare_re = re.compile(r"(?<![\w.'-])(" + ident + r")(?![\w'-])") if ident else None
    read_re = re.compile(r"builtins\.readFile\s+([^\s;+)]+)")

    for _ in range(MAX_EXPANSION_ROUNDS):
        before = expr
        if interp_re is not None:
            expr = interp_re.sub(lambda m: bindings[m.group(1)], expr)
        if bare_re is not None:
            expr = bare_re.sub(lambda m: bindings[m.group(1)], expr)

        def _read(m: re.Match[str]) -> str:
            p = (TMUX_MODULE.parent / m.group(1)).resolve()
            try:
                return p.read_text(encoding="utf8")
            except OSError:
                return m.group(0)

        expr = read_re.sub(_read, expr)
        if expr == before:
            break
    return expr


def generated_config() -> str:
    text = TMUX_MODULE.read_text(encoding="utf8")
    return _expand(_extra_config_expr(text), _let_bindings(text))


# --------------------------------------------------------------------------- #
# THE SIMULATION
# --------------------------------------------------------------------------- #


class Stmt:
    __slots__ = ("lineno", "append", "value", "line")

    def __init__(self, lineno: int, append: bool, value: str, line: str) -> None:
        self.lineno, self.append, self.value, self.line = lineno, append, value, line

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{'APPEND' if self.append else 'PLAIN '} {self.line.strip()!r}>"


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def status_right_statements(config: str) -> list[Stmt]:
    out: list[Stmt] = []
    for lineno, line in enumerate(config.splitlines(), 1):
        # `#` opens a comment in nix AND in tmux config, so one rule covers both
        # the raw-source fragments and the real tmux lines.
        if line.lstrip().startswith("#"):
            continue
        m = _STATUS_RIGHT_RE.match(line)
        if not m:
            continue
        flags = m.group(1)
        append = any("a" in tok for tok in flags.split())
        out.append(Stmt(lineno, append, _unquote(m.group("value")), line))
    return out


def simulate(statements: list[Stmt]) -> str:
    """Replay tmux's `set` / `set -a` semantics for one option."""
    value = ""
    for s in statements:
        value = value + s.value if s.append else s.value
    return value


# --------------------------------------------------------------------------- #
# LAYER 1 — hermetic. Always runs.
# --------------------------------------------------------------------------- #


def test_the_reconstruction_actually_observes_status_right_statements() -> None:
    """POSITIVE CONTROL.

    Every assertion below is a claim about a list this reconstruction produced.
    An empty list — a renamed attribute, a readFile path that stopped resolving,
    a regex that drifted — would make the simulation return `""` and could be
    read either as "clobbered" or, with a sloppier assertion, as a pass. A
    reassuring zero here is the failure, not the all-clear, so it is asserted
    before anything is concluded from the list.

    The PLAIN statement is asserted separately from the total: the whole hazard
    is a plain `set` landing after the append, so a reconstruction that has
    stopped seeing the plain `set -g status-right` in `.tmux.conf` cannot
    observe the hazard at all, even though it would still see the append and
    report a confident green.
    """
    statements = status_right_statements(generated_config())
    assert statements, (
        "STATUS-RIGHT RECONSTRUCTION OBSERVED NOTHING: expanding the "
        f"`extraConfig` expression in {TMUX_MODULE.relative_to(REPO)} produced no "
        "`set … status-right …` statement at all. This guard is measuring "
        "nothing — fix the expansion (_extra_config_expr / _expand / "
        "_STATUS_RIGHT_RE), do not delete the assertion."
    )
    plain = [s for s in statements if not s.append]
    assert plain, (
        "STATUS-RIGHT RECONSTRUCTION SAW NO PLAIN `set`: only append forms were "
        f"found, but {TMUX_CONF.name} carries a plain `set -g status-right`. The "
        "clobber this guard exists to catch is invisible to a reconstruction "
        "that cannot see the clobbering statement. Statements found: "
        + repr(statements)
    )


def test_the_continuum_save_interpolation_survives_to_the_final_status_right() -> None:
    """THE INVARIANT. Presence AND survival, as one simulated value.

    The two failure modes get two different messages on purpose: the first says
    the driver is missing, the second says it is present but dead. During the
    outage the second was the true state, and every "is continuum configured?"
    check anyone ran answered yes.
    """
    statements = status_right_statements(generated_config())
    assert statements, "reconstruction observed nothing — see the positive control"

    carriers = [s for s in statements if INTERPOLATION_RE.search(s.value)]
    assert carriers, (
        "NO CONTINUUM SAVE INTERPOLATION IN status-right — continuum's autosave "
        "is a status-line interpolation and nothing else drives it, so tmux "
        "sessions are NEVER saved. The plugin still loads and reports itself "
        "enabled; the only symptom is that `@continuum-save-last-timestamp` "
        "never moves.\n"
        "Restore an append AFTER the last plain `set -g status-right` in the "
        "config nix/programs/tmux/default.nix generates:\n"
        "    set -ag status-right '#(${pkgs.tmuxPlugins.continuum}"
        "/share/tmux-plugins/continuum/scripts/continuum_save.sh)'\n"
        "status-right statements found: " + repr(statements)
    )

    final = simulate(statements)
    assert INTERPOLATION_RE.search(final), (
        "CONTINUUM SAVE INTERPOLATION IS CLOBBERED — it IS set "
        f"(line {carriers[-1].lineno} of the generated config) but a later plain "
        "`set … status-right` REPLACES the option, so it never reaches a running "
        "tmux. This is the exact 2026-09-04 outage: continuum had not saved in 30 "
        "days while looking perfectly configured.\n"
        "  last plain set : "
        + repr([s for s in statements if not s.append][-1].line.strip())
        + "\n  interpolation  : "
        + repr(carriers[-1].line.strip())
        + "\n  simulated final status-right: "
        + repr(final)
        + "\nMove the append AFTER every plain `set … status-right`. In "
        "nix/programs/tmux/default.nix that means appending it to `extraConfig` "
        "after the readFile'd .tmux.conf, not before."
    )


def test_the_interpolation_is_an_append_not_a_replacement() -> None:
    """IDEMPOTENCE ACROSS RELOADS.

    Every `source-file` re-runs the whole config: the plain `set` resets the
    value, then the append adds the interpolation once. Spelling it as a plain
    `set` instead would work on the first load and then RESTATE the bar — the
    interpolation would be the whole of status-right and the clock, host and
    session pills would vanish. Verified on a throwaway `-L` socket: one
    occurrence after the initial load, still exactly one after two reloads.
    """
    statements = status_right_statements(generated_config())
    carriers = [s for s in statements if INTERPOLATION_RE.search(s.value)]
    assert carriers, "no interpolation — see the survival test for the playbook"
    plain_carriers = [s for s in carriers if not s.append]
    assert not plain_carriers, (
        "CONTINUUM SAVE INTERPOLATION IS SET WITH A PLAIN `set`, NOT AN APPEND. "
        "A plain `set` REPLACES status-right, so this discards the clock, host "
        "and every other segment the bar is built from:\n"
        + "\n".join(f"  {s.line.strip()}" for s in plain_carriers)
        + "\nUse the append form (`set -ag status-right …`), which composes with "
        "whatever .tmux.conf set and stays a single occurrence across reloads."
    )


def test_the_continuum_plugin_is_referenced_through_nix_not_a_store_hash() -> None:
    """🔴 A LITERAL /nix/store PATH IS GARBAGE-COLLECTABLE.

    Not style. Referencing the plugin through `${pkgs.tmuxPlugins.continuum}`
    makes it a real dependency of the home-manager generation, so it is GC-rooted
    for as long as that generation lives. A pasted hash is not, and this repo has
    already lost one: the tmux server on the workbench still points at a
    tmux-resurrect store path that no longer exists — the same class of failure
    one directory over, which is why the sibling guard has to treat its plugin
    source as possibly-absent.
    """
    offenders: list[str] = []
    for f in (TMUX_MODULE, TMUX_CONF):
        for lineno, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if "/nix/store/" in line:
                offenders.append(f"  {f.relative_to(REPO)}:{lineno}  {line.strip()}")
    assert not offenders, (
        "HARDCODED /nix/store PATH IN THE TMUX CONFIG — the path is "
        "garbage-collectable and is NOT a dependency of the generation, so it "
        "can vanish under a running tmux and take the feature with it, "
        "silently:\n"
        + "\n".join(offenders)
        + "\nReference the package instead, e.g. "
        "${pkgs.tmuxPlugins.continuum}/share/tmux-plugins/continuum/…"
    )


# --------------------------------------------------------------------------- #
# LAYER 2 — the pin against the plugin's own source.
# --------------------------------------------------------------------------- #


class ContinuumSourceUnmeasured(UserWarning):
    """Layer 2 could not measure. Carries a machine-readable reason token."""


def _unmeasured(reason: str, detail: str) -> None:
    """Report loudly and return. NOT a skip and NOT a failure.

    Same reasoning as the sibling resurrect guard: `scripts/run-tests.sh` pins
    the expected-skip SET exactly and its only conditional form is `unset:VAR`,
    which cannot express "the store path was garbage-collected", so a skip would
    red whichever host disagreed. A warning is surfaced in pytest's summary even
    under `-q`, and the token says WHICH not-measured state this is.
    """
    msg = (
        f"LAYER 2 UNMEASURED [{reason}]: the continuum autosave premise was NOT "
        f"cross-checked against the plugin source this run. {detail} Layer 1 "
        f"still ran; the premise is unverified against upstream."
    )
    print("\n" + msg)
    warnings.warn(ContinuumSourceUnmeasured(msg), stacklevel=2)


def _locate_plugin_source() -> Path | None:
    """Find a continuum checkout that actually resolves.

    Discovered, never hardcoded — the store hash moves with every nixpkgs bump
    and the path is GC-able, which is the whole point of the layer-1 guard above.
    """
    store = Path("/nix/store")
    if not store.is_dir():
        return None
    for c in sorted(store.glob("*tmuxplugin-continuum*/share/tmux-plugins/continuum")):
        if (c / "continuum.tmux").is_file():
            return c
    return None


def test_upstream_still_drives_autosave_through_the_status_right_interpolation() -> None:
    """LAYER 2. The PREMISE, not the spelling.

    Layer 1 asserts an interpolation survives into `status-right`. That is only
    worth asserting while continuum's autosave is actually driven that way. If
    upstream moves autosave to a hook or a daemon, layer 1 keeps passing while
    guarding a vestige — so re-derive the premise from the plugin's own source:
    it must still prepend a `continuum_save.sh` interpolation INTO `status-right`,
    and the script must exist at the path this repo's config names.
    """
    src = _locate_plugin_source()
    if src is None:
        _unmeasured(
            "source-not-located",
            "No `/nix/store/*tmuxplugin-continuum*/share/tmux-plugins/continuum` "
            "with a continuum.tmux resolved (the path is garbage-collectable, and "
            "a dangling plugin path has already been observed on this host).",
        )
        return

    save_script = src / "scripts" / "continuum_save.sh"
    assert save_script.is_file(), (
        f"CONTINUUM PLUGIN LAYOUT CHANGED: {src} has no scripts/continuum_save.sh, "
        f"but nix/programs/tmux/default.nix builds its interpolation from exactly "
        f"that relative path. The config would point at a file that does not "
        f"exist, and tmux reports nothing when a `#()` command fails. Re-derive "
        f"the path from the package's real layout."
    )

    entry = (src / "continuum.tmux").read_text(encoding="utf8")
    m = re.search(
        r"add_resurrect_save_interpolation\(\)\s*\{(.*?)\n\}", entry, re.S
    )
    if m is None:
        _unmeasured(
            "premise-not-parsed",
            f"Located {src} but could not find an "
            f"`add_resurrect_save_interpolation()` function body in "
            f"continuum.tmux. Upstream may have restructured autosave — which is "
            f"exactly what this layer exists to notice. Re-read the plugin before "
            f"trusting layer 1's premise.",
        )
        return

    body = m.group(1)
    assert "status-right" in body, (
        f"CONTINUUM NO LONGER DRIVES AUTOSAVE VIA status-right: "
        f"{src}/continuum.tmux's add_resurrect_save_interpolation() does not "
        f"mention `status-right` any more. Layer 1 is now guarding a vestige — "
        f"re-derive how autosave is triggered and repoint both the config and "
        f"this guard.\nfunction body:\n{body}"
    )
    assert "continuum_save.sh" in entry, (
        f"CONTINUUM'S SAVE SCRIPT IS NO LONGER NAMED continuum_save.sh in "
        f"{src}/continuum.tmux. The interpolation this repo sets by hand would "
        f"name a script upstream has stopped treating as the autosave driver."
    )
