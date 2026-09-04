"""🔴 A tmux-resurrect HOOK NAME IS SILENT WHEN IT IS WRONG.

`scripts/helpers.sh:execute_hook()` in tmux-resurrect does exactly this:

    hook=$(get_tmux_option "$hook_prefix$kind" "")
    if [ -n "$hook" ]; then eval "$hook $args"; fi

`get_tmux_option` falls back to `""` for an option nobody sets, and an empty
hook is a no-op. So an option whose `<kind>` half is not one resurrect actually
invokes is read by NOTHING: tmux accepts the `set -g`, the plugin never looks it
up, the config is valid, the switch succeeds, and the only symptom is that the
callback never runs. There is no error at any layer.

THE OUTAGE THIS FILE EXISTS FOR. `nix/programs/tmux/default.nix` set
`@resurrect-hook-post-save` — plausible, symmetric with the other four names,
and not a kind. `post-save` is not in the invoked set; `post-save-all` is.
Measured on the workbench 2026-09-04, with 50 live claude panes:
`~/.cache/tmux-session-restore.log` had NEVER been created (the callback writes
it unconditionally on every run), and `tmux-session-restore.service` had failed
on every boot since with "restore plan is 1461.9h old (limit 2.0h) — too stale,
skipping". The plan was frozen because nothing was refreshing it.

WHY THE GUARD IS SHAPED LIKE THIS. A guard that pinned the literal string
`@resurrect-hook-post-save-all` would be a guard on a WORD, walkable by anyone
who edits the line for any reason and re-introduces a name in the same shape.
The hazard is not that one spelling changed; it is that a hook option can be
set to a kind the plugin does not invoke. So this is a SEAM guard, and it
asserts a RELATIONSHIP in two layers:

  LAYER 1 (hermetic, always runs). Every `@resurrect-hook-<kind>` appearing in
  this repo's config/scripts must name a kind in the ledger below. The set of
  files is DERIVED by walking the tree, not hardcoded, so moving the setting to
  another file — or adding a second one — cannot escape the guard.

  LAYER 2 (the two-way pin). Re-derive the invoked-kind set from the resurrect
  plugin's OWN `execute_hook` call sites and assert it EQUALS the ledger. This
  is what stops the ledger rotting silently when the plugin is upgraded: a
  kind added, removed or renamed upstream fails here rather than turning some
  future hook into another silent no-op.

🔴 LAYER 2 MUST NEVER BECOME A PERMANENTLY-RED GATE, and it is genuinely
unmeasurable sometimes: the plugin lives at a `/nix/store` path that is
GC-able, and this host has already lost one (the running tmux server points at
`/nix/store/s7ij43…`, which no longer exists). A layer that reds when the store
is garbage-collected trains everyone to ignore it. So when the source cannot be
located — or is located but parses to zero kinds, which is a broken instrument,
never a pass — layer 2 reports UNMEASURED with a REASON, loudly, via a warning
that pytest surfaces in its summary even under `-q`, and does not fail. Layer 1
is unaffected and still runs.

🔴 NO `git ls-files` AND NO `nix` SUBPROCESS. The authoritative tier builds
`checks.pytests` from a tracked-file copy with no `.git` and cannot run nested
`nix`. This file only walks the filesystem and reads text.
"""

from __future__ import annotations

import os
import re
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

