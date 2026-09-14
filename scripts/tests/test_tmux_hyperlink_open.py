"""🔴 A URL WRAPPED ACROSS LINES INSIDE TMUX IS ONLY CLICKABLE ON ITS FIRST
LINE, AND THE CLICK OPENS A TRUNCATED URL — because tmux emits every pane grid
row to the outer terminal as a HARD line (measured 2026-09-12: `\r\n` between
rows in full draws, CUP/CHA + `\x1b[K` at every wrap boundary in incremental
redraws). Alacritty's regex hint matcher only spans rows carrying a WRAPLINE
flag (alacritty_terminal search.rs), and tmux never sets one — upstream calls
this wontfix (alacritty#1705) and answers "use OSC 8 escape sequences". An
OSC 8 hyperlink is attached PER CELL, so it survives the row split: tmux keeps
it across wrapped rows and Alacritty opens the stored URI, not the visible
text (alacritty display/hint.rs — source-verified).

This pins the two shipped halves of that fix, both in .tmux.conf (which
nix/programs/tmux/default.nix readFiles verbatim into extraConfig):

  1. `set -as terminal-features ',*:hyperlinks'` — tmux's built-in
     default-features table does NOT include alacritty, so OSC 8 from
     applications is parsed but never re-emitted to the outer terminal without
     this (tmux#4308). Functionally verified below against a REAL throwaway
     tmux server, not by re-parsing the file.

  2. `bind -T copy-mode-vi o` (and the plain copy-mode table) — opens
     `#{copy_cursor_hyperlink}`, the URI from tmux's grid hyperlink table,
     via xdg-open. The prefix-table `o` binding (window 16) is a DIFFERENT
     table and must survive unshadowed.

Known residual, stated not hidden: this only covers URLs that are EMITTED as
OSC 8 (gcc 10+, `ls --hyperlink`, `rg --hyperlink-format`, ...). A plain-text
wrapped URL in arbitrary output still truncates on click — no terminal-side
fix exists (alacritty#1705 wontfix); the copy-mode `o` binding also answers
"" (empty format) there.
"""

import os
import re
import shutil
import subprocess
import time
import uuid

import pytest

REPO = __file__.rsplit("/scripts/tests/", 1)[0]
CONF = f"{REPO}/.tmux.conf"

# A URI longer than the 40-col test pane, over visible text long enough to
# wrap across two grid rows — that is the exact shape the click path must
# survive.
URI = "https://example.com/" + "a" * 60
LINK_TEXT = "L" * 60
OSC8 = f"\\033]8;;{URI}\\033\\\\{LINK_TEXT}\\033]8;;\\033\\\\"
SOCK = f"hint-open-{uuid.uuid4().hex[:8]}"
# Resolved here but NOT asserted at module scope: an `assert` during import
# makes the whole module uncollectable on a tmux-less host (0 tests, no
# diagnostic), including the two tests below that only read .tmux.conf. The
# deferred assert in the `server` fixture is the pattern
# test_tmux_reply_agent.py chose for the same runtime.
TMUX = shutil.which("tmux")
# Generous on purpose, and deliberately NOT justified by a multiple of the
# observed render time. Three independent measurements of that time disagree —
# 15-25 ms, 3.2-23.5 ms (median 14.0), and 11.9-55.4 ms (median 20-21) — because
# it moves with host load, and this box runs dozens of concurrent suites. A
# "~400x headroom" claim derived from the first of those was wrong by the third.
# What is actually being asserted: 10 s is far above any PANE-GRID render
# latency observed at any load. It is a DEADLINE, not a delay — `rendered`
# returns as soon as the grid holds the URI and never spends it.
RENDER_TIMEOUT_S = 10.0
# 🔴 A SECOND, SEPARATE CONSTANT — NOT a reuse of the one above, because the two
# wait on DIFFERENT OBJECTS (see `enter_copy_mode_on_the_link`): the pane grid
# vs the copy-mode screen's hyperlink table.
# ⚠ RETRACTED, and corrected here rather than quietly dropped: this comment used
# to say a dropped-format regression "takes 2x this to report" because "each
# consumer spends its deadline in turn". MEASURED with the format renamed to an
# unknown one: a single path reports in 10.96s, not 21s. `rendered` reads the
# GRID, which a format drop does not touch, so it returns fast — and had it
# timed out, its `pytest.fail` aborts before this consumer ever runs. The whole
# FILE takes ~21s only because two tests each pay THIS constant once.
# So: raising RENDER_TIMEOUT_S contributes ZERO to a dropped-format report;
# raising this one is what costs, once per grid-reading test.
COPY_MODE_TIMEOUT_S = 10.0


