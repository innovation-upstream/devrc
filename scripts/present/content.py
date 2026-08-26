#!/usr/bin/env python3
"""The PROSE SPINE and the hand-authored diagrams.

🔴 NO PROSE HERE MAY RESTATE A NUMBER THE GENERATOR MEASURES. That is the exact
rule, it is narrower than "no quantities", and it is what
`scripts/tests/test_present_content.py` enforces: it takes every value the live
registry produces, pulls the standalone numeric tokens out of it, and fails if
one of them appears in the page's authored VISIBLE TEXT.

Historical quantities are deliberately IN BOUNDS — a killed proposal's
measurement, a dated incident's count. They are permanently true, they are not
what the generator measures, and stripping them would gut §11 and §12, whose
whole job is to carry the number that settled a question. What is out of bounds
is restating a LIVE fact: the moment prose carries a byte count or a test total,
that paragraph starts aging, and this repo has measured its own prose false in
both directions.

Prose may name a MECHANISM ("the listing is capped at a fraction of the context
window"), never its live VALUE.

WHY THE DIAGRAMS ARE HAND-AUTHORED SVG
--------------------------------------
The page must open from `file://` with no network. A mermaid `<script>` tag is
disqualified outright, and rendering mermaid at build time would make the build
depend on a node toolchain being present — which turns a missing dependency into
a missing diagram, i.e. a silently thinner page. Hand-authored SVG has no build
step, no fetch, and no failure mode between "the file is there" and "it is not".

Every diagram uses `currentColor` and the page's CSS custom properties, so it
inverts correctly with the theme instead of being a light-mode image sitting in
a dark page.

TWO KINDS OF DIAGRAM
--------------------
`DIAGRAMS` are static: they name mechanisms and carry no values at all.
`LIVE_DIAGRAMS` are handed the MeasurementSet and render counts from it — the
§0 overview is the only one. A live diagram never formats a number itself; it
prints the measured row's own value, or the word UNMEASURED.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Section model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Section:
    slug: str
    number: str
    title: str
    lede: str
    #: Blocks: ("p", html) | ("note", (kind, title, html)) | ("ul", [html, ...])
    #: | ("svg", key) | ("svgm", key) | ("measure", key) | ("unbanner", None)
    #: | ("cards", [(title, html), ...]) | ("h3", text) | ("kv", [(k, v), ...])
    blocks: tuple = ()
    #: Rendered as a visible NOT YET WRITTEN banner instead of the blocks.
    stub: str = ""
    tags: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Diagrams — inline SVG, theme-aware via currentColor + CSS custom properties
# --------------------------------------------------------------------------- #

_SVG_HEAD = (
    '<svg role="img" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
    'class="diagram" aria-label="{alt}">'
    '<title>{alt}</title>'
    '<defs><marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
    '<path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>'
)


def _box(x, y, w, h, label, sub="", tone="n"):
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
        f'class="dbox dbox-{tone}"/>',
        f'<text x="{x + w / 2}" y="{y + (20 if sub else h / 2 + 5)}" '
        f'class="dlabel">{label}</text>',
    ]
    if sub:
        for i, line in enumerate(sub.split("|")):
            out.append(
                f'<text x="{x + w / 2}" y="{y + 38 + i * 14}" class="dsub">{line}</text>'
            )
    return "".join(out)


def _arrow(x1, y1, x2, y2, mid, label=""):
    out = [
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="darrow" '
        f'marker-end="url(#{mid})"/>'
    ]
    if label:
        out.append(
            f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 6}" class="dedge">{label}</text>'
        )
    return "".join(out)


# --------------------------------------------------------------------------- #
# §0 — the overview cycle. The ONLY place the whole system appears at once.
# --------------------------------------------------------------------------- #

#: (stage label, section slug, description lines, measurement key, box tone).
#:
#: 🔴 The fourth field is a KEY, never a number. The diagram prints whatever the
#: measured row holds, so a stage cannot drift away from the section it links to
#: — and a key no measurer produces renders UNMEASURED rather than blank.
#: `test_present_content.py` pins every slug against a real section and every key
#: against the live registry, both directions.
OVERVIEW_STAGES: tuple[tuple[str, str, str, str, str], ...] = (
    ("TOLD", "told",
     "rules &#183; skills &#183; memory|all three CAPPED, paid every session",
     "skills.listing", "a"),
    ("MAY DO", "may-do",
     "hooks that can REFUSE a call|shipped and registered differ",
     "hooks", "a"),
    ("VERIFIED", "verified",
     "two gate tiers, running in|two different environments",
     "gate.tiers", "b"),
    ("SHIPS", "ships",
     "a switch, per host &#8212; a merge|deploys precisely nothing",
     "ship.managed", "b"),
    ("DRIFT", "drift",
     "a passive deadman on a timer|it reports, and never fixes",
     "drift.ladder", "d"),
    ("OBSERVED", "observed",
     "telemetry &#183; recall store|&#183; live session surfaces",
     "telemetry.sources", "c"),
)

#: Box geometry, laid out as a closed racetrack: three across the top going
#: right, down the right edge, three across the bottom coming back left, up the
#: left edge to the start. A true hexagon reads worse at this text density.
_OV_W, _OV_H = 260, 104
_OV_POS = ((20, 30), (330, 30), (640, 30), (640, 250), (330, 250), (20, 250))


def _wrap(text: str, width: int, maxlines: int = 2) -> list[str]:
    """Greedy word wrap. The last line absorbs any overflow rather than losing it.

    Truncating here would silently shorten a measured value, which is the same
    class of failure as rendering it blank.
    """
    lines: list[str] = []
    cur = ""
    for word in str(text).split():
        cand = f"{cur} {word}".strip()
        if cur and len(cand) > width and len(lines) < maxlines - 1:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def _stage_count(ms, key: str):
    """-> (lines, unmeasured, reason). NEVER a blank, a dash or a zero.

    Three distinct outcomes, and all three are loud:
      * measured        -> the row's OWN value, wrapped
      * unmeasured      -> the word UNMEASURED, plus the row's reason
      * no such key     -> the word UNMEASURED, plus a GENERATOR-DEFECT reason

    The third is the one that would otherwise vanish: a stage pointing at a key
    nobody produces has no row to be absent from, so if the diagram fell back to
    empty text the picture would read as complete.
    """
    m = ms.by_key(key) if ms is not None else None
    if m is None:
        return (["UNMEASURED"], True,
                f"no measurer produces {key!r} — a generator defect, "
                "rendered rather than hidden")
    if not m.measured:
        return (["UNMEASURED"], True,
                m.reason or "no reason recorded — which is itself a defect")
    # 38 characters is what fits one line of `.dcount` inside a 260-unit box with
    # margin. A longer value wraps to a second line rather than being truncated.
    return (_wrap(m.value, 38, 2), False, "")


def diagram_overview(ms) -> str:
    """The whole system as one closed cycle, with live counts, as navigation.

    Each stage is an `<a href="#slug">`: this figure IS the page's primary
    navigation, which is why it must stay legible rather than shrink to fit. The
    CSS gives it a `min-width` inside a scrolling container, so a narrow viewport
    scrolls the FIGURE and never the page body.
    """
    mid = "ar-ov"
    # 🔴 NOT role="img". An `img` role collapses the whole figure into one opaque
    # image for assistive tech, which would hide the six stage LINKS inside it —
    # and this diagram is the page's primary navigation. `role="group"` keeps the
    # accessible name while leaving the links reachable.
    parts = [_SVG_HEAD.format(w=920, h=384, alt=(
        "The loop a change travels, drawn as a closed cycle: what the agent is "
        "told, what it may do, how work is verified, how it ships, how drift is "
        "caught, what is observed, and back to what the agent is told. Each "
        "stage carries its live measured count and links to its section."
    ), mid=mid)
        .replace('role="img"', 'role="group"')
        .replace('class="diagram"', 'class="diagram overview"')]

    for i, (label, slug, sub, key, tone) in enumerate(OVERVIEW_STAGES):
        x, y = _OV_POS[i]
        cx = x + _OV_W / 2
        lines, un, reason = _stage_count(ms, key)
        tip = f"{label} — {'UNMEASURED: ' + reason if un else 'open this section'}"
        parts.append(f'<a href="#{slug}"><title>{html.escape(tip)}</title>')
        parts.append(_box(x, y, _OV_W, _OV_H, label, sub, "warn" if un else tone))
        cls = "dcount dcount-un" if un else "dcount"
        # One line sits low and centred; two lines start higher so the second
        # never rides the box's bottom edge. An UNMEASURED stage always takes the
        # two-line form: the word, and where its reason lives.
        top = y + (82 if (len(lines) == 1 and not un) else 76)
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{cx}" y="{top + j * 14}" class="{cls}">'
                f'{html.escape(line)}</text>'
            )
        if un:
            parts.append(
                f'<text x="{cx}" y="{y + 92}" class="dnote">'
                'why not &#8212; open this section</text>'
            )
        parts.append("</a>")

    parts.append(_arrow(284, 82, 326, 82, mid))
    parts.append(_arrow(594, 82, 636, 82, mid))
    parts.append(_arrow(770, 138, 770, 246, mid))
    parts.append(_arrow(636, 302, 594, 302, mid))
    parts.append(_arrow(326, 302, 284, 302, mid))
    parts.append(_arrow(150, 246, 150, 138, mid))

    parts.append('<text x="460" y="166" class="dctr">THE LOOP A CHANGE TRAVELS</text>')
    parts.append('<text x="460" y="190" class="dnote">what drift finds becomes a RULE,</text>')
    parts.append('<text x="460" y="206" class="dnote">and a rule costs always-on budget &#8212;</text>')
    parts.append('<text x="460" y="222" class="dnote">which is why the loop has a ceiling.</text>')
    parts.append("</svg>")
    return f'<div class="ovwrap">{"".join(parts)}</div>'


def diagram_tiers() -> str:
    """Two gate tiers, and what each is structurally blind to."""
    mid = "ar-tiers"
    parts = [_SVG_HEAD.format(w=920, h=290, alt=(
        "The two gate tiers: the dev-host tier runs the runners in a real git "
        "checkout; the sandbox tier builds from a store copy with no .git and is "
        "the one the merge is gated on. Each is blind to what the other sees."
    ), mid=mid)]
    parts.append('<text x="20" y="99" class="dhead">one change</text>')
    parts.append(_arrow(105, 95, 200, 95, mid))

    parts.append(_box(210, 46, 330, 96, "DEV-HOST TIER", (
        "scripts/gate.sh --tier both|a real git checkout, your PATH|"
        "never invokes nix build"), "b"))
    parts.append('<text x="375" y="162" class="dnote">BLIND TO: anything the</text>'
                 '<text x="375" y="178" class="dnote">sandbox lacks &#8212; no .git,</text>'
                 '<text x="375" y="194" class="dnote">no network, pinned python</text>')

    parts.append(_box(570, 46, 330, 96, "SANDBOX TIER", (
        "nix build .#checks&#8230;{pytests,nodetests}|a cp -r store copy, NO .git|"
        "this is what CI runs"), "d"))
    parts.append('<text x="735" y="162" class="dnote">BLIND TO: anything keyed on</text>'
                 '<text x="735" y="178" class="dnote">the dev host &#8212; an ambient</text>'
                 '<text x="735" y="194" class="dnote">tool, a repo-local git config</text>')

    parts.append(_arrow(540, 94, 566, 94, mid))
    parts.append('<rect x="210" y="222" width="690" height="46" rx="7" class="dbox dbox-warn"/>')
    parts.append('<text x="555" y="242" class="dlabel">GREEN ON BOTH TIERS = one claim, about one BASE SHA</text>')
    parts.append('<text x="555" y="259" class="dsub">the tree a merge CREATES was never run &#8212; gating that is still yours to do by hand</text>')
    parts.append("</svg>")
    return "".join(parts)


def diagram_budget() -> str:
    """Where always-on context goes, and what is free."""
    mid = "ar-budget"
    parts = [_SVG_HEAD.format(w=920, h=250, alt=(
        "Three always-on context costs — the rules file, the skill listing and "
        "the memory index — against the surfaces that cost nothing until they "
        "are asked for: skill bodies, reference files and the index store."
    ), mid=mid)]
    parts.append('<text x="20" y="22" class="dhead">PAID EVERY SESSION, before you type anything</text>')
    parts.append(_box(20, 36, 270, 62, "RULES.md", "test-enforced BYTE ceiling|evicts to RULES-ARCHIVE.md", "a"))
    parts.append(_box(310, 36, 270, 62, "the SKILL LISTING", "name + description, per skill|a fraction of the window", "a"))
    parts.append(_box(600, 36, 300, 62, "MEMORY.md", "hard cap &#8212; overflow is DROPPED|silently, with no error", "a"))

    parts.append('<text x="20" y="140" class="dhead">FREE until something asks for it</text>')
    parts.append(_box(20, 154, 270, 62, "skill BODIES", "loaded on trigger only|so detail belongs here", "c"))
    parts.append(_box(310, 154, 270, 62, "reference/ + flows/", "durable facts &#183; procedures|nothing auto-fires them", "c"))
    parts.append(_box(600, 154, 300, 62, "the INDEX STORE", "recalled on demand, per scope|local state, not in the repo", "c"))
    parts.append('<text x="20" y="238" class="dnote dnote-l">Every instruction is a bid for space in the top row, '
                 'and the top row is the only thing that is scarce.</text>')
    parts.append("</svg>")
    return "".join(parts)


#: Static diagrams: mechanisms only, no values.
DIAGRAMS = {
    "tiers": diagram_tiers,
    "budget": diagram_budget,
}

#: Measurement-aware diagrams: handed the MeasurementSet, print ITS values.
LIVE_DIAGRAMS = {
    "overview": diagram_overview,
}


# --------------------------------------------------------------------------- #
# The spine
# --------------------------------------------------------------------------- #

SECTIONS: tuple[Section, ...] = (
    Section(
        slug="overview", number="0", title="Overview — the whole loop",
        lede="A NixOS dotfiles repo that grew an agent-operations layer: one operator works "
             "almost entirely through coding agents, so what is engineered here is the "
             "agent's environment. Every stage below carries its live count and is a link.",
        blocks=(
            ("svgm", "overview"),
            ("note", ("why", "One fact explains most of the design", (
                "<b>Instructions cost context on every session, and context is finite.</b> "
                "The byte ceilings, the tier ledger, the eviction rule and the refusal to "
                "restate a number all fall out of that."))),
            ("unbanner", None),
        ),
    ),

    Section(
        slug="how-to-read", number="1", title="How to read this page",
        lede="What a row here is, and is not, claiming.",
        blocks=(
            ("ul", [
                "<b>Every number was measured at build time</b> by "
                "<code>scripts/present/measure.py</code>. None was typed by hand.",
                "<b>The stamp is per row.</b> A byte count ages in minutes, a timer roster "
                "in days; one footer date would claim the same freshness for both.",
                "<b>An absence is reported as an absence</b> &mdash; "
                "<span class=\"pill pill-un\">UNMEASURED</span>, with its reason and the "
                "command that settles it. An omitted row is byte-identical to one that "
                "measured clean.",
                "<b>A green check is a claim about one tier and one base sha</b>, and this "
                "page names which.",
                "<b>Prose names mechanisms; only measured rows carry live values.</b> A test "
                "greps the live registry's numbers against this page's text. Historical "
                "numbers &mdash; the measurement that killed a proposal &mdash; are in "
                "bounds; they cannot go stale.",
            ]),
            ("note", ("why", "Why a generator instead of a document", (
                "This repo's own <code>CLAUDE.md</code> carried one claim in two opposite, "
                "both-wrong directions: the merge gate read <i>“CI gates both suites”</i>, "
                "and later <i>“NO AUTOMATED GATE IS RUNNING”</i>. Neither was true when "
                "read. A page that restates numbers inherits that decay; one that "
                "re-measures them cannot. The cost is one-sided and real: a stale copy is "
                "stale in a way the reader can only see from the per-row stamps."))),
            ("measure", "repo.head"),
        ),
    ),

    Section(
        slug="start-here", number="2", title="Start here",
        lede="Four entry paths, by what you came to do. They lead to different failure "
             "modes, which is the point.",
        blocks=(
            ("cards", [
                ("“Add a skill or a rule.”",
                 "<a href=\"#told\">§3</a>, then <a href=\"#cost\">§10</a>. Both are capped "
                 "surfaces: an addition needs an eviction in the same commit, and a new "
                 "skill needs a tier-ledger entry or the suite goes red."),
                ("“Change some code.”",
                 "<a href=\"#verified\">§5</a> and <a href=\"#invariants\">§9</a>. Two gate "
                 "tiers in two environments &mdash; and merged is not deployed."),
                ("“Something is stale on a host.”",
                 "<a href=\"#ships\">§6</a> and <a href=\"#drift\">§7</a>. Git, host and "
                 "source parity are independent; a machine can be perfect on one and "
                 "silently broken on another."),
                ("“Propose an improvement.”",
                 "<a href=\"#negative\">§11</a> first, then <a href=\"#evidence\">§12</a>. "
                 "§11 is what was already killed, each with the measurement that killed it."),
            ]),
        ),
    ),

    Section(
        slug="told", number="3", title="What the agent is told",
        lede="Three surfaces load into every session before the operator types anything. All "
             "three are capped, and the caps are the most explanatory fact about the design.",
        blocks=(
            ("p", (
                "Instructions are paid on <b>every session</b>, across two machines and "
                "several concurrent agents. So the question is never “is this instruction "
                "correct?” but “is it worth what it costs, forever, against what it "
                "displaces?”")),
            ("svg", "budget"),
            ("kv", [
                ("The rules file &mdash; <i>paid twice</i>",
                 "Imperatives and the verification standard, charged once by the harness and "
                 "again because the nix build concatenates it into the other agent runner's "
                 "instruction file. It once grew 2.9&times; in three days by a process where "
                 "every commit was individually correct and nothing asked what the file "
                 "cost. A test now owns its ceiling."),
                ("The skill listing &mdash; <i>it fails silently</i>",
                 "A skill body costs nothing until its trigger fires, but every name and "
                 "description loads every session under a fraction of the context window. On "
                 "overflow the harness <b>drops descriptions, least-invoked first, with no "
                 "error</b> &mdash; so the skills least able to afford weak routing lose it "
                 "first. A description is <b>routing surface, not documentation</b>."),
                ("The memory index &mdash; <i>a hard cap</i>",
                 "Durable lessons, with topic files behind it that cost nothing until "
                 "recalled. <b>Content past the cap is dropped on load, silently</b> &mdash; "
                 "the index does not error, it carries less than it says. The usual cause is "
                 "status creep: “shipped”, “deployed”, “soaking”."),
            ]),
            ("measure", "rules.bytes"),
            ("measure", "rules.archive"),
            ("note", ("hazard", "Never satisfy a ceiling by narrowing a rule", (
                "One prohibition had to be <i>re-broadened</i> after a narrow wording let an "
                "agent walk into the failure it forbade. <b>Evict the evidence, never the "
                "scope</b> &mdash; and raising a ceiling costs a commit message naming the "
                "rule that would not fit."))),
            ("measure", "skills.listing"),
            ("note", ("hazard", "Tier by symptom, never by an invocation counter", (
                "Tier A carries a full description and auto-fires from a described symptom; "
                "tier B is name-only. Two skills here are demonstrably live with a recorded "
                "count of <i>zero</i>, because the counter cannot see a skill running as a "
                "systemd service. Tie-break: tier B when a mis-route <b>degrades the "
                "answer</b>, tier A when it <b>takes a wrong action</b>."))),
            ("measure", "memory.index"),
            ("p", (
                "The inventory is shallow on purpose &mdash; name, tier, the skill's own "
                "first sentence, path. <b>The skill is the operating surface; this page "
                "routes to it.</b> A procedure restated here would be a fourth documentation "
                "surface, and the fourth copy goes stale first.")),
            ("measure", "skills.inventory"),
        ),
    ),

    Section(
        slug="may-do", number="4", title="What the agent may do",
        lede="Hooks that can refuse a tool call, and integrations that act on the world.",
        blocks=(
            ("p", (
                "Prose is the weak form of control &mdash; the rules file says so itself. "
                "The strong form is a <b>hook</b>: a program the harness runs before a tool "
                "call, whose exit code can block it. The blocking one refuses blind staging "
                "(<code>git add -A</code>), hard resets, <code>git stash</code> (the stash "
                "stack is repository-global, so it reaches into other worktrees), oversized "
                "heredocs, and publishing a secret or a public IP into a public repo.")),
            ("note", ("hazard", "A hook that only nudges is not a guard", (
                "Most hooks here emit advice and exit zero. Legitimate &mdash; one nudge "
                "moved a behaviour from 0% to 50% adoption &mdash; but <b>not</b> a control. "
                "<b>Read the exit code before calling something a guard.</b>"))),
            ("note", ("hazard", "Shipped and registered are independent facts", (
                "The repo ships hook scripts; a <i>host</i> registers them in a per-host, "
                "deliberately unmanaged settings file. A hook can be present, correct, fully "
                "tested, and firing on neither machine &mdash; hence two counts below."))),
            ("measure", "hooks"),
            ("note", ("why", "What generalises across the integrations", (
                "An approval UI turning a permission prompt into a phone notification, a "
                "ticket system, a bridge driving the operator's real logged-in browser, a "
                "download router &mdash; each operated through its own skill (see "
                "<a href=\"#told\">§3</a>). These are the surfaces where a wrong action is "
                "not recoverable by editing a file, so work that leaves the machine, cannot "
                "be undone, or moves many things at once is flagged <i>before</i> it "
                "happens &mdash; and an approval covers only the step it was given for."))),
        ),
    ),

    Section(
        slug="verified", number="5", title="How work is verified",
        lede="Two gate tiers running the same suites in two environments. Treating them as "
             "two spellings of one thing has already cost a required check.",
        blocks=(
            ("svg", "tiers"),
            ("p", (
                "The diagram is the whole warning. Four consecutive green dev-host runs on "
                "one pull request were followed by a red sandbox check, because the sandbox "
                "tier had simply never been run.")),
            ("measure", "gate.tiers"),
            ("p", (
                "Whether a check <i>runs</i> and whether it <i>blocks</i> are different "
                "facts, and the repo's machine-checked marker sees only the first. One day "
                "here exactly one of the two required contexts was listed &mdash; and it "
                "collected only the JavaScript tests, so a Python-only change could not fail "
                "it and read as mergeable with the Python suite red. <b>Check the list, not "
                "that the key exists.</b>")),
            ("measure", "gate.protection"),
            ("measure", "gate.hooks_installed"),
            ("p", (
                "Both runners assert a <b>collected-test floor</b> and parse structured "
                "output instead of reading an exit status, because a collection error or an "
                "empty glob can produce “zero tests” with a zero exit. There is no "
                "hand-written total: the global floor is the sum of per-target floors, each "
                "a function of a measurement. The literal it replaced took eleven values "
                "across eight pull requests in one day.")),
            ("measure", "tests.pytest"),
            ("measure", "tests.node"),
            ("h3", "The evidence bar for a test"),
            ("p", (
                "Stricter than “the suite is green”. The vocabulary &mdash; <i>invariant "
                "guard</i>, <i>seam guard</i>, <i>positive control</i> &mdash; is defined in "
                "<a href=\"#glossary\">§14</a>; these are the obligations.")),
            ("ul", [
                "<b>Show a regression test FAIL on pre-change code</b> and report the "
                "matrix: red at the base ref, green at HEAD. A test pinning an invariant the "
                "bug never violated is an invariant guard &mdash; say so, and do not count "
                "it as regression coverage.",
                "<b>Mutation-test a guard, and prove it REACHABLE.</b> A mutation still "
                "passes when an earlier check always wins, or when a <i>different</i> "
                "guard's error kills your test.",
                "<b>Validate the instrument before reading its verdict</b> &mdash; both "
                "controls, and report the pair. A reassuring zero is indistinguishable from "
                "a harness wired to nothing.",
                "<b>“Verified in isolation” is the new vacuous green.</b> Two components, "
                "each hermetically tested, can be broken together because no test ever built "
                "the combined state. Ask which surface your fixture does <i>not</i> load.",
                "<b>Never derive an expectation from the implementation it tests.</b> "
                "Stubbing one function to a no-op once left thirty-one integration tests "
                "green.",
            ]),
        ),
    ),

    Section(
        slug="ships", number="6", title="How it ships",
        lede="Two machines converged by one idempotent script. The trap: a merge changes "
             "nothing a package manager owns.",
        blocks=(
            ("p", (
                "A nix flake describes what each host should have; applying it is a "
                "<i>switch</i>. One convergence script fetches, fast-forwards, switches and "
                "verifies both hosts. It never stashes, and a host it cannot fast-forward "
                "is <b>skipped and left exactly as found</b>, with the blocking files "
                "named.")),
            ("note", ("hazard", "Merged is not deployed", (
                "Every managed path changes only on a switch. <code>git pull</code> changes "
                "nothing nix manages &mdash; <b>deliberately</b>, so a concurrent session's "
                "<code>git checkout</code> cannot swap deployed code out from under a "
                "verification in progress. It is also exactly what makes it easy to trip "
                "on. Merge &rarr; pull &rarr; switch &rarr; restart the consumer; skip the "
                "last two and you will verify the old artefact and report it as new."))),
            ("note", ("hazard", "The skip is the dangerous outcome, not the failure", (
                "A skipped host keeps looking healthy &mdash; same commits, same green "
                "generation, no error &mdash; while receiving nothing. That has happened "
                "more than once, and each time the only detector was a human shipping "
                "something unrelated and reading the per-host lines. <b>Read every per-host "
                "line, not the final verdict.</b>"))),
            ("measure", "ship.managed"),
            ("note", ("why", "readlink is the only arbiter of live-versus-stale", (
                "Some managed paths are store copies (editing the repo does nothing until a "
                "switch); others are out-of-store symlinks (the working copy <i>is</i> the "
                "live file). Resolve the symlink &mdash; never diff the file against the "
                "repo. Byte-identical proves nothing: identity can mean they are one "
                "file."))),
        ),
    ),

    Section(
        slug="drift", number="7", title="How drift is caught",
        lede="A passive deadman on a timer, because nothing runs the deploy on a schedule. "
             "It reports and never fixes.",
        blocks=(
            ("p", (
                "The convergence script is correct and not enough, because nothing invokes "
                "it automatically. A separate checker runs unattended and asks one "
                "question: <i>is either host silently no longer receiving changes?</i> It "
                "may fetch; a static allowlist scanner in its own suite proves it can run "
                "no mutating git subcommand, including through an ssh hop. <b>A deadman "
                "that repairs is a deployer with no supervision.</b>")),
            ("kv", [
                ("Git parity",
                 "Is the checkout still tracking the main branch? The obvious one, and for "
                 "a long time the only one asked."),
                ("Host parity",
                 "Is what the checkout describes actually <i>deployed</i>, and the same on "
                 "both machines? Every skill on one host was once a dangling symlink into a "
                 "garbage-collected store path while the checkout was byte-identical to "
                 "origin. Perfect git parity, zero host parity, checker green."),
                ("Source parity",
                 "Some packages build from a working tree of <i>another</i> repository, and "
                 "nothing converges those. One host shipped a binary missing two "
                 "subcommands while wearing the version label of one that had them; the "
                 "command printed help and exited zero."),
            ]),
            ("note", ("hazard", "Why UNMEASURED grew its own escalation", (
                "Setting no exit code for “we could not look” is right per run and wrong "
                "forever: a scope that could never be evaluated escalated <b>never</b>, so "
                "the run read clean while the check that should have fired was structurally "
                "unable to. It now rides a consecutive-run ladder, per host and per scope, "
                "reset the moment it measures. <b>This is the idea this page borrowed most "
                "directly.</b>"))),
            ("note", ("why", "Two designs worth stealing from the exit-code ladder", (
                "An “actionable, not drift” code that is the <i>least</i> severe the checker "
                "owns &mdash; and a <i>success</i> to the service manager, since a code that "
                "stays set until someone does a cleanup would otherwise fire a failure "
                "notification several times a day forever. And an adopted-then-drifted-only "
                "arm: a host that never adopted a mechanism prints NOT ADOPTED and sets no "
                "code. <b>A permanently-red gate is worse than no gate.</b>"))),
            ("measure", "drift.ladder"),
            ("measure", "timers"),
        ),
    ),

    Section(
        slug="observed", number="8", title="What is observed",
        lede="Three surfaces with different lifetimes: a telemetry pipeline, a recall store, "
             "and the live session views.",
        blocks=(
            ("p", (
                "<b>Activity telemetry.</b> Shell, multiplexer, keyboard, window manager, "
                "browser and agent transcripts feed a collector that lands events in a "
                "columnar database, with a per-source deadman so a source that stops is "
                "noticed rather than assumed quiet. It is what makes “this tool is dead” "
                "measurable instead of impressionistic.")),
            ("measure", "telemetry.sources"),
            ("p", (
                "<b>The subsystem index store.</b> What a past session learned, keyed by "
                "scope, recalled on demand. It costs nothing per session &mdash; precisely "
                "why it can be large where the rules file cannot. It is local state, not in "
                "this repository, so the row below describes the machine that built this "
                "page.")),
            ("measure", "index.store"),
            ("p", (
                "<b>Session surfaces.</b> A cross-host view of every terminal window: which "
                "have an agent running, and &mdash; the one that pays &mdash; which are "
                "<i>waiting on a human</i>. That got its own tool after two windows were "
                "measured sitting unanswered for days. See <a href=\"#soft\">§13</a> for "
                "how many surfaces now answer overlapping versions of it.")),
        ),
    ),

    Section(
        slug="invariants", number="9", title="Invariants and tripwires",
        lede="What a change must not break. Most are enforced by a test; where one is not, "
             "it says so.",
        blocks=(
            ("kv", [
                ("Adding a skill costs an eviction, in the same commit",
                 "The listing total is ratcheted by a test that owns the constant. "
                 "<span class=\"tag\">enforced</span>"),
                ("A new skill needs a tier-ledger entry",
                 "Pinned two-way: a shipped skill with no entry fails, an entry naming no "
                 "skill fails. <span class=\"tag\">enforced</span>"),
                ("The rules file has a byte ceiling",
                 "Owned by its test, which prints the eviction playbook on failure. Raising "
                 "it costs a commit message naming the rule that would not fit. "
                 "<span class=\"tag\">enforced</span>"),
                ("Both gate tiers must pass",
                 "Different environments; green on one says nothing about the other. "
                 "<span class=\"tag\">enforced at merge</span> "
                 "<span class=\"tag tag-soft\">not enforced locally</span>"),
                ("A new file must be <code>git add</code>ed",
                 "The flake builds from tracked files, so an untracked skill, hook or test "
                 "is silently omitted from the deploy &mdash; the switch succeeds and the "
                 "file is not there. "
                 "<span class=\"tag tag-soft\">not enforced &mdash; the loudest silent failure here</span>"),
                ("Worktree isolation for parallel file-modifying agents",
                 "Two agents modifying files in one checkout <b>will</b> clobber each "
                 "other. A worktree isolates a working directory only &mdash; not the repo "
                 "it was built from, the branch namespace, the gitignored environment file, "
                 "submodules, or a copy you make of it. "
                 "<span class=\"tag tag-soft\">convention</span>"),
                ("Never <code>git stash</code>",
                 "The stash ref lives in the common git directory, so your own worktree "
                 "gives you zero isolation and a concurrent agent can pop your stash. "
                 "<span class=\"tag\">hook-blocked</span>"),
                ("Never <code>git add -A</code>, never <code>git reset --hard</code>",
                 "Blind staging leaks unrelated work and secrets from a dirty tree; a hard "
                 "reset irreversibly destroys uncommitted work. "
                 "<span class=\"tag\">hook-blocked</span>"),
                ("Merged is not deployed",
                 "Every managed path changes only on a switch. "
                 "<span class=\"tag tag-soft\">not enforced &mdash; see <a href=\"#ships\">§6</a></span>"),
                ("Never commit to main in either host checkout",
                 "The convergence script fast-forwards only, so a diverged host is skipped "
                 "and then silently receives nothing while looking healthy. "
                 "<span class=\"tag\">deadman-detected</span>"),
                ("This repository is public",
                 "No real media path, client identifier, third-party hostname, IP literal, "
                 "or captured text, however it arrives. A test needs the <i>shape</i>; "
                 "regenerate it synthetic. "
                 "<span class=\"tag\">enforced by four content gates</span> "
                 "<span class=\"tag tag-soft\">all blind to git history</span>"),
            ]),
        ),
    ),

    Section(
        slug="cost", number="10", title="The cost model of a change",
        lede="What it costs to add something here — not to build it, to keep it. Four "
             "clocks, only the first of which anyone estimates.",
        blocks=(
            ("kv", [
                ("Per-session context",
                 "Paid every session, forever, by every agent on both hosts. The scarcest "
                 "budget, and the only one where an addition <i>displaces</i> something "
                 "rather than adding to it."),
                ("Eviction pressure",
                 "The honest question is not “does this rule earn its bytes” but “does it "
                 "earn them more than the least valuable rule already there”. Once eviction "
                 "is complete, the next rule costs a ceiling raise &mdash; which costs a "
                 "justification."),
                ("Gate time, on every merge, forever",
                 "A new suite is a floor entry, a two-way pin, and wall-clock time on both "
                 "tiers for every future pull request. A suite is not free because it "
                 "passes."),
                ("Surface area for the next false claim",
                 "Every documented fact can go stale, and stale facts here have measurably "
                 "sent sessions in the wrong direction. Hence “write it in the test that "
                 "owns the constant”, and quote a number from exactly one place."),
            ]),
            ("note", ("why", "The shape of a good addition", (
                "Cheap when idle, loud when it matters, owning its own number. A skill body "
                "costs nothing until triggered. A test that owns a constant turns every "
                "other mention into a cross-reference instead of a copy. A gate that prints "
                "the replacement value on failure removes the arithmetic where mistakes "
                "live. The expensive shape is the opposite: a paragraph, loaded always, "
                "restating a number owned elsewhere."))),
            ("note", ("hazard", "Consolidation is a bug-finding instrument, not hygiene", (
                "A predicate open-coded at N sites is typically wrong at N&minus;1 of them, "
                "in the same direction, and unifying them is what makes the disagreement "
                "audible. One predicate here was re-fixed five times and only held once "
                "consolidated. If you are patching the second copy, that is the signal."))),
        ),
    ),

    Section(
        slug="negative", number="11", title="Negative space — what was tried and rejected",
        lede="Each was proposed, built or nearly built, and killed by a specific "
             "measurement. The highest-yield section on the page: it stops you re-proposing "
             "a settled question.",
        blocks=(
            ("note", ("why", "Read this before proposing anything", (
                "None was rejected on taste. Each has a number attached, and in two cases "
                "the number falsified the proposal's <i>own core claim</i>. Where a "
                "rejection has since been superseded, that is recorded too &mdash; a "
                "negative-space list that only grows is a list nobody trusts."))),
            ("h3", "A generalized “subsystem” store"),
            ("p", (
                "<b>Proposed:</b> generalize a per-service recall index into a universal "
                "store covering anything durable, with a multi-verb CRUD, a "
                "<code>type:</code> field driving per-class behaviour, and a dependency "
                "graph.")),
            ("p", (
                "<b>Killed by the corpus, after eight weeks of real use.</b> Twenty index "
                "entries; <b>zero</b> of a non-infra type; <b>one</b> distinct scope in "
                "use; <b>zero</b> dependency edges populated. So <code>type:</code> &mdash; "
                "“the mechanism that makes generality real” &mdash; would have had exactly "
                "one value, and the graph, “the payoff grep cannot produce”, no edges. A "
                "hand-authored strain test confirmed it: addressing collided at n=1. "
                "<b>The proposal's two core claims measured themselves false.</b>")),
            ("note", ("why", "Then the reopen gate tripped — the better lesson", (
                "The rejection shipped with a <i>falsifiable reopen gate</i> (“five entries "
                "outside the single scope, or five non-infra entries”). Two days later it "
                "tripped: twenty-nine entries across five scopes. The document marks itself "
                "superseded and says the gate was <b>the wrong question</b> &mdash; its “no "
                "demand” premise was circular, because the only writer at the time was an "
                "infra-recon command pointed at two cluster repos, so only infra entries in "
                "one scope <i>could</i> exist. The three narrow rejections still stand on "
                "independent evidence. <b>Attach a falsifiable reopen condition to a "
                "rejection, and be ready for it to teach you that your condition was "
                "measuring your own sampling.</b>"))),
            ("h3", "A <code>--rearm</code> flag for the writeback guard"),
            ("p", (
                "<b>Proposed:</b> let an operator undo a dismissal, so a guard told “this "
                "work was not for that ticket” could resume nagging in the same session. "
                "<b>Refused, after two escapes were measured failing in production.</b> The "
                "first &mdash; “say so in one line and stop” &mdash; did not work, because "
                "saying something changes no state and the next stop re-blocked with "
                "identical text. The second, a dismissal that cleared the guard's state, "
                "failed <i>twice</i>: clearing the state restored the session to its "
                "pre-read condition, so the next read of the ticket re-armed the guard. The "
                "ledger timestamps show the re-arm landing <b>90 milliseconds later, inside "
                "the same tool call</b>. Three audit rounds missed it, because every test "
                "drove the dismissal and asserted silence &mdash; none read the ticket "
                "again afterwards.")),
            ("p", (
                "The shipped answer is an <b>absolute session tombstone with a named false "
                "negative</b>. The source says why: <i>“the alternative … is speculative "
                "complexity inventing an intention nobody stated. There is deliberately no "
                "<code>--rearm</code> flag: the escape from a dismissal is a new session, "
                "which costs nothing and is unambiguous.”</i>")),
            ("h3", "A per-scope fallback to the hosted store"),
            ("p", (
                "<b>Proposed:</b> when a recall finds a scope absent or empty locally, fall "
                "back to the hosted copy. <b>Killed by granularity.</b> Re-deriving all "
                "three copies twice showed the scope-granular trigger would reach <b>9 of "
                "70 entries &mdash; 13%</b>. On one host it is a strict no-op; on the other "
                "it never fires at all, because every scope there is non-empty &mdash; "
                "while <b>61 entries in the four shared scopes stay invisible</b>, which is "
                "precisely where that host holds one-entry stubs against six to "
                "thirty-six. The killing sentence: <i>“the loss is at entry granularity; a "
                "scope-granular trigger cannot see it.”</i> <b>Match the granularity of the "
                "trigger to the granularity of the loss.</b>")),
            ("note", ("why", "A second finding from the same investigation", (
                "The snapshot header's <code>entry-files</code> count was <i>not</i> the "
                "entry count &mdash; both the seeder and the server counted every markdown "
                "file, including one index per scope, and quoting the header had made the "
                "hosted copy look like a superset of the store it mirrors. Separately, an "
                "entire ratio argument was <b>deleted outright</b>, because every audit "
                "round but the first found its new defect inside that one paragraph: "
                "<i>“a number nobody needs is a place for the next error to live.”</i>"))),
            ("h3", "The skill-tier ledger, applied to zero hosts"),
            ("p", (
                "<b>Built end to end, then deliberately not switched on.</b> Ledger, sync "
                "script, two-way test pin, drift-check arm &mdash; all shipped. The "
                "measurement that stopped it being <i>applied</i> killed the proposal's own "
                "strongest claim: an earlier draft asserted “the truncation is already "
                "happening; tiering only makes an accidental loss deliberate”, and against "
                "the <i>large</i> context window the listing measured comfortably inside "
                "budget. <b>It does not fit the small window and cannot be made to</b> "
                "&mdash; the fit is a claim about one window size, exactly the qualifier "
                "the draft had dropped.")),
            ("p", (
                "So the sync script defaults to dry-run, the ledger is deployed nowhere, "
                "and the drift checker prints NOT ADOPTED with no exit code &mdash; “the "
                "state the mechanism shipped in, not drift”. The conservatism is pinned as "
                "a <i>property</i>: a test asserts the name-only set stays a minority, so a "
                "majority-name-only listing has to be argued at that assertion rather than "
                "arrived at one entry at a time. <b>Build the lever, measure that you do "
                "not need it yet, ship it unpulled behind a gate that will tell you when "
                "you do.</b>")),
            ("h3", "Four more, briefly"),
            ("ul", [
                "<b>Shrinking the telemetry table</b> &mdash; the maximum conceivable "
                "saving was about twenty megabytes against a hundred-and-twenty-gigabyte "
                "store, and it was the only option that lost data. <i>Size the prize before "
                "designing for it.</i>",
                "<b>A stale-directory reap estimated at tens of gigabytes</b> &mdash; "
                "self-retracted: the estimate multiplied a sample <i>mean</i> over a "
                "heavy-tailed distribution, the actual reap freed about 1.4 GiB, and the "
                "median was visible at the time. <i>Never extrapolate a mean over a heavy "
                "tail when the median is in front of you.</i>",
                "<b>A reported flake rate</b> &mdash; retracted: measured over a window the "
                "measuring session was itself saturating, 4% before the load burst and 24% "
                "during it. <i>A rate measured inside your own load is measuring you.</i>",
                "<b>“The runner can exit zero while printing FAIL”</b> &mdash; retracted, "
                "does not reproduce. The real defect was a status destroyed by a pipeline. "
                "<i>A false-green report is usually a status read through a pipe.</i>",
            ]),
        ),
    ),

    Section(
        slug="evidence", number="12", title="The evidence bar for a proposal",
        lede="Measure, then propose. Here is the worked example that teaches it, because the "
             "abstract version does not stick.",
        blocks=(
            ("p", (
                "One session produced <b>six recommendations</b> by tracing code across "
                "several subsystems. Every one was carefully reasoned. Then they were "
                "checked. <b>Four did not survive.</b>")),
            ("kv", [
                ("1. Close an auth gap on a route — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>two <code>curl</code> calls</b> with no credentials, one per "
                 "middleware. The result <i>inverted the premise</i>: the middleware the "
                 "recommendation trusted was a literal pass-through &mdash; a function "
                 "returning its argument &mdash; so the named route was not the open "
                 "surface; every route using that middleware was. The proposed fix would "
                 "also have broken the operator's own web UI."),
                ("2. Retire a legacy mode — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>reading the comment already in the source</b>: the mode had "
                 "been made opt-in days earlier and the runtime win was already banked. "
                 "(Its supporting evidence was also n=3, on a single day.)"),
                ("3. Add a cross-scope query — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>one <code>grep</code></b>. It already shipped, in both the "
                 "library and the HTTP layer, with a flag. The “gap identified” was a "
                 "reading error."),
                ("4. Ship a stamping feature or retract — <span class=\"pill pill-ok\">SURVIVED</span>",
                 "Settled by <b>one <code>grep</code></b> too &mdash; the sharper half of "
                 "the point: the same cheap check both kills and confirms."),
                ("5. Add a reconciliation signal — <span class=\"pill pill-na\">NOT FILED</span>",
                 "No check settled it because <b>there was nothing to check</b>: no evidence "
                 "was ever gathered that the two sources disagree. Rejected for having no "
                 "closing condition and for shipping “a new permanently-green gate”."),
                ("6. Make a guard's escalation resettable — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>reading a ten-line comment block</b> that already named and "
                 "priced the exact false negative the recommendation thought it had found. "
                 "(The <code>--rearm</code> refusal from <a href=\"#negative\">§11</a>.)"),
            ]),
            ("note", ("why", "The failure mode, in the document's own words", (
                "<i>“The failure mode was <b>tracing code and reporting the trace as a "
                "finding</b>: reading that a route is wrapped in a middleware without "
                "reading what that middleware does, and recommending a feature without "
                "grepping for its flag. Both are one command away.”</i>"))),
            ("ul", [
                "<b>Use a negative control.</b> The probe deliberately included a route "
                "known to reject, annotated as “the probe <i>can</i> see a rejection”. A "
                "success means nothing unless the same probe demonstrably fails somewhere.",
                "<b>An empty result is not evidence.</b> A query against the wrong "
                "repository slug returned an empty list &mdash; <i>byte-identical to "
                "“nothing was ever proposed”</i>. Take the slug from the remote URL, never "
                "the directory name.",
                "<b>A definition is not a mechanism.</b> Expect a definition <i>and</i> a "
                "call site; a definition alone is a dormant field. The first revision of "
                "the correction got this wrong by quoting a document instead of checking "
                "the code &mdash; the same failure, reproduced inside the correction.",
                "<b>Engage the existing counter-argument.</b> Re-proposing something whose "
                "refusal is written down, without engaging it, is a regression in the "
                "reasoning rather than a finding.",
                "<b>No closing condition, no work item.</b> A proposal that cannot name "
                "what would end it &mdash; a mechanical check, or a named human judgement "
                "over named evidence &mdash; is not a work item. Say so, and why, rather "
                "than mint an object nobody can close.",
            ]),
        ),
    ),

    Section(
        slug="soft", number="13", title="Where the system is soft",
        lede="Open seams, each with what would settle it. Analysis, not a backlog — by "
             "the local standard none of it becomes a work item without a closing condition.",
        blocks=(
            ("h3", "A complete subsystem with no reader &mdash; the seam that closed"),
            ("p", (
                "A hosted API over the recall store was built, tested and run in public. "
                "The consuming client was designed, <i>decided</i> &mdash; “hosted is an "
                "entry-level advisory, never the primary read” &mdash; and never written. "
                "No local reader ever contained an HTTP client; the only things that spoke "
                "to it were its own seeding and byte-identity scripts. It was <b>retired</b> "
                "once its audit log was read: every request it served came from the session "
                "that built it. <b>A subsystem can be complete, correct, well-tested and "
                "have no reader, with every gate green throughout.</b> The missing piece "
                "was never more server &mdash; it was a sync, which nothing had.")),
            ("measure", "seam.store_api"),
            ("h3", "Several detectors for one question"),
            ("p", (
                "Independent implementations answer “is an agent running in this pane”, and "
                "their predicates are <b>not equivalent</b>: a case-insensitive regex over "
                "the pane's command; the same regex plus a process-tree walk; exact string "
                "equality; and, in the multiplexer's own config, a prefix glob. An agent "
                "under a wrapper is invisible to the ones that do not walk the tree. The "
                "honest counterpoint: other nearby tools deliberately <i>refuse to "
                "re-derive</i> the predicate and say so in their headers. The seam is the "
                "ones that do.")),
            ("measure", "seam.detectors"),
            ("h3", "One question, several owners"),
            ("p", (
                "A constellation of session tools, each satellite existing because of what "
                "the primary does and does not expose. One satellite's header says it adds "
                "<i>“exactly two things … a clock and a threshold”</i>, because the primary "
                "computed a “waiting” field nothing consumed. Another records a measured "
                "refutation of its own brief: the view it was told to consume drops the "
                "join key it was told to join on. <b>What would settle it:</b> whether the "
                "coupling is to a command-line contract or a shared library, and whether "
                "one owner with two flags is smaller than several owners with several "
                "suites. Neither is measured here.")),
            ("measure", "seam.sessions"),
            ("h3", "Many walks over one corpus"),
            ("p", (
                "Independent places open the agent transcript corpus themselves, and "
                "<b>they do not agree on what that corpus is</b>: some walk recursively, "
                "some recursively while excluding the synthetic sub-agent directories, some "
                "only the top level. One subsystem's source documents the divergence and "
                "quantifies it in the thousands of files; a pair of walkers are "
                "near-duplicates re-implementing the same skip rule inline, held in sync by "
                "<b>a comment</b> saying they match. <b>What would settle it:</b> re-run "
                "the count before and after any consolidation. If it does not fall, the "
                "consolidation added a walker instead of removing several &mdash; and a "
                "shared walker silently picking one of the three definitions changes what "
                "at least one subsystem sees.")),
            ("measure", "seam.jsonl"),
        ),
    ),

    Section(
        slug="glossary", number="14", title="Glossary",
        lede="The local vocabulary is not guessable. These appear in commit messages, test "
             "names and skill bodies, and mean specific things here.",
        blocks=(
            ("kv", [
                ("the merged tree",
                 "The tree a merge <i>creates</i>, as opposed to either branch. A pull "
                 "request green on its own branch proves nothing about it: the second "
                 "change's reviewer ran before the first existed."),
                ("tier A / tier B",
                 "A skill's slot in the always-on listing. Tier A carries a full description "
                 "and auto-fires from a described symptom; tier B is name-only."),
                ("<code>UNMEASURED</code>",
                 "A first-class result meaning “this was not evaluated”, as distinct from "
                 "“this evaluated clean”."),
                ("<code>rc 17</code>",
                 "The drift checker's <i>source parity</i> code: the subtree a package is "
                 "built from is not current on that host. Counted against a path-limited "
                 "subtree &mdash; scoping it to the whole repository made it permanently red."),
                ("<code>scope-absent</code>",
                 "A recall condition meaning the requested scope is not in the local store. "
                 "Notable as a rejected trigger: the loss it was meant to cover lives at "
                 "<i>entry</i> granularity, which a scope-level condition cannot see."),
                ("positive / negative control",
                 "A positive control feeds an instrument a case that <i>must</i> produce a "
                 "non-zero result, to prove it can observe the thing at all; a negative "
                 "control feeds a case that must fail. If either misbehaves, the instrument "
                 "is testing nothing."),
                ("seam guard",
                 "A test pinning a <i>relationship</i> rather than a component: a ledger of "
                 "every writer or caller, failing when the set grows <i>or</i> shrinks, plus "
                 "a behavioural case &mdash; a structural check type-checks past a wrong "
                 "argument."),
                ("invariant guard",
                 "A test pinning a property the bug never violated. Legitimate, but "
                 "<b>not</b> regression coverage, and saying so is required."),
                ("clawgate",
                 "The self-hosted approval UI: an agent's permission prompt becomes "
                 "something the operator can approve remotely."),
                ("fuzzyclaw",
                 "A multiplexer task integration that writes session state to disk. Flagged "
                 "<b>untrusted as a data source</b> in this repo's own notes."),
                ("the base clone",
                 "The main checkout on a host, as opposed to the worktrees agents work in. "
                 "The worktrees do the committing, so it is effectively write-only and "
                 "silently falls behind; a fast-forward-only merge is the check, since it "
                 "either advances or refuses."),
                ("could-not-vouch",
                 "The gate wrapper's exit code for “this gate cannot vouch for its own "
                 "answer” &mdash; the exit status disagreeing with the printed verdict, or a "
                 "truncated run. Deliberately not the code for “the tests failed”."),
            ]),
        ),
    ),
)