# --------------------------------------------------------------------------- #
# THE LEDGER.
#
# PROVENANCE — derived by reading tmux-resurrect's own source, not its README.
# `scripts/variables.sh:45` sets `hook_prefix="@resurrect-hook-"`, and
# `scripts/helpers.sh:139 execute_hook()` looks up `${hook_prefix}${kind}`. The
# complete set of `<kind>` values is every literal passed to `execute_hook`,
# and there are exactly five call sites (verified with GNU grep — not the
# gitignore-honouring wrapper — across the whole plugin tree, and cross-checked
# against a second store path of the same version):
#
#   scripts/save.sh:246     execute_hook "post-save-layout" "$resurrect_file_path"
#   scripts/save.sh:259     execute_hook "post-save-all"
#   scripts/restore.sh:369  execute_hook "pre-restore-all"
#   scripts/restore.sh:373  execute_hook "pre-restore-pane-processes"
#   scripts/restore.sh:382  execute_hook "post-restore-all"
#
# Read from tmuxplugin-resurrect-unstable-2022-05-01. The VALUE is the script
# that invokes the kind, which is what makes "does this hook fire at save time"
# a question the hermetic layer can answer without the plugin present.
# --------------------------------------------------------------------------- #
HOOK_PREFIX = "@resurrect-hook-"

RESURRECT_HOOK_KINDS: dict[str, str] = {
    "post-save-layout": "save.sh",
    "post-save-all": "save.sh",
    "pre-restore-all": "restore.sh",
    "pre-restore-pane-processes": "restore.sh",
    "post-restore-all": "restore.sh",
}

SAVE_TIME_KINDS = {k for k, v in RESURRECT_HOOK_KINDS.items() if v == "save.sh"}

# --------------------------------------------------------------------------- #
# FILE DISCOVERY
# --------------------------------------------------------------------------- #

# Where a tmux option can plausibly be set in this repo: nix `extraConfig`
# strings, tmux config files, and shell that writes tmux config. Comments count
# — a comment is a claim, and the comment atop `scripts/tmux-post-save.sh` named
# the broken option for as long as the option itself was broken.
SCAN_SUFFIXES = {".nix", ".sh", ".bash", ".zsh", ".conf", ".tmux", ".py"}

# 🔴 `.claude/worktrees` is NOT optional. Agent worktrees nest INSIDE the base
# clone, so a tree-walk from the dev host would otherwise scan a sibling
# agent's in-progress branch and red this gate on work that is not ours. The
# rest are ordinary build/cache noise.
SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".direnv",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
}
SKIP_REL_PREFIXES = (
    ".claude/worktrees",
    "result",
)

# Prose may legitimately quote the historical broken name — this docstring does,
# and so will the PR body and any handoff written about it. Only executable
# config is in scope.
SKIP_REL_SUFFIXES = (".md",)

# 🔴 This file itself is excluded BY PATH, not by pattern: it necessarily
# contains the broken option name in the prose above, and a guard that fails on
# its own explanation is a guard nobody keeps.
SKIP_PATHS = {SELF}

_OPTION_RE = re.compile(re.escape(HOOK_PREFIX) + r"([A-Za-z0-9_-]+)")
# A SET site: `set -g @resurrect-hook-<kind> '<value>'` (the `-g` and the quotes
# are both optional in tmux). The value is captured so the save-time
# relationship below can be checked.
_SET_RE = re.compile(
    r"""set(?:-option)?\s+(?:-[A-Za-z]+\s+)*"""
    + re.escape(HOOK_PREFIX)
    + r"""([A-Za-z0-9_-]+)\s+(?P<q>['"]?)(?P<value>[^'"\n]*)(?P=q)"""
)


def _candidate_files() -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        d = Path(dirpath)
        rel_dir = d.relative_to(REPO).as_posix()
        if rel_dir != "." and any(
            rel_dir == p or rel_dir.startswith(p + "/") for p in SKIP_REL_PREFIXES
        ):
            dirnames[:] = []
            continue
        for name in filenames:
            f = d / name
            rel = f.relative_to(REPO).as_posix()
            if any(rel == p or rel.startswith(p + "/") for p in SKIP_REL_PREFIXES):
                continue
            if rel.endswith(SKIP_REL_SUFFIXES):
                continue
            if f.resolve() in SKIP_PATHS:
                continue
            if f.suffix not in SCAN_SUFFIXES and name not in {".tmux.conf", ".zshrc"}:
                continue
            out.append(f)
    return out