def tmux_on(sock, *args):
    """Low-level: run a tmux command against an EXPLICIT socket.

    Exists so the forced-race regression test can drive its OWN server. Keeping
    every call bound to the module's `SOCK` is what made the retry below
    unreachable by any test — a fixture had already rendered the grid before a
    test could observe the race.
    """
    return subprocess.run(
        ["tmux", "-L", sock, *args], capture_output=True, text=True, timeout=30
    )


def tmux(*args):
    return tmux_on(SOCK, *args)


@pytest.fixture(scope="module")
def server():
    assert TMUX, "tmux is not on PATH — the live-tmux tests cannot run"
    # Fake HOME so .tmux.conf's session-created/after-select-window hooks
    # (pipe-activity.sh, activity-emit.sh, autoname-session.sh) resolve to
    # nothing — this test must not write a row into the REAL activity spool.
    import os

    env = dict(os.environ, HOME="/nonexistent-tmux-hyperlink-test")
    proc = subprocess.run(
        ["tmux", "-L", SOCK, "-f", CONF, "new-session", "-d", "-x", "40",
         "-y", "6", "-s", "t", "sh", "-c",
         f"printf '{OSC8}'; sleep 300"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if proc.returncode != 0:
        # Tear down whatever came up before failing: an assert before yield
        # skips the fixture teardown entirely and leaks the server on SOCK,
        # which the next run's identically-named socket would then contend
        # with.
        subprocess.run(["tmux", "-L", SOCK, "kill-server"],
                       capture_output=True, timeout=30)
        pytest.fail(f"tmux new-session failed: {proc.stderr}")
    yield
    subprocess.run(["tmux", "-L", SOCK, "kill-server"], capture_output=True,
                   timeout=30)


@pytest.fixture(scope="module")
def rendered(server):
    """Block until the pane's OSC 8 link is actually IN the grid.

    🔴 `new-session -d` returns when the SERVER is up, which says nothing about
    whether the pane command's `printf` has been read and parsed into the grid.
    MEASURED 2026-09-14: 30 of 30 immediate captures came back EMPTY, with the
    URI arriving 15-25 ms later (independently re-measured at 3.2-23.5 ms,
    median 14.0 ms, n=30 — the lower bound above is optimistic and the upper
    one holds). A grid read was therefore passing only when some unrelated
    earlier test happened to burn that much wall time first.

    ⚠ THE RATE IS LOAD-DEPENDENT, SO NO SINGLE FIGURE IS QUOTED HERE. Isolated,
    `test_the_grid_hyperlink_spans_the_wrap` was measured at 7 of 8 failing on
    one host-load and 3 of 8 on another; in file order it does not fail at all,
    because the test before it pays the render time. An earlier draft of this
    docstring quoted one run of each as though they were properties of the
    tests, and labelled an ISOLATED run as file order. What reproduces is the
    DIRECTION: isolated flakes, in-order does not.

    🔴 THE LOAD-BEARING CLAIM: with the fixture absent and only
    `test_tmux_stores_the_hyperlink_and_can_report_it` deleted, the whole file
    still flakes — so deleting the reported test on its own would NOT have
    fixed it, and that is why the wait is a FIXTURE rather than a retry on one
    test. Measured three times on that exact tree: 3 of 10 failing, then
    **17 of 24 at load 36**, and once **0 of 40** — the third by an audit that
    reported it as a refutation. Two of three reproduce it and the disagreement
    is consistent with the same load-dependence as every other rate here, so
    the claim stands; the single figure it used to quote did not.

    🔴 IT CANNOT MASK A REAL BREAKAGE. On timeout it FAILS, loudly, quoting the
    grid it did see, so an OSC 8 that never renders — the actual thing this
    test pins — is still RED and is distinguishable from a slow one. A bare
    `sleep` would have been the masking version of this fix. Mutation-checked:
    with the OSC 8 wrapper removed from the pane payload the fixture fails with
    the message below and `Last capture: '\\n'`, never a pass.

    Only the grid-READING test takes this fixture; the four that read the conf,
    a server option or the key table do not, because they never read the grid
    and would pay the poll for nothing. ⚠ That is the whole reason — an earlier
    draft claimed it also kept "their independent signal green" under a genuine
    OSC 8 regression, which is FALSE: dropping `terminal-features ,*:hyperlinks`
    from the shipped conf reddens 2 of those 4, and this fixture is not on that
    path at all.
    """
    deadline = time.monotonic() + RENDER_TIMEOUT_S
    last = ""
    while time.monotonic() < deadline:
        cap = tmux("capture-pane", "-p", "-H", "-t", "t")
        # 🔴 CHECK rc, DO NOT JUST READ stdout. A failing `capture-pane` returns
        # EMPTY stdout with its reason on stderr, which is indistinguishable
        # from "not rendered yet" if you only look at stdout: the loop then
        # burns the whole deadline and the message below blames the render,
        # asserting the OPPOSITE of the truth. MEASURED: swapping `-H` for an
        # unsupported flag produced exactly that, after 10.8s, while tmux's own
        # `unknown flag` on stderr was printed nowhere. The test deleted above
        # carried `assert out.returncode == 0, out.stderr`; losing that
        # diagnostic with nothing in its place was a regression, not a tidy-up.
        if cap.returncode != 0:
            pytest.fail(
                f"`capture-pane` FAILED (rc={cap.returncode}) — this is a tmux "
                f"or invocation error, NOT the render race and NOT a missing "
                f"hyperlink. stderr: {cap.stderr!r}"
            )
        last = cap.stdout
        if URI in last:
            return
        time.sleep(0.01)
    pytest.fail(
        f"the OSC 8 hyperlink never reached the tmux grid within "
        f"{RENDER_TIMEOUT_S}s — this is the failure these tests exist to "
        f"catch, NOT the render race this fixture absorbs. "
        f"Last capture: {last!r}"
    )


def enter_copy_mode_on_the_link(sock=None, deadline_s=COPY_MODE_TIMEOUT_S):
    """Enter copy mode with the cursor on the link, and the hyperlink READABLE.

    🔴 THE PANE GRID AND THE COPY-MODE SCREEN ARE DIFFERENT OBJECTS. `rendered`
    waits on `capture-pane`, which reads the PANE grid; the assertion below
    reads `#{copy_cursor_hyperlink}`, served from the COPY-MODE screen's own
    hyperlink table — a separate snapshot taken when copy mode is ENTERED.
    Waiting on the first does not gate the second, so a wait built only on
    `capture-pane` can be fully satisfied while the asserted object is empty:
    a guard that passes while the hazard it describes is live.

    ⚠ HONEST SCOPE. An audit measured this residual at 2 of 180 isolated runs
    (~1.1%) at host load 45-58 and found that retrying INSIDE one copy mode
    recovered 0 of 2 while cancel-and-re-enter recovered 2 of 2 — which is why
    the remedy here is re-ENTRY and not a re-read. I could NOT reproduce it:
    0 of 250 trials at load 36, which argues against 1.1% at that load but
    cannot rule the race out. It is fixed regardless, because waiting on the
    object you assert on is free and strictly better than waiting on one that
    merely correlates with it. **Do not read 0/250 as "there was no bug".**

    It cannot mask a real failure: on timeout it FAILS and says the format
    stayed empty, which is exactly the tmux-dropped-the-format regression the
    surviving test exists to catch.

    🔴 `cancel` IS THE WHOLE RETRY, SO ITS rc IS CHECKED — and checking
    `copy-mode`'s rc does NOT cover it. MEASURED on tmux 3.7c: issuing
    `copy-mode` at a pane ALREADY in copy mode returns rc 0, empty stderr, and
    does NOT re-snapshot. So if `cancel` ever failed, every later `copy-mode`
    would be an rc-0 no-op re-reading the same stale snapshot: the loop would
    still LOOK like a retry, would spin to the deadline, and would then blame a
    tmux regression for a harness fault — the exact defect the `rendered` rc
    check above exists to prevent, one screen further down.

    ⚠ `#{pane_in_mode}` is deliberately NOT asserted, and the comment beside
    `cancel` says why. An earlier version of this docstring claimed it WAS —
    while that comment forbade adding it, 26 lines apart, both written in the
    same commit. The inert-loop case an rc-0-but-ineffective `cancel` would
    produce is therefore UNCOVERED, stated rather than implied.
    """
    sock = SOCK if sock is None else sock
    deadline = time.monotonic() + deadline_s
    while True:
        p = tmux_on(sock, "copy-mode", "-t", "t")
        assert p.returncode == 0, p.stderr
        for step in ("top-line", "start-of-line"):
            s = tmux_on(sock, "send-keys", "-t", "t", "-X", step)
            assert s.returncode == 0, (step, s.stderr)
        out = tmux_on(sock, "display", "-p", "-t", "t",
                      "#{copy_cursor_hyperlink}")
        assert out.returncode == 0, out.stderr
        last = out.stdout.strip()
        if last == URI:
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                f"`#{{copy_cursor_hyperlink}}` never became readable within "
                f"{deadline_s}s of re-entering copy mode — last value {last!r}. "
                f"An UNKNOWN format expands to empty at rc 0, so this is also "
                f"what a tmux that dropped `copy_cursor_hyperlink` looks like."
            )
        c = tmux_on(sock, "send-keys", "-t", "t", "-X", "cancel")
        assert c.returncode == 0, ("cancel FAILED — the retry below would be "
                                   "an rc-0 no-op on a stale snapshot", c.stderr)
        # 🔴 DO NOT POLL `#{pane_in_mode}` FOR `0` HERE — but not for the reason
        # this comment gave for two commits, which was wrong twice over.
        #
        # RETRACTED: "it reads `2` in copy mode, so it is not a boolean" and
        # "each iteration pushes a mode and pops one". MEASURED on tmux 3.7c:
        # it is a COUNT OF STACKED MODES, it reads `1` in copy mode on an
        # otherwise-clean pane, and `copy-mode` at a pane already in copy mode
        # does NOT push — the count holds.
        #
        # THE REAL MECHANISM, and it is a property of THIS FIXTURE, not of
        # tmux: the pane is already in `view-mode` before any test touches it,
        # because the shipped .tmux.conf's `session-created` run-shell hooks
        # cannot resolve under the fixture's fake
        # `HOME=/nonexistent-tmux-hyperlink-test`, and tmux shows their output
        # in a view-mode pane. So the stack is [view]; `copy-mode` pushes to
        # [copy, view] = 2; ONE `cancel` pops to [view] = 1, never 0. Control:
        # shipped conf -> view-mode 8/8; `-f /dev/null` -> no mode 8/8.
        #
        # So a `== "0"` poll hangs because ONE cancel cannot reach 0 here, not
        # because the counter is unreadable. It is reachable if you pop twice
        # or clear the view mode first — deliberately not done, because
        # `cancel`'s rc above is cheaper and the extra state is not worth it.
        time.sleep(0.01)


def test_conf_appends_the_hyperlinks_terminal_feature():
    conf = open(CONF).read()
    assert re.search(r"^set -as terminal-features ',\*:hyperlinks'", conf, re.M)
    # The clipboard feature arrived the same way and must not have been
    # replaced by the new line — both must survive as separate appends.
    assert re.search(r"^set -ga terminal-features '\*:clipboard'", conf, re.M)


def test_conf_binds_o_in_BOTH_copy_mode_tables_with_the_grid_format():
    conf = open(CONF).read()
    # The bindings are written with `\` line continuations; join them into
    # logical lines so the whole command is one match target.
    logical = re.sub(r"\\\n\s*", " ", conf)
    for table in ("copy-mode", "copy-mode-vi"):
        line = re.search(rf"^bind -T {table} o .*$", logical, re.M)
        assert line, f"no `o` binding in the {table} table"
        assert "copy_cursor_hyperlink" in line.group(0), line.group(0)
        assert "xdg-open" in line.group(0), line.group(0)
    # The prefix-table `o` (window 16) must not have been repointed.
    assert re.search(r"^bind-key o select-window -t :16$", logical, re.M)


def test_running_tmux_accepts_the_hyperlinks_feature(server):
    out = tmux("show", "-s", "terminal-features")
    assert out.returncode == 0, out.stderr
    assert "hyperlinks" in out.stdout, out.stdout


# 🔴 DELETED 2026-09-14: `test_tmux_stores_the_hyperlink_and_can_report_it`.
# It read `capture-pane -p -H` and asserted the URI was in the grid. Removed
# because it detected NOTHING that the test below does not, and the route it
# read is one no shipped code path uses. MEASURED, against the SHIPPED
# artifact rather than the test's own payload:
#   - drop `set -as terminal-features ',*:hyperlinks'` from .tmux.conf
#     -> it PASSED (so did the test below; the conf/option tests caught it).
#   - the class both guards exist for — a tmux upgrade dropping the grid
#     hyperlink format — is SILENT: an unknown `#{...}` expands to EMPTY with
#     rc 0 (verified on tmux 3.7c: `#{copy_cursor_hyperlink_GONE}` -> `[]`,
#     rc 0). The key-table test only greps the literal string and stays green.
#     The test below is the ONLY one that asserts that format's VALUE, so it
#     is the only one that goes red. The deleted test never read the format.
# ⚠ Deleting it was NOT sufficient on its own and was never the whole fix —
# see the `rendered` docstring: without the fixture the test below still
# failed 3 of 10 with this one gone.


def test_the_grid_hyperlink_spans_the_wrap(rendered):
    # ⚠ INVARIANT GUARD, not regression coverage. The caveat in full, because
    # it used to live in a sibling test that was deleted and a cross-reference
    # to "the file docstring" pointed at prose that never carried it:
    #   - tmux PARSES OSC 8 from applications UNCONDITIONALLY (input.c), so the
    #     grid holds the URI whether or not `terminal-features` is set. This
    #     test therefore passed BEFORE the shipped fix too — and that fact is
    #     exactly what the deletion argument above rests on.
    #   - `capture-pane` prints to stdout only with `-p`; without it the capture
    #     goes to a paste buffer and stdout is silently EMPTY. That gotcha now
    #     applies to the `rendered` fixture's own call, which is why it is
    #     recorded here rather than lost with the test that used to state it.
    # What this pins that nothing else does: the copy-mode format reads the
    # full URI at the link's first cell AND on the wrapped continuation row,
    # which is what makes the whole wrapped link one clickable span in
    # Alacritty (per-cell scan across rows). The third probe sits 10 columns
    # into the continuation row, still on the link.
    # 🔴 Copy mode is entered by the delegate below — which may enter and
    # cancel it SEVERAL times — and the three probes then run inside the one
    # mode it leaves established. ⚠ A previous version of this comment said
    # "entered ONCE" and explained the discipline with "`send-keys -X` outside
    # copy mode exits 0 and silently does nothing". Both halves are wrong on the
    # tmux this file pins: MEASURED on 3.7c, every `send-keys -X` verb outside
    # copy mode returns rc **1** (`not in a mode`), so the `assert s.returncode
    # == 0` in the probe loop below does catch that case. (A line NUMBER used to
    # stand here and had already rotted into pointing at a comment — a
    # cross-reference is a claim, and one keyed on a line number ages badly.) The discipline still matters, for a different reason the old
    # sentence did not describe — a pane left in a STALE mode, which is rc 0 and
    # invisible; see the delegate's own note on `cancel`.
    enter_copy_mode_on_the_link()
    for label, nav in (
        ("row0 col0", ["top-line", "start-of-line"]),
        ("row1 col0", ["top-line", "start-of-line", "cursor-down"]),
        ("row1 col10", ["top-line", "start-of-line", "cursor-down"]
         + ["cursor-right"] * 10),
    ):
        for step in nav:
            s = tmux("send-keys", "-t", "t", "-X", step)
            assert s.returncode == 0, (label, step, s.stderr)
        out = tmux("display", "-p", "-t", "t", "#{copy_cursor_hyperlink}")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == URI, (label, out.stdout)


def test_the_copy_mode_retry_RECOVERS_a_snapshot_taken_before_the_link_landed():
    """🔴 REACHABILITY TEST for `enter_copy_mode_on_the_link`'s retry.

    Without this, the retry is certified by NOTHING. On a healthy run the
    module-scoped `rendered` fixture has already put the URI in the grid before
    any test runs, so the first copy-mode snapshot always matches and the loop
    body past iteration 1 never executes. MEASURED: replacing the `cancel` line
    with `pass` left the whole file GREEN — a mutation that survives because the
    happy path resolves anyway, which is the textbook unreachable-guard shape.

    So this drives its OWN server, with the link emitted LATE and copy mode
    entered BEFORE it lands — the only state in which the retry does work. It
    asserts recovery, and it is the regression test for the `cancel` call:
    with `cancel` removed it fails on the deadline instead of recovering.
    """
    assert TMUX, "tmux is not on PATH — the live-tmux tests cannot run"
    sock = f"hint-open-race-{uuid.uuid4().hex[:8]}"
    env = dict(os.environ, HOME="/nonexistent-tmux-hyperlink-test")
    proc = subprocess.run(
        ["tmux", "-L", sock, "-f", CONF, "new-session", "-d", "-x", "40",
         "-y", "6", "-s", "t", "sh", "-c",
         f"sleep 0.75; printf '{OSC8}'; sleep 300"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if proc.returncode != 0:
        tmux_on(sock, "kill-server")
        pytest.fail(f"tmux new-session failed: {proc.stderr}")
    try:
        # Enter copy mode NOW — the link is still ~0.75s away, so this
        # snapshot is necessarily empty. This is the state the retry exists
        # for, and the state no other test in this file can produce.
        assert tmux_on(sock, "copy-mode", "-t", "t").returncode == 0
        # 🔴 MOVE THE CURSOR ONTO THE LINK BEFORE READING, OR THIS CHECK IS A
        # TAUTOLOGY. `#{copy_cursor_hyperlink}` is the hyperlink AT THE COPY
        # CURSOR, and on entering copy mode the cursor sits at the pane cursor
        # — one cell past the end of a 60-char link that ends at row 1 col 19.
        # It therefore reads '' whether or not the link has landed, so the
        # assert below could never fail. MEASURED with the link definitively
        # in the grid: unmoved cursor -> '' (guard silent); after `top-line`
        # + `start-of-line` -> the URI (guard fires).
        #
        # That is not pedantry: it is the only thing standing between this
        # test and a VACUOUS PASS. If ~0.75s elapses before this point — a
        # deschedule, a slower host, a CI sandbox, or anyone shortening the
        # sleep this message invites them to tune — the snapshot already holds
        # the link, the delegate returns on iteration 1 without ever calling
        # `cancel`, and the test passes having exercised nothing. CONTROL:
        # with `cancel` broken AND a 1.0s deschedule injected here, the old
        # tautological guard let the suite pass 6/6; with this fixed guard the
        # same mutant FAILS on exactly this test.
        for step in ("top-line", "start-of-line"):
            assert tmux_on(sock, "send-keys", "-t", "t",
                           "-X", step).returncode == 0
        empty = tmux_on(sock, "display", "-p", "-t", "t",
                        "#{copy_cursor_hyperlink}").stdout.strip()
        assert empty != URI, (
            "the link rendered before copy mode was entered, so this test did "
            "NOT exercise the retry — raise the sleep in the pane command", empty)

        # The delegate must now recover, which it can only do by cancelling
        # and re-entering: a re-read inside this mode returns the same stale
        # snapshot forever (measured, 0 of 2 recovered that way).
        enter_copy_mode_on_the_link(sock=sock, deadline_s=COPY_MODE_TIMEOUT_S)

        got = tmux_on(sock, "display", "-p", "-t", "t",
                      "#{copy_cursor_hyperlink}").stdout.strip()
        assert got == URI, got
    finally:
        tmux_on(sock, "kill-server")


def test_the_binding_is_listed_by_tmux_in_both_tables(server):
    # `list-keys -T <table> <key>` returns rc 0 with EMPTY output even for a
    # key that IS bound (measured on tmux 3.7c, all three tables), so this
    # must filter the full listing instead — grepping a broken filter would
    # be a green built on nothing.
    out = tmux("list-keys")
    assert out.returncode == 0, out.stderr
    for table in ("copy-mode", "copy-mode-vi"):
        hits = [ln for ln in out.stdout.splitlines()
                if f"-T {table} " in ln and ln.split()[3] == "o"]
        assert hits, f"no `o` binding listed in the {table} table"
        assert all("copy_cursor_hyperlink" in ln and "xdg-open" in ln
                   for ln in hits), hits
