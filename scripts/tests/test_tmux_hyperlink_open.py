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
# Generous on purpose: the measured render is 15-25 ms, so this is ~400x the
# observed need. It is a DEADLINE, not a delay — a healthy run returns as soon
# as the grid holds the URI and never spends it.
RENDER_TIMEOUT_S = 10.0


def tmux(*args):
    return subprocess.run(
        ["tmux", "-L", SOCK, *args], capture_output=True, text=True, timeout=30
    )


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
    URI arriving 15-25 ms later. Every grid-reading test was therefore passing
    only when some unrelated earlier test happened to burn that much wall time
    first — which made `test_the_grid_hyperlink_spans_the_wrap` fail 7 of 8 runs
    in isolation while `test_tmux_stores_the_hyperlink_and_can_report_it` failed
    1 of 3 in file order. ONE defect, two symptoms, ranked by run order.

    That ordering is why this is a fixture and not a retry on the one test that
    was observed flaking: deleting that test — the standing recommendation
    before this was measured — would have left the WORSE flake behind it, newly
    first in line. One rule, one place.

    🔴 IT CANNOT MASK A REAL BREAKAGE. On timeout it FAILS, loudly, quoting the
    grid it did see, so an OSC 8 that never renders — the actual thing these
    tests pin — is still RED and is distinguishable from a slow one. A bare
    `sleep` would have been the masking version of this fix. Mutation-checked:
    with the OSC 8 wrapper removed from the pane payload the fixture fails with
    the message below and `Last capture: '\\n'`, never a pass.

    Only the two grid-READING tests take this fixture. The tests that read a
    server option or the key table keep plain `server`, so a genuine OSC 8
    regression still leaves their independent signal green and legible.
    """
    deadline = time.monotonic() + RENDER_TIMEOUT_S
    last = ""
    while time.monotonic() < deadline:
        cap = tmux("capture-pane", "-p", "-H", "-t", "t")
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


def test_tmux_stores_the_hyperlink_and_can_report_it(rendered):
    # ⚠ INVARIANT GUARD, not regression coverage: tmux PARSES OSC 8 from
    # applications unconditionally (input.c), so the grid holds the URI
    # whether or not terminal-features is set — this passed BEFORE the fix
    # too. It pins the storage the fix DEPENDS on (a tmux upgrade dropping
    # grid link storage would break the `o` binding silently).
    # `capture-pane` prints to stdout only with -p; without it the capture
    # goes to a paste buffer and stdout is silently empty.
    out = tmux("capture-pane", "-p", "-H", "-t", "t")
    assert out.returncode == 0, out.stderr
    assert URI in out.stdout, out.stdout


def test_the_grid_hyperlink_spans_the_wrap(rendered):
    # ⚠ INVARIANT GUARD, same caveat as above — measured: the copy-mode
    # format reads the full URI at the link's first cell AND on the wrapped
    # continuation row, which is what makes the whole wrapped link one
    # clickable span in Alacritty (per-cell scan across rows). The third
    # probe sits 10 columns into the continuation row, still on the link.
    # Copy mode is entered ONCE — `send-keys -X` outside copy mode exits 0
    # and silently does nothing, which is how this test once failed green.
    p = tmux("copy-mode", "-t", "t")
    assert p.returncode == 0, p.stderr
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