def _mentions() -> list[tuple[Path, int, str]]:
    """(file, lineno, kind) for every `@resurrect-hook-<kind>` occurrence."""
    found: list[tuple[Path, int, str]] = []
    for f in _candidate_files():
        try:
            text = f.read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue
        if HOOK_PREFIX not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _OPTION_RE.finditer(line):
                found.append((f, lineno, m.group(1)))
    return found


def _set_sites() -> list[tuple[Path, int, str, str]]:
    """(file, lineno, kind, value) for every `set … @resurrect-hook-<kind> <v>`."""
    found: list[tuple[Path, int, str, str]] = []
    for f in _candidate_files():
        try:
            text = f.read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue
        if HOOK_PREFIX not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _SET_RE.search(line)
            if m:
                found.append((f, lineno, m.group(1), m.group("value").strip()))
    return found


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


# --------------------------------------------------------------------------- #
# LAYER 1 — hermetic. Always runs.
# --------------------------------------------------------------------------- #


def test_the_scan_actually_observes_a_hook_option() -> None:
    """POSITIVE CONTROL for layer 1.

    Every assertion below is over a SET that the walk produced. A walk that
    matched nothing — a broken regex, an over-eager exclusion, a suffix list
    that stopped covering `.nix` — would satisfy all of them vacuously and read
    as a pass. A reassuring zero here is indistinguishable from a guard wired to
    nothing, so the zero is the failure.
    """
    mentions = _mentions()
    sets = _set_sites()
    assert mentions, (
        f"RESURRECT-HOOK SCAN OBSERVED NOTHING: no {HOOK_PREFIX}* occurrence "
        f"found anywhere under {REPO}. This guard is measuring nothing — fix "
        f"the walk (SCAN_SUFFIXES / SKIP_* / _OPTION_RE), do not delete the "
        f"assertion. Files considered: {len(_candidate_files())}."
    )
    assert sets, (
        f"RESURRECT-HOOK SCAN FOUND NO SET SITE: {len(mentions)} mention(s) of "
        f"{HOOK_PREFIX}* exist but _SET_RE matched none of them. Either the "
        f"repo genuinely stopped setting a resurrect hook (then delete this "
        f"file and its ledger), or _SET_RE has drifted from how tmux options "
        f"are written here. Mentions: "
        + ", ".join(f"{_rel(f)}:{n}" for f, n, _ in mentions)
    )


def test_every_resurrect_hook_option_names_a_kind_the_plugin_invokes() -> None:
    """LAYER 1. The membership half of the seam.

    Not a check that one known-good string is present — a check that NO name
    outside the invoked set appears. That is the difference between a guard on
    a word and a guard on the relationship: it fires on `post-save`,
    `post-save-hook`, `after-save`, or any other plausible-but-dead spelling,
    including ones nobody has thought of yet.
    """
    bad = [
        (f, n, kind)
        for f, n, kind in _mentions()
        if kind not in RESURRECT_HOOK_KINDS
    ]
    assert not bad, (
        "DEAD RESURRECT HOOK OPTION — tmux-resurrect never invokes this kind, "
        "so the option is read by nothing and the callback silently never "
        "runs:\n"
        + "\n".join(
            f"  {_rel(f)}:{n}  {HOOK_PREFIX}{kind}" for f, n, kind in bad
        )
        + "\nInvoked kinds are exactly: "
        + ", ".join(sorted(RESURRECT_HOOK_KINDS))
        + f"\n(so e.g. {HOOK_PREFIX}post-save-all, not {HOOK_PREFIX}post-save.)"
    )


