"""🔴 git must carry SSH keepalives, or a long pre-push gate kills the push.

WHY THIS FILE EXISTS
--------------------
MEASURED 2026-08-26 against the real github.com, twice independently (#782).

  * github.com closes an IDLE `git-receive-pack` session after ~360 s. Two runs,
    both **361 s**; the clean one also returned `rc=255` with
    `Connection to github.com closed by remote host.` on stderr.
  * git opens AND negotiates the connection BEFORE running `pre-push` — measured
    with a `GIT_SSH_COMMAND` stamp, not inferred from interleaved output:
    `ssh-launch 04:12:04Z` then `hook START 04:12:05Z`. So the connection idles
    for the hook's entire runtime.
  * `githooks/tests-on-push.sh` is precisely such a hook. Paired push arms,
    identical 420 s hook, one variable:

        no keepalive           -> push rc=141 (SIGPIPE), branch ABSENT
        ServerAliveInterval=30 -> push rc=0,             branch CREATED

    An idle session with the keepalive was still alive at 1367 s (3.8x).

🔴 THE FAILURE LOOKS LIKE SUCCESS. The hook prints `✅ devrc test suite passed.`
AFTER the connection is already dead, and a wrapper's trailing command swallows
the 141 — #782 records `exit 0` reported twice while the real status was 141 and
the branch was never created. Only `git ls-remote` distinguishes them.

🔴 WHY THE SETTING LIVES IN `nix/programs/git`, NOT IN `githooks/install.sh`.
The first version of this fix put it in the installer, on the reasoning that the
installer already writes global git config. **That reasoning was false on the
machines devrc targets, and an audit caught it.** On a home-manager host
`git config --global` resolves to `~/.config/git/config`, which is a symlink
into the READ-ONLY nix store — the file `nix/programs/git/default.nix`
generates. The installer dies with
`could not lock config file ... Read-only file system` (rc=255) on its
PRE-EXISTING `core.hooksPath` line, before reaching anything new. A fix placed
there is INERT here. That is also why `core.hooksPath` reads empty on this host,
which #782 had attributed to another session toggling it.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label):

  * `test_git_config_declares_an_ssh_keepalive` is THE regression test for the
    measured defect.
  * `test_the_keepalive_interval_is_shorter_than_the_measured_close` pins the
    RELATIONSHIP that makes the fix a fix. An interval above the server's idle
    close is not a weaker mitigation, it is none at all — and nothing else in
    the tree would notice the number drifting up.
  * `test_the_disconnect_budget_is_explicit_not_inherited` is REGRESSION
    coverage for an audit finding: `ServerAliveCountMax` defaults to 3, so
    setting only the interval silently introduces a 90 s disconnect trigger on
    EVERY git-over-ssh operation on the host. Inheriting that silently is the
    defect; declaring it is the fix.
  * `test_every_GIT_SSH_COMMAND_export_carries_the_keepalive` is a LEDGER, and
    REGRESSION coverage for a bypass: `GIT_SSH_COMMAND` (env) beats
    `core.sshCommand` (config), so any script exporting it discards the fix.
  * `test_the_setting_is_reachable_from_the_home_manager_entry_point` is an
    INVARIANT GUARD, and it is the one that would have caught the original
    misplacement: a setting in a file nothing imports ships nothing. It is
    labelled as a guard and NOT counted as regression coverage for #782.

(Five tests, and the list above has one entry each — count it rather than
trusting a total maintained in parallel with the thing it counts.)

🔴 WHAT THIS FILE STILL CANNOT SEE, STATED SO NOBODY READS IT AS WIDER:
  * `scripts/claim-work.sh` uses `${GIT_SSH_COMMAND:-…}`. An INHERITED outer
    value wins and the keepalive is then silently absent, while the ledger reads
    the literal default and passes. Pre-dates this change and is only reachable
    from an operator's or a parent agent's environment — but the ledger is
    structurally blind to it, and that is not the same as it being safe.
  * The setting shipping is not the setting being LIVE. It reaches a host only
    after `home-manager switch`; until then #782 is unmitigated there.

🔴 THIS FILE PARSES NIX SOURCE TEXT, WHICH IS A WEAKER INSTRUMENT THAN
EVALUATING IT, AND THAT IS A DELIBERATE TRADE. Evaluating would mean shelling
out to `nix`, which is not available to `checks.pytests` inside the nix sandbox
— the test would then SKIP in the hermetic tier and be structurally blind there,
which is worse than a text pin that runs identically in both tiers. To stop the
pin degrading into "the word appears somewhere", the VALUE is extracted and then
asserted on STRUCTURALLY (parsed integers, relationships), never by substring.

🔴 NO `git`-METADATA READS OF THIS REPO — `nix flake check` builds
`checks.pytests` from a tracked-file copy with no `.git`, so a baseline taken
from `origin/main` would SKIP in the hermetic tier and go unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_MODULE = REPO_ROOT / "nix" / "programs" / "git" / "default.nix"
PROGRAMS_INDEX = REPO_ROOT / "nix" / "programs" / "default.nix"
SCRIPTS = REPO_ROOT / "scripts"
GITHOOKS = REPO_ROOT / "githooks"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

# Assembled, not spelled, so THIS file's own text cannot register as one of the
# assignments the ledger below enumerates.
NEEDLE = "GIT_SSH_" + "COMMAND="


def _searchable_files() -> list[Path]:
    """Every file that could plausibly set the variable — not just `*.sh`.

    🔴 A FIRST VERSION GLOBBED `scripts/**/*.sh` WHILE ITS DOCSTRING CLAIMED IT
    "fails when the set GROWS". An audit walked it three ways, all real shapes in
    this tree: a `.py` under `scripts/`; an EXTENSIONLESS shell script (there are
    43 of them here — `run3`, `dogfood-cycle`, `mail-triage`, …); and anything in
    `githooks/`, which is the directory hosting the very pre-push hook whose
    runtime caused #782 and which is not under `scripts/` at all.

    So the scan is defined by "a text file in the two directories that hold
    executable code", never by extension. Breadth is the point of a ledger; an
    enumeration narrower than its own sentence is a coverage claim nobody can
    see is false.

    ⚠ ONE DELIBERATE EXCLUSION: `.md`, and its ORIGINAL JUSTIFICATION HERE WAS
    FALSE. This said `githooks/README.md` "shows the bad form as a
    counter-example"; it does not — that file contains no such assignment at
    all, because the README change that would have introduced one was reverted
    in c558eb0d. A census of tracked files finds the string in exactly two
    places, both already in the ledger. So the exclusion protects against
    nothing TODAY and is kept only pre-emptively: documentation is not executed,
    and a ledger that reddens at prose is a ledger people learn to ignore.
    Stated honestly rather than left reading as a measured need.

    ⚠ RESIDUAL, not hidden: a test FIXTURE representing the broken state would
    still trip this, as would a shell COMMENT mentioning the assignment — the
    "documentation is not executed" reasoning does not extend to comments inside
    executable files. Neither exists today.
    """
    out: list[Path] = []
    for root in (SCRIPTS, GITHOOKS):
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if (p.is_file() and "__pycache__" not in p.parts
                    and p.suffix.lower() != ".md"
                    and p.resolve() != Path(__file__).resolve()):
                out.append(p)
    return out

# The measured close, in seconds. Named so the pins below state a RELATIONSHIP
# rather than restate a magic number.
MEASURED_IDLE_CLOSE_S = 360


def _nix_code(path: Path, keep_strings: bool = True) -> str:
    r"""Nix source with ALL comment forms removed, in ONE string-aware pass.

    🔴 THIS FUNCTION IS THE FIX FOR A BUG I SHIPPED TWICE. Round 3 of this PR's
    audit hardened comment-stripping HERE and left the identical whole-line-`#`
    stripper open in the two sibling guards that read `nix/home.nix` and
    `nix/programs/default.nix` — so the very delete-shape this docstring calls
    "the one a reviewer would actually reach for" walked all three links of the
    reachability chain while the suite stayed green. A predicate open-coded at N
    sites is wrong at N-1 of them; consolidating is what makes that audible, so
    every nix read in this file now goes through this one function.

    Handles all three nix comment forms and both string forms:
      * `# …` to end of line, and `/* … */`, each recognised only OUTSIDE a
        string — a `#` in an option value, or a `/*` inside a path glob like
        `"~/ignore/*.tmp"`, must not eat code. (The previous version ran a
        `/\*.*?\*/` regex with no string awareness; an ordinary glob value plus
        any later `*/` deleted the ENTIRE remainder of the module from the
        parser's view.)
      * `"…"` with backslash escapes, and `''…''` indented strings.

    `keep_strings=False` blanks string CONTENT (keeping the quotes), so a pin
    looking for code cannot be satisfied by the same text spelled inside a
    string literal. Reads that need the value itself pass True.

    Newlines inside removed comments are preserved so that line-anchored
    patterns still see the structure they expect.
    """
    src = path.read_text(encoding="utf-8")
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(src[i:j] if keep_strings else '"' + " " * max(0, j - i - 2) + '"')
            i = j
            continue
        if src.startswith("''", i):
            # 🔴 `''$`, `'''` and `''\\` are ESCAPES inside an indented string, not
            # terminators. `nix/home.nix` contains four `''${…}` antiquotation
            # escapes, and treating one as a closer DESYNCS the scanner for the
            # rest of the file: every real closer is then read as an opener.
            # Measured consequences before this was handled — both of them the
            # exact failure classes this file exists to prevent:
            #   * FALSE GREEN — the contents of a later `''` block were emitted
            #     as code, so `keep_strings=False` did not blank them and a pin
            #     could be satisfied by text spelled inside a string.
            #   * FALSE RED, position-dependent — a later block contains the
            #     glob `{plugin,plugins}/*.{ts,js}`, whose `/*` then swallowed
            #     to EOF, so merely MOVING `programs = programs;` to the end of
            #     home.nix turned this suite red. A guard whose verdict depends
            #     on where in a file an assignment sits is worse than no guard.
            # Confirmed against nix itself: `builtins.stringLength ''a="''${x}"''`
            # counts the `${` as literal.
            j = i + 2
            while j < n:
                if src.startswith("''", j):
                    if src[j + 2:j + 3] in ("$", "'", "\\"):
                        j += 3          # escape — keep scanning
                        continue
                    j += 2
                    break
                j += 1
            else:
                j = n
            chunk = src[i:j]
            out.append(chunk if keep_strings
                       else "''" + "".join(ch if ch == "\n" else " "
                                           for ch in chunk[2:-2]) + "''")
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("\n" * src.count("\n", i, j))
            i = j
            continue
        if c == "#":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _top_level_of_settings() -> str:
    r"""Only the entries at the TOP level of the git module's `settings` attrset.

    🔴 WHY DEPTH MATTERS, AND WHY AN ANCHOR WAS NOT ENOUGH. `^\s*core\.sshCommand`
    matches at ANY indentation, so a keepalive declared inside a scoped
    `includes = [{ condition = "gitdir:~/workspace/"; contents = {…}; }]` — or a
    raw `includeIf` attribute — satisfied it while applying to ONE SUBTREE. That
    leaves #782 live for every other repo on the host, which is the opposite of
    the claim the guard makes. An audit walked it both ways before this existed.

    Nested braces are blanked rather than removed, so a match here means "a
    direct member of settings", not "somewhere under settings".
    """
    # 🔴 LOCATE on the string-BLANKED text, SLICE the string-KEEPING one. Both
    # passes remove comments identically and blank strings in place, so the two
    # outputs have identical offsets — which lets the locator be immune to a
    # `settings = {` spelled inside a string while the extraction still sees the
    # real value.
    # 🔴 `\b` is NOT the right boundary: it treats `.` as one, so `lfs.settings`
    # satisfied `\bsettings` and an earlier unrelated attrset could hijack the
    # locator. That is the SAME `\b` trap this file already fixed for
    # `import ./git` — one rule, wrong at the site nobody updated.
    code = _nix_code(GIT_MODULE, keep_strings=True)
    located = _nix_code(GIT_MODULE, keep_strings=False)
    m = re.search(r"(?<![.\w])settings\s*=\s*\{", located)
    if not m:
        return ""
    depth, i, body = 1, m.end(), []
    while i < len(code) and depth:
        ch = code[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        body.append(ch)
        i += 1
    depth = 0
    out: list[str] = []
    for ch in body:
        if ch == "{":
            depth += 1
            out.append(" ")
        elif ch == "}":
            depth -= 1
            out.append(" ")
        else:
            out.append(ch if depth == 0 else (" " if ch != "\n" else "\n"))
    return "".join(out)


def _ssh_command() -> str:
    """The `core.sshCommand` declared as a DIRECT member of `settings`, or "".

    Anchored (so `notcore.sshCommand` cannot satisfy it) AND depth-constrained
    (so a value scoped to a subtree cannot) — see `_top_level_of_settings`.
    """
    m = re.search(r'(?:^|\s)core\.sshCommand\s*=\s*"([^"]*)"\s*;',
                  _top_level_of_settings())
    return m.group(1) if m else ""


def _assigned_value(line: str) -> str:
    """The VALUE assigned to the variable on this line, quotes respected.

    🔴 SPLITTING ON THE VARIABLE NAME AND KEEPING THE REST OF THE LINE IS NOT
    SCOPING — it only trims the LEFT. An audit walked the previous version both
    ways, and the second is the realistic one:

        export GIT_SSH_...="ssh -o BatchMode=yes"; export OTHER="... -o ServerAliveInterval=30"
        export GIT_SSH_...="ssh -o BatchMode=yes"  # TODO restore -o ServerAliveInterval=30

    A keepalive DELETED and left as a TODO passed the very ledger that exists to
    catch it. So the value ends where the quoted string ends, and an unquoted
    value ends at whitespace or `;`.
    """
    rest = line.split(NEEDLE, 1)[1].lstrip()
    if rest[:1] in ("'", '"'):
        quote, j = rest[0], 1
        while j < len(rest):
            if rest[j] == "\\" and quote == '"':
                j += 2
                continue
            if rest[j] == quote:
                break
            j += 1
        return rest[1:j]
    return re.split(r"[;\s]", rest, maxsplit=1)[0]


def _opt(value: str, name: str) -> int | None:
    """One `-o <Name>=<int>` from an ssh command line, parsed as an int.

    Returns None when absent OR when present with a non-integer argument — a
    caller asserting on the number then fails rather than silently comparing
    against a string it mistook for a measurement.
    """
    m = re.search(rf"-o\s+{re.escape(name)}=(\d+)\b", value)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# 🔴 THE REGRESSION TESTS — the measured defect
# --------------------------------------------------------------------------- #
def test_git_config_declares_an_ssh_keepalive():
    """Without this, a devrc push whose test gate runs longer than the server's
    ~360 s idle close dies with SIGPIPE and creates no branch, while the hook
    prints its own success on the same screen."""
    assert GIT_MODULE.is_file(), f"{GIT_MODULE} is gone — this test proves nothing"
    value = _ssh_command()
    assert value, (
        "nix/programs/git declares no core.sshCommand, so git sends no SSH "
        "keepalives. Measured: github.com closes an idle receive-pack session "
        f"after ~{MEASURED_IDLE_CLOSE_S}s, so a push whose pre-push gate runs "
        "longer dies with SIGPIPE (rc=141) and the branch is never created — "
        "while the hook reports success. See #782.")
    assert _opt(value, "ServerAliveInterval") is not None, (
        "core.sshCommand is declared but carries no numeric "
        f"ServerAliveInterval: {value!r}")


def test_the_keepalive_interval_is_shorter_than_the_measured_close():
    """🔴 THE RELATIONSHIP, not the number.

    A keepalive longer than the server's idle close is not a weaker fix — it is
    no fix, and it would look exactly like this one. Pinned against the measured
    close rather than against a copy of itself, so the value cannot drift upward
    unnoticed.
    """
    interval = _opt(_ssh_command(), "ServerAliveInterval")
    assert interval is not None
    assert interval > 0, (
        "ServerAliveInterval=0 DISABLES keepalives. That is precisely this "
        "host's effective default and the configuration that produced #782 — "
        "the option would be present and inert.")
    assert interval < MEASURED_IDLE_CLOSE_S, (
        f"ServerAliveInterval={interval}s is not shorter than the measured "
        f"~{MEASURED_IDLE_CLOSE_S}s idle close, so the connection dies before a "
        "keepalive is ever sent.")
    # A margin, not merely 'less than': one lost probe must not exhaust it.
    assert interval * 3 <= MEASURED_IDLE_CLOSE_S, (
        f"ServerAliveInterval={interval}s leaves no room for a dropped probe "
        f"before the ~{MEASURED_IDLE_CLOSE_S}s close")


def test_the_disconnect_budget_is_explicit_not_inherited():
    """🔴 REGRESSION COVERAGE FOR THE FIX'S OWN FAILURE MODE.

    `ServerAliveCountMax` defaults to **3**. Declaring only the interval
    therefore introduces, silently and machine-wide, a
    `interval x 3` disconnect trigger on EVERY git-over-ssh operation — 90 s at
    interval 30, where previously only TCPKeepAlive applied and a stall of any
    length survived. On a flaky link that kills pushes which used to recover.

    The setting must be DECLARED, so the budget is a reviewed decision rather
    than an inherited accident, and the resulting budget must be at least as
    long as the interval it is built from.
    """
    value = _ssh_command()
    count = _opt(value, "ServerAliveCountMax")
    assert count is not None, (
        "core.sshCommand sets ServerAliveInterval but NOT ServerAliveCountMax, "
        "so the disconnect budget is inherited (default 3) rather than chosen. "
        f"At this interval that is a silent {(_opt(value, 'ServerAliveInterval') or 0) * 3}s "
        "machine-wide disconnect trigger on every git-over-ssh operation. "
        "Declare it explicitly — see this test's docstring.")
    assert count >= 1, f"ServerAliveCountMax={count} is not a usable budget"
    interval = _opt(value, "ServerAliveInterval") or 0
    assert interval * count >= interval, "unreachable unless interval is negative"
    # The budget is what a reader will reason about, so state it in the failure.
    assert interval * count >= 60, (
        f"interval {interval}s x countmax {count} = {interval * count}s is a very "
        "short tolerance for an unresponsive server; a brief stall would now "
        "kill pushes that previously recovered")


# --------------------------------------------------------------------------- #
# INVARIANT GUARD — a setting nothing imports ships nothing
# --------------------------------------------------------------------------- #
def test_the_setting_is_reachable_from_the_home_manager_entry_point():
    """🔴 NOT regression coverage for #782 — an invariant guard, and the one
    that would have caught this fix's FIRST misplacement.

    The original version of this change wrote the keepalive into a file that
    could never apply it on a home-manager host. "The setting exists" and "the
    setting ships" are different claims. This pins the second, as far as a text
    read can: the module this file asserts on must actually be imported by the
    aggregator `nix/home.nix` builds `programs` from.

    🔴 IT MUST CHECK THE WHOLE CHAIN, AND A FIRST VERSION CHECKED ONE LINK.
    An audit walked it: commenting out `programs = programs;` in `nix/home.nix`
    — which makes every setting in this module ship NOTHING — left the suite at
    5 passed. And `import\\s+\\./git\\b` matches `import ./git-old`, because `-`
    is a word boundary, so redirecting the import to a different module also
    passed. Both are now covered. The chain is:

        nix/home.nix  programs = programs;
          -> nix/programs/default.nix  git = import ./git {};
            -> nix/programs/git/default.nix  core.sshCommand = …
    """
    # keep_strings=False: a pin looking for CODE must not be satisfiable by the
    # same text spelled inside a nix string literal.
    code = _nix_code(PROGRAMS_INDEX, keep_strings=False)
    # `\b` is NOT enough: `./git-old` matches it. Require the import to be
    # followed by nix argument-set syntax, which `./git-old {}` cannot satisfy
    # while still naming ./git.
    assert re.search(r"import\s+\./git\s*\{", code), (
        f"{PROGRAMS_INDEX} no longer imports ./git (or imports a DIFFERENT "
        "module whose name starts with 'git') — the module this file asserts "
        "on would generate nothing")
    assert re.search(r"^\s*git\s*=\s*git\s*;", code, re.M), (
        f"{PROGRAMS_INDEX} imports ./git but does not expose it as `git`, so "
        "home-manager's programs.git never receives these settings")
    # The link above that one: home.nix must actually hand `programs` to
    # home-manager. Without this the whole aggregator is inert and every
    # assertion in this file is about a file nothing reads.
    home_code = _nix_code(HOME_NIX, keep_strings=False)
    assert re.search(r"^\s*programs\s*=\s*programs\s*;", home_code, re.M), (
        f"{HOME_NIX} no longer assigns `programs = programs;`, so "
        f"{PROGRAMS_INDEX} is built but never handed to home-manager — every "
        "setting in nix/programs/git ships nothing")


# --------------------------------------------------------------------------- #
# 🔴 THE LEDGER — every site that can bypass the fix must carry it
# --------------------------------------------------------------------------- #
def test_every_GIT_SSH_COMMAND_export_carries_the_keepalive():
    """🔴 `GIT_SSH_COMMAND` (env) BEATS `core.sshCommand` (config).

    So the setting pinned above is silently discarded inside any script that
    exports its own. `scripts/claim-work.sh` does exactly that, and
    `claudedocs/design-claim-by-push.md` records claim-BY-PUSH as shipped — a
    long remote operation under that export would resurrect #782 with nothing
    able to observe it.

    This is a LEDGER, not a spot check: the keepalive requirement now lives at
    three sites, and a requirement open-coded at N sites is typically wrong at
    N-1 of them. Enumerating every export and asserting on ALL of them is what
    makes a disagreement between the sites audible, and it fails when the set
    GROWS (a new export that forgot) as well as when an existing one is edited.
    """
    offenders: list[str] = []
    found_any = False
    for path in _searchable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue          # binary or unreadable: cannot assign a shell var
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or NEEDLE not in line:
                continue
            found_any = True
            # 🔴 Search the ASSIGNMENT'S VALUE, not the whole line. A line-wise
            # match is satisfied by an unrelated neighbour —
            #   export OTHER="ssh -o ServerAliveInterval=30"; export GIT_SSH_...="ssh"
            # passed before this narrowing. Green for a reason unrelated to the
            # option is the shape this whole file exists to avoid.
            value = _assigned_value(line)
            # `-o Name=value` and `-oName=value` are both valid ssh spellings
            # (confirmed against `ssh -G`), and both are in use in this repo.
            if not re.search(r"-o\s*ServerAliveInterval=\d+", value):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {stripped}")

    # 🔴 POSITIVE CONTROL. Without this, a glob that matched nothing — a moved
    # directory, a renamed extension — produces an empty `offenders` list and
    # reads as a clean pass. "No offenders" and "nothing was inspected" must not
    # look the same.
    assert found_any, (
        f"no such assignment was found anywhere under {SCRIPTS} or {GITHOOKS}. "
        "This test inspected nothing, so its silence means nothing — the scan "
        "is broken, not the repo clean.")

    assert not offenders, (
        "these GIT_SSH_COMMAND exports override core.sshCommand and carry no "
        "ServerAliveInterval, so git sends no keepalives under them and a "
        "remote operation lasting longer than the measured "
        f"~{MEASURED_IDLE_CLOSE_S}s idle close dies with SIGPIPE (#782):\n  "
        + "\n  ".join(offenders))