def test_the_post_save_callback_is_registered_on_a_save_time_kind() -> None:
    """LAYER 1. The INTENT half of the seam.

    Membership alone is walkable: `pre-restore-all` is a real kind, so
    re-pointing the save callback at it would pass the test above while
    restoring exactly the outage — the restore plan would stop being refreshed
    and the boot-time restore would go stale again. This asserts the
    relationship that matters: whatever runs `tmux-post-save` must be hung on a
    kind the plugin invokes from `save.sh`.

    The save/restore split comes from the ledger, so the hermetic tier can
    answer it with no plugin source present; layer 2 pins that split against
    the plugin's real call sites.
    """
    save_callbacks = [
        (f, n, kind, value)
        for f, n, kind, value in _set_sites()
        if "tmux-post-save" in value
    ]
    assert save_callbacks, (
        "NO SAVE CALLBACK REGISTERED: no `set … " + HOOK_PREFIX + "<kind>` in "
        "this repo has a value mentioning `tmux-post-save`. Either the "
        "callback was removed (then remove this test and "
        "scripts/tmux-post-save.sh together) or the registration moved "
        "somewhere this walk cannot see."
    )
    wrong = [
        (f, n, kind, value)
        for f, n, kind, value in save_callbacks
        if kind not in SAVE_TIME_KINDS
    ]
    assert not wrong, (
        "SAVE CALLBACK HUNG ON A NON-SAVE HOOK — it will fire at the wrong "
        "time (or, for a restore-only kind, never during a continuum save "
        "cycle), which is the same outage in a different shape:\n"
        + "\n".join(
            f"  {_rel(f)}:{n}  {HOOK_PREFIX}{kind} -> {value}"
            for f, n, kind, value in wrong
        )
        + "\nSave-time kinds are: "
        + ", ".join(sorted(SAVE_TIME_KINDS))
    )


# --------------------------------------------------------------------------- #
# LAYER 2 — the two-way pin against the plugin's own source.
# --------------------------------------------------------------------------- #

_EXECUTE_HOOK_RE = re.compile(r'execute_hook\s+"([A-Za-z0-9_-]+)"')
_HOOK_PREFIX_RE = re.compile(r'^\s*hook_prefix="([^"]*)"', re.MULTILINE)


class ResurrectSourceUnmeasured(UserWarning):
    """Layer 2 could not measure. Carries a machine-readable reason token."""


def _unmeasured(reason: str, detail: str) -> None:
    """Report loudly and return. NOT a skip and NOT a failure.

    A `pytest.skip` here would be wrong twice over: `scripts/run-tests.sh`
    pins the expected-skip SET exactly, and its only conditional form is
    `unset:VAR` — which cannot express "the store path was garbage-collected",
    so the pin would be red on whichever host disagreed. And a bare skip prints
    nothing a reader can act on. A warning is surfaced in pytest's summary even
    under `-q`, and the reason token says which of the several distinct
    not-measured states this is.
    """
    msg = (
        f"LAYER 2 UNMEASURED [{reason}]: the tmux-resurrect ledger was NOT "
        f"cross-checked against the plugin source this run. {detail} "
        f"Layer 1 still ran; the ledger is unverified against upstream."
    )
    print("\n" + msg)
    warnings.warn(ResurrectSourceUnmeasured(msg), stacklevel=2)


def _locate_plugin_source() -> Path | None:
    """Find a resurrect checkout that actually resolves.

    Discovered, never hardcoded: the store hash changes with every nixpkgs bump
    and the path is GC-able. Two routes, cheapest first.
    """
    candidates: list[Path] = []
    store = Path("/nix/store")
    if store.is_dir():
        candidates.extend(
            sorted(store.glob("*tmuxplugin-resurrect*/share/tmux-plugins/resurrect"))
        )
    # Second route: whatever the GENERATED tmux config actually sources. This is
    # the path the live tmux server would use, so it is the more honest answer
    # when it resolves — but it points into the store too, and on this host it
    # has already been observed dangling, which is why it is a fallback rather
    # than the only route.
    conf = Path.home() / ".config" / "tmux" / "tmux.conf"
    try:
        for line in conf.read_text(encoding="utf8").splitlines():
            if "resurrect" in line and "run-shell" in line:
                for tok in re.findall(r"[^\s'\"]+", line):
                    if "resurrect" in tok and tok.startswith("/"):
                        p = Path(tok)
                        # tok is usually …/resurrect/resurrect.tmux
                        candidates.append(p.parent if p.suffix == ".tmux" else p)
    except OSError:
        pass
    for c in candidates:
        if (c / "scripts" / "save.sh").is_file():
            return c
    return None


def test_ledger_matches_the_plugin_sources_own_execute_hook_call_sites() -> None:
    """LAYER 2. Re-derive, then compare — in BOTH directions.

    An upstream kind the ledger is missing means a future hook option would be
    rejected by layer 1 even though it works. A ledger entry upstream no longer
    invokes means layer 1 would wave through a dead option. Both are the same
    silence; equality catches both.
    """
    src = _locate_plugin_source()
    if src is None:
        _unmeasured(
            "source-not-located",
            "No `/nix/store/*tmuxplugin-resurrect*/share/tmux-plugins/resurrect` "
            "resolved (the path is garbage-collectable, and a dangling one has "
            "been observed on this host) and ~/.config/tmux/tmux.conf named no "
            "resolving resurrect path either.",
        )
        return

    scripts_dir = src / "scripts"
    derived: dict[str, str] = {}
    for sh in sorted(scripts_dir.glob("*.sh")):
        try:
            text = sh.read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind in _EXECUTE_HOOK_RE.findall(text):
            derived[kind] = sh.name

    if not derived:
        # A located source that yields zero kinds is a BROKEN INSTRUMENT, never
        # a clean run. Upstream may have restructured `execute_hook` — which is
        # exactly the kind of change this layer exists to notice — so it must
        # not be reported as agreement.
        _unmeasured(
            "parsed-zero-kinds",
            f"Located {src} but `execute_hook \"<kind>\"` matched nothing in "
            f"{len(list(scripts_dir.glob('*.sh')))} script(s). The plugin may "
            f"have restructured its hook dispatch; re-derive the ledger by hand.",
        )
        return

    # The prefix is half the option name. If upstream renames it, every option
    # this repo sets goes dead at once — the same silence, one level up.
    prefix_seen = None
    vars_sh = scripts_dir / "variables.sh"
    if vars_sh.is_file():
        m = _HOOK_PREFIX_RE.search(vars_sh.read_text(encoding="utf8"))
        if m:
            prefix_seen = m.group(1)
    if prefix_seen is None:
        _unmeasured(
            "prefix-not-parsed",
            f"Located {src} and parsed {len(derived)} kind(s), but could not "
            f"read `hook_prefix=` out of scripts/variables.sh, so the option "
            f"PREFIX half of the seam is unchecked.",
        )
    else:
        assert prefix_seen == HOOK_PREFIX, (
            f"RESURRECT HOOK PREFIX CHANGED UPSTREAM: {src} sets "
            f"hook_prefix={prefix_seen!r}, this repo's options are spelled "
            f"{HOOK_PREFIX!r}. Every hook option set here is currently a "
            f"silent no-op. Update HOOK_PREFIX and every `set -g` site."
        )

    assert derived == RESURRECT_HOOK_KINDS, (
        "RESURRECT HOOK LEDGER IS OUT OF DATE — re-derived from "
        f"{src}:\n"
        f"  upstream invokes : {derived}\n"
        f"  ledger says      : {RESURRECT_HOOK_KINDS}\n"
        f"  only upstream    : {sorted(set(derived) - set(RESURRECT_HOOK_KINDS))}\n"
        f"  only ledger      : {sorted(set(RESURRECT_HOOK_KINDS) - set(derived))}\n"
        "Update RESURRECT_HOOK_KINDS (and its PROVENANCE comment) from the "
        "call sites above, then re-check every option this repo sets."
    )
