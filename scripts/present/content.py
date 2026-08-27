#!/usr/bin/env python3
"""The PROSE SPINE and the hand-authored diagrams.

🔴 NO PROSE HERE MAY RESTATE A NUMBER THE GENERATOR MEASURES. That is the exact
rule, it is narrower than "no quantities", and it is what
`scripts/tests/test_present_content.py` enforces: it takes every value the live
registry produces, pulls the standalone numeric tokens out of it, and fails if
one of them appears in the page's authored VISIBLE TEXT.

Historical quantities are deliberately IN BOUNDS — a killed proposal's
measurement, a dated incident's count. They are permanently true, they are not
what the generator measures, and stripping them would gut §10 (negative space)
and §11 (the evidence bar), whose whole job is to carry the number that settled
a question. What is out of bounds
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
# §0 — the three concepts. The ONLY place the whole system appears at once.
# --------------------------------------------------------------------------- #

#: (stage label, section slug, description lines, measurement key, box tone).
#:
#: 🔴 The fourth field is a KEY, never a number. The diagram prints whatever the
#: measured row holds, so a stage cannot drift away from the section it links to
#: — and a key no measurer produces renders UNMEASURED rather than blank.
#: `test_present_content.py` pins every slug against a real section and every key
#: against the live registry, both directions.
#:
#: The first three are the page's three PRIMARY CONCEPTS. The fourth is not a
#: fourth concept: it is the band of constraints all three sit inside, drawn
#: underneath them rather than beside them for exactly that reason.
OVERVIEW_STAGES: tuple[tuple[str, str, str, str, str], ...] = (
    ("SESSIONS", "sessions",
     "Claude Code instances doing work|one pane, one transcript, many views",
     "seam.sessions", "a"),
    ("CLAIMABLE TASKS", "tasks",
     "clawgate Tasks is canonical here|three other systems also mint work",
     "tasks.intake", "b"),
    ("SUBSYSTEMS", "subsystems",
     "what is worked on, and what is|known about it &#8212; recalled on demand",
     "index.store", "c"),
    ("WHAT CONSTRAINS ALL THREE", "constraints",
     "always-on context budgets &#183; two gate tiers, in two environments|"
     "merged is not deployed &#8212; a switch, per host, or nothing moves",
     "skills.listing", "d"),
)

#: Box geometry, parallel to `OVERVIEW_STAGES`: three concept boxes across the
#: top, and the constraints band spanning the full width beneath them. The band
#: is WIDE and LOW on purpose — it reads as ground, not as a peer.
_OV_GEOM = (
    (16, 30, 272, 112),
    (324, 30, 272, 112),
    (632, 30, 272, 112),
    (16, 214, 888, 92),
)


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
    """The three concepts, the three seams between them, and the ground they sit on.

    Each stage is an `<a href="#slug">`: this figure IS the page's primary
    navigation, which is why it must stay legible rather than shrink to fit. The
    CSS gives it a `min-width` inside a scrolling container, so a narrow viewport
    scrolls the FIGURE and never the page body.

    🔴 THE EDGES ARE THE PAYLOAD, not decoration between boxes. Each of the three
    is a place where two owners meet and neither holds both sides, so all three
    are drawn as labelled arrows and the whole seam group is itself a link.
    """
    mid = "ar-ov"
    # 🔴 NOT role="img". An `img` role collapses the whole figure into one opaque
    # image for assistive tech, which would hide the stage LINKS inside it — and
    # this diagram is the page's primary navigation. `role="group"` keeps the
    # accessible name while leaving the links reachable.
    parts = [_SVG_HEAD.format(w=920, h=344, alt=(
        "The three primary concepts, drawn as three boxes: sessions, claimable "
        "tasks and subsystems. Sessions claim tasks; tasks name subsystems; "
        "sessions write subsystems. Beneath all three runs a band of constraints "
        "they all sit inside. Each box carries its live measured count and links "
        "to its section."
    ), mid=mid)
        .replace('role="img"', 'role="group"')
        .replace('class="diagram"', 'class="diagram overview"')]

    for i, (label, slug, sub, key, tone) in enumerate(OVERVIEW_STAGES):
        x, y, w, h = _OV_GEOM[i]
        cx = x + w / 2
        lines, un, reason = _stage_count(ms, key)
        tip = f"{label} — {'UNMEASURED: ' + reason if un else 'open this section'}"
        parts.append(f'<a href="#{slug}"><title>{html.escape(tip)}</title>')
        parts.append(_box(x, y, w, h, label, sub, "warn" if un else tone))
        cls = "dcount dcount-un" if un else "dcount"
        # One line sits low and centred; two lines start higher so the second
        # never rides the box's bottom edge. An UNMEASURED stage always takes the
        # two-line form: the word, and where its reason lives.
        top = y + h - (22 if (len(lines) == 1 and not un) else 28)
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{cx}" y="{top + j * 14}" class="{cls}">'
                f'{html.escape(line)}</text>'
            )
        if un:
            parts.append(
                f'<text x="{cx}" y="{y + h - 12}" class="dnote">'
                'why not &#8212; open this section</text>'
            )
        parts.append("</a>")

    # The three seams. The two short ones sit between the boxes; the long one
    # runs underneath, because sessions reach subsystems without going through a
    # task at all — which is the seam that leaks.
    parts.append('<a href="#seams"><title>The three seams &#8212; open the section</title>')
    parts.append(_arrow(292, 86, 320, 86, mid, "claim"))
    parts.append(_arrow(600, 86, 628, 86, mid, "name"))
    parts.append(
        '<path d="M152,148 V186 H768 V150" class="darrow" '
        f'marker-end="url(#{mid})"/>'
    )
    parts.append('<text x="460" y="180" class="dedge">sessions write subsystems '
                 'without going through a task</text>')
    parts.append('<text x="460" y="204" class="dctr">THE SEAMS ARE WHERE THE DEFECTS LIVE</text>')
    parts.append("</a>")

    parts.append('<text x="460" y="330" class="dnote">Not a fourth concept &#8212; the '
                 'physics. Nothing above escapes the band below it.</text>')
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
        slug="overview", number="0", title="Overview — three concepts",
        lede="A NixOS dotfiles repo that grew an agent-operations layer: one operator works "
             "almost entirely through coding agents, so what is engineered here is the "
             "agent's environment. Three nouns carry all of it. Each box below shows its "
             "live count and is a link.",
        blocks=(
            ("svgm", "overview"),
            ("note", ("why", "One fact explains most of the design", (
                "<b>Instructions cost context on every session, and context is finite.</b> "
                "The byte ceilings, the tier ledger, the eviction rule and the refusal to "
                "restate a number all fall out of that &mdash; which is why the band under "
                "the three concepts is drawn as ground rather than as a fourth box."))),
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
                "numbers cannot go stale, so they are in bounds.",
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
                ("“What is running, and is anything waiting on me?”",
                 "<a href=\"#sessions\">§3</a>. A session is one agent instance in one "
                 "terminal pane; every tool here is a view onto that pane and its "
                 "transcript."),
                ("“What should I pick up next?”",
                 "<a href=\"#tasks\">§4</a>. One task system is canonical &mdash; and three "
                 "others also mint work, which is the thing to know before you meet one."),
                ("“What do we already know about X?”",
                 "<a href=\"#subsystems\">§5</a>. Knowledge about a subsystem is durable "
                 "state in a store that costs nothing until it is recalled."),
                ("“Propose an improvement.”",
                 "<a href=\"#negative\">§10</a> first, then <a href=\"#evidence\">§11</a>. "
                 "§10 is what was already killed, each with the measurement that killed it."),
            ]),
            ("note", ("what", "Where everything else went", (
                "The three touch at three edges, and that is where the real defects are "
                "&mdash; <a href=\"#seams\">§6</a>. What bounds all three is not a fourth "
                "concept but the physics they sit in "
                "(<a href=\"#constraints\">§7</a>)."))),
        ),
    ),

    Section(
        slug="sessions", number="3", title="Sessions",
        lede="A session is one Claude Code instance doing work. Every tool named here is a "
             "view onto the same two facts — a multiplexer pane, and a transcript on disk.",
        blocks=(
            ("p", (
                "There is no session <i>service</i> and no session record. A session is "
                "inferred &mdash; a process under a pane, and a transcript file it appends "
                "to &mdash; and everything that reports on sessions re-derives that "
                "inference, which is why these surfaces disagree with each other more than "
                "anything else here (<a href=\"#seams\">§6</a>).")),
            ("kv", [
                ("The live inventory &mdash; <code>/session-manager</code>",
                 "Cross-host: which panes have an agent, what each is doing, and &mdash; the "
                 "one that pays &mdash; which are <i>waiting on a human</i>. That got its own "
                 "tool after two windows were measured unanswered for days."),
                ("One window &mdash; <code>/window-triage</code>",
                 "By codename, hotkey or address, plus a ranking of the windows stranded "
                 "past a threshold."),
                ("A past session &mdash; <code>/find-session</code>",
                 "Keyword search across every transcript: project, branch, resume command."),
                ("The scripts underneath",
                 "Resolution, the waiting clock, writing into a pane, the shared "
                 "<code>/proc</code> detector, and the multiplexer naming and restore "
                 "helpers. <b>The skill is the surface; this page routes to it.</b>"),
            ]),
            ("h3", "What a session may do"),
            ("p", (
                "Prose is the weak form of control &mdash; the rules file says so itself. "
                "The strong form is a <b>hook</b>: a program the harness runs before a tool "
                "call, whose exit code can block it. The blocking one refuses blind staging, "
                "hard resets, <code>git stash</code> (the stash stack is repository-global, "
                "so it reaches into other worktrees), oversized heredocs, and publishing a "
                "secret or a public IP into a public repo.")),
            ("note", ("hazard", "A hook that only nudges is not a guard", (
                "Most hooks here emit advice and exit zero. Legitimate &mdash; one nudge "
                "moved a behaviour from 0% to 50% adoption &mdash; but <b>not</b> a control. "
                "<b>Read the exit code before calling something a guard.</b>"))),
            ("note", ("hazard", "Shipped and registered are independent facts", (
                "The repo ships hook scripts; a <i>host</i> registers them in a per-host, "
                "deliberately unmanaged settings file. A hook can be present, correct, fully "
                "tested, and firing on neither machine &mdash; hence two counts below."))),
            ("measure", "hooks"),
            ("h3", "What is observed about sessions"),
            ("p", (
                "Shell, multiplexer, keyboard, window manager, browser and agent transcripts "
                "feed a collector that lands events in a columnar database, with a "
                "per-source deadman so a source that stops is noticed rather than assumed "
                "quiet. It is what makes “this tool is dead” measurable.")),
            ("measure", "telemetry.sources"),
        ),
    ),

    Section(
        slug="tasks", number="4", title="Claimable tasks",
        lede="A unit of work something can pick up and be held to. One system here is "
             "canonical. Three others also mint work, and meeting one of those in the wild "
             "should not be a surprise.",
        blocks=(
            ("note", ("why", "The canonical one, and why the others are still here", (
                "<b>clawgate Tasks</b> is the canonical claimable task: it has dispatch, "
                "agents, acceptance criteria and a status gate &mdash; the four properties "
                "that let something other than a human pick the work up and be judged on it. "
                "The other three are listed, not hidden. <b>Implying a consolidation that "
                "has not happened is the failure this page exists to teach against</b>: it "
                "reads as coverage while providing none."))),
            ("p", (
                "clawgate is the self-hosted approval UI &mdash; an agent's permission "
                "prompt becomes something the operator can approve from a phone &mdash; and "
                "it grew a Tasks board on that channel. Authoring one well is a procedure "
                "with its own flow document rather than a paragraph here. Skill: "
                "<code>/clawgate</code>.")),
            ("h3", "The other three, one line each"),
            ("kv", [
                ("ClickUp &mdash; <code>/clickup</code>",
                 "The client-facing ticket system. <b>Separate because it is someone else's "
                 "system of record</b>: statuses and assignees are set off this machine, so "
                 "nothing here gates on them and no agent claims one. "
                 "<code>/check-clickup-addressed</code> reads transcripts to answer whether "
                 "the work happened."),
                ("The initiatives board &mdash; <code>/initiatives</code>",
                 "A durable cross-repo ledger of ongoing efforts, with momentum and a next "
                 "step. <b>Separate because an initiative is a thread, not a unit</b>: "
                 "coarser than a task and carrying no acceptance criterion. "
                 "<code>/initiative-scan</code> is the on-demand view."),
                ("<code>scripts/claim-work.sh</code>",
                 "<b>Separate because it is a lock, not a queue</b>: the ranked next-steps "
                 "list in a handoff document has no lock, so pushing an orphan commit to a "
                 "claim ref <i>is</i> the claim &mdash; git's own compare-and-swap, so two "
                 "simultaneous first movers resolve to exactly one winner. It <b>fails "
                 "open</b>."),
            ]),
            ("note", ("hazard", "Four systems mint work and none knows about the others", (
                "A session drawing from a handoff document is covered by the claim lock; a "
                "clawgate Task by its own status gate; a ClickUp ticket and an initiative by "
                "nothing on this machine. <b>What would settle it:</b> whether one claim can "
                "be expressed against all four, or whether the other three are deliberately "
                "out of scope. Neither is measured here &mdash; so it is analysis, not a "
                "work item."))),
            ("measure", "tasks.intake"),
        ),
    ),

    Section(
        slug="subsystems", number="5", title="Subsystems",
        lede="The things being worked on, and what is known about them. That knowledge is "
             "durable state in a store that costs nothing until something asks for it.",
        blocks=(
            ("p", (
                "A subsystem is a named area with an owner: the browser bridge, the download "
                "router, the telemetry pipeline, the mail automation, the drift checker. Two "
                "things attach to each &mdash; <b>an operating surface</b> (a skill, which "
                "costs nothing until its trigger fires) and <b>a recall entry</b> (what a "
                "past session learned, keyed by scope).")),
            ("p", (
                "<b>The skill is the operating surface; this page routes to it.</b> A "
                "procedure restated here would be a second documentation surface, and the "
                "second copy goes stale first. So the inventory below is deliberately "
                "shallow: name, tier, the skill's own first sentence, path.")),
            ("measure", "skills.inventory"),
            ("h3", "The index store"),
            ("p", (
                "Recalled on demand and never auto-loaded, which is precisely why it can be "
                "large where the rules file cannot (<a href=\"#constraints\">§7</a>). It is "
                "<b>local state on each machine and not in this repository</b>, so the row "
                "below describes the host that built this page. Three libraries sit under "
                "it: one reads the store, one resolves a reference to a scope, one records "
                "what a session touched. Skills: <code>/analyze-service</code> recalls, "
                "<code>/subsystem-index</code> writes, <code>/prune-index</code> evicts "
                "resolved entries so recall keeps surfacing the right one.")),
            ("measure", "index.store"),
            ("h3", "A complete subsystem with no reader"),
            ("p", (
                "A hosted API over that store is built, tested and running. The consuming "
                "client was designed, <i>decided</i> &mdash; “hosted is an entry-level "
                "advisory, never the primary read” &mdash; and never written. No local "
                "reader in this tree contains an HTTP client at all; the only things that "
                "have spoken to it are its own seeding and byte-identity scripts. <b>A "
                "subsystem can be complete, correct, well-tested and have no reader, with "
                "every gate green throughout.</b>")),
            ("measure", "seam.store_api"),
            ("measure", "seam.store_traffic"),
        ),
    ),

    Section(
        slug="seams", number="6", title="Where the three touch",
        lede="Sessions claim tasks · tasks name subsystems · sessions write subsystems. The "
             "defects live on these edges, because no single owner holds both sides.",
        blocks=(
            ("h3", "Sessions claim tasks"),
            ("p", (
                "Work intake spans <b>four</b> systems (<a href=\"#tasks\">§4</a>) and the "
                "claim mechanism covers <b>one</b>. Worktree isolation is not an alternative "
                "and never was: every colliding session was already in its own worktree, "
                "because a worktree prevents a <i>filesystem</i> collision while this is a "
                "<i>task-allocation</i> collision &mdash; isolation is what hides it.")),
            ("h3", "Tasks name subsystems"),
            ("p", (
                "A task names its subsystem in prose. <b>Nothing joins a task to a scope in "
                "the index store</b>, so what a previous session learned about that "
                "subsystem is not attached to the task that needs it; the join is done by a "
                "human or not at all. <b>What would settle it:</b> whether any consumer "
                "would branch on a scope reference carried on a task &mdash; a field that "
                "exists in a record is not a guard, only a branch on it is.")),
            ("h3", "Sessions write subsystems"),
            ("p", (
                "Sessions produce transcripts and index entries; several subsystems read "
                "them back, and <b>they do not agree on what the corpus is</b>: some walk "
                "recursively, some recursively while excluding the synthetic sub-agent "
                "directories, some only the top level. One subsystem's source documents the "
                "divergence and quantifies it in the thousands of files; a pair of walkers "
                "re-implement the same skip rule inline, held in sync by <b>a comment</b> "
                "saying they match. <b>What would settle it:</b> re-run the count before and "
                "after any consolidation &mdash; if it does not fall, the consolidation "
                "added a walker instead of removing several, and a shared walker silently "
                "picking one of the three definitions changes what at least one subsystem "
                "sees.")),
            ("measure", "seam.jsonl"),
            ("h3", "And one concept is soft inside itself"),
            ("p", (
                "“Sessions” is not one surface but a constellation, each satellite existing "
                "because of what the primary does and does not expose: one satellite's "
                "header says it adds <i>“exactly two things … a clock and a threshold”</i>, "
                "because the primary computed a “waiting” field nothing consumed. Another "
                "records a measured refutation of its own brief &mdash; the view it was told "
                "to consume drops the join key it was told to join on.")),
            ("p", (
                "Underneath, independent implementations answer “is an agent running in this "
                "pane”, and their predicates are <b>not equivalent</b>: a case-insensitive "
                "regex over the pane's command; the same regex plus a process-tree walk; "
                "exact string equality; and, in the multiplexer's own config, a prefix glob. "
                "An agent under a wrapper is invisible to the ones that do not walk the "
                "tree. The honest counterpoint: other nearby tools deliberately <i>refuse to "
                "re-derive</i> the predicate. The seam is the ones that do.")),
            ("measure", "seam.detectors"),
            ("measure", "seam.sessions"),
            ("note", ("what", "This is analysis, not a backlog", (
                "None of the above becomes a work item without a closing condition &mdash; a "
                "mechanical check, or a named human judgement over named evidence. Where "
                "this page can name one it does; where it cannot it says so, rather than "
                "minting an object nobody can close."))),
        ),
    ),

    Section(
        slug="constraints", number="7", title="What constrains all three",
        lede="Not a fourth concept — the physics. Three facts bound every session, every "
             "task and every subsystem here, and most of the design falls out of the first.",
        blocks=(
            ("h3", "1 &middot; Always-on context is finite, and overflow is silent"),
            ("p", (
                "Three surfaces load before the operator types anything, on every session, "
                "on two machines, for every concurrent agent. So the question is never “is "
                "this instruction correct?” but “is it worth what it costs, forever, against "
                "what it displaces?”")),
            ("svg", "budget"),
            ("kv", [
                ("The rules file &mdash; <i>paid twice</i>",
                 "Charged once by the harness, again because the nix build concatenates it "
                 "into the other agent runner's instructions. It once grew 2.9&times; in "
                 "three days with every commit individually correct. A test owns its "
                 "ceiling."),
                ("The skill listing &mdash; <i>it fails silently</i>",
                 "Every name and description loads every session; a skill's body costs "
                 "nothing until its trigger fires. On overflow the harness <b>drops "
                 "descriptions, least-invoked first, with no error</b> &mdash; so a "
                 "description is <b>routing surface, not documentation</b>. Tier a skill by "
                 "the symptom that should auto-fire it, never by an invocation counter: two "
                 "here are demonstrably live with a recorded count of <i>zero</i>."),
            ]),
            ("measure", "rules.bytes"),
            ("measure", "rules.archive"),
            ("note", ("hazard", "Never satisfy a ceiling by narrowing a rule", (
                "One prohibition had to be <i>re-broadened</i> after a narrow wording let an "
                "agent walk into the failure it forbade. <b>Evict the evidence, never the "
                "scope</b> &mdash; and raising a ceiling costs a commit message naming the "
                "rule that would not fit."))),
            ("measure", "skills.listing"),
            ("p", (
                "The third fails most quietly: <b>content past the memory index's cap is "
                "dropped on load, silently</b>. It does not error; it carries less than it "
                "says.")),
            ("measure", "memory.index"),
            ("h3", "2 &middot; Two gate tiers, running in two environments"),
            ("svg", "tiers"),
            ("p", (
                "The diagram is the whole warning. Four consecutive green dev-host runs on "
                "one pull request were followed by a red sandbox check, because the sandbox "
                "tier had simply never been run.")),
            ("measure", "gate.tiers"),
            ("p", (
                "The gate reports its own inability to answer as a distinct code. "
                "<b>90 is not &ldquo;the tests failed&rdquo;</b> &mdash; it means the runner's "
                "verdict and its exit status disagreed, or the run was truncated, so the "
                "gate cannot vouch for it. Debugging a diff against a 90 is debugging the "
                "wrong thing.")),
            ("measure", "gate.exit_codes"),
            ("p", (
                "Whether a check <i>runs</i> and whether it <i>blocks</i> are different "
                "facts, and the repo's machine-checked marker sees only the first. One day "
                "here exactly one of the two required contexts was listed, and it collected "
                "only the JavaScript tests &mdash; so a Python-only change could not fail it "
                "and read as mergeable with the Python suite red. <b>Check the list, not "
                "that the key exists.</b>")),
            ("measure", "gate.protection"),
            ("measure", "gate.hooks_installed"),
            ("p", (
                "Both runners assert a <b>collected-test floor</b> and parse structured "
                "output rather than an exit status, because a collection error produces "
                "“zero tests” with a zero exit. The global floor is the sum of per-target "
                "floors, each a function of a measurement; the hand-written total it "
                "replaced took eleven values across eight pull requests in one day. What a "
                "test must <i>prove</i>, as opposed to run, is "
                "<a href=\"#evidence\">§11</a>.")),
            ("measure", "tests.pytest"),
            ("measure", "tests.node"),
            ("h3", "3 &middot; Merged is not deployed"),
            ("p", (
                "A nix flake describes what each host should have; applying it is a "
                "<i>switch</i>, and one convergence script fetches, fast-forwards, switches "
                "and verifies both hosts. <code>git pull</code> changes nothing nix manages "
                "&mdash; <b>deliberately</b>, so a concurrent session's checkout cannot swap "
                "deployed code out from under a verification. Whether a given path is live "
                "or stale is answered by resolving the symlink and by nothing else: some "
                "managed paths are store copies, others point back at the working tree, and "
                "byte-identical proves nothing because identity can mean they are one "
                "file.")),
            ("note", ("hazard", "The skip is the dangerous outcome, not the failure", (
                "A host the script cannot fast-forward is <b>skipped and left exactly as "
                "found</b> &mdash; and then keeps looking healthy: same commits, same green "
                "generation, no error, receiving nothing. Each time that happened the only "
                "detector was a human shipping something unrelated. <b>Read every per-host "
                "line, not the final verdict.</b>"))),
            ("measure", "ship.managed"),
            ("p", (
                "Nothing invokes that script on a schedule, so a separate checker runs "
                "unattended asking one question: <i>is either host silently no longer "
                "receiving changes?</i> <b>A deadman that repairs is a deployer with no "
                "supervision</b>, so it only reports. It answers three independent parities "
                "&mdash; git (is the checkout tracking?), host (is what the checkout "
                "describes actually <i>deployed</i>, on both machines?) and source (some "
                "packages build from a working tree of <i>another</i> repository, and "
                "nothing converges those). One host was once byte-identical to origin while "
                "every skill on it was a dangling symlink into a collected store path.")),
            ("note", ("hazard", "Why UNMEASURED grew its own escalation", (
                "Setting no exit code for “we could not look” is right per run and wrong "
                "forever: a scope that could never be evaluated escalated <b>never</b>, so "
                "the run read clean while the check that should have fired was structurally "
                "unable to. It now rides a consecutive-run ladder, per host and per scope, "
                "reset the moment it measures. <b>This is the idea this page borrowed most "
                "directly.</b>"))),
            ("measure", "drift.ladder"),
            ("measure", "timers"),
        ),
    ),

    Section(
        slug="invariants", number="8", title="Invariants and tripwires",
        lede="What a change must not break. Most are enforced by a test; where one is not, "
             "it says so.",
        blocks=(
            ("kv", [
                ("Adding a skill costs an eviction, in the same commit",
                 "The listing total is ratcheted by a test that owns the constant, and a new "
                 "skill needs a tier-ledger entry &mdash; pinned two-way, so a skill with no "
                 "entry and an entry naming no skill both fail. "
                 "<span class=\"tag\">enforced</span>"),
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
                 "is silently omitted &mdash; the switch succeeds and the file is not there. "
                 "<span class=\"tag tag-soft\">not enforced &mdash; the loudest silent failure here</span>"),
                ("Worktree isolation for parallel file-modifying agents",
                 "Two agents modifying files in one checkout <b>will</b> clobber each "
                 "other. A worktree isolates a working directory only &mdash; not the repo "
                 "it was built from, the branch namespace, the gitignored environment file, "
                 "submodules, or a copy you make of it. It is <b>not</b> a defence against "
                 "two sessions claiming the same work (<a href=\"#seams\">§6</a>). "
                 "<span class=\"tag tag-soft\">convention</span>"),
                ("Never <code>git stash</code>, <code>git add -A</code> or "
                 "<code>git reset --hard</code>",
                 "The stash ref lives in the common git directory, so your own worktree "
                 "gives zero isolation and a concurrent agent can pop your stash; blind "
                 "staging leaks unrelated work and secrets from a dirty tree; a hard reset "
                 "irreversibly destroys uncommitted work. "
                 "<span class=\"tag\">hook-blocked</span>"),
                ("Merged is not deployed",
                 "Every managed path changes only on a switch. "
                 "<span class=\"tag tag-soft\">not enforced &mdash; see <a href=\"#constraints\">§7</a></span>"),
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
        slug="cost", number="9", title="The cost model of a change",
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
        slug="negative", number="10", title="Negative space — what was tried and rejected",
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
                "negative</b>, and the source says why: <i>“the escape from a dismissal is a "
                "new session, which costs nothing and is unambiguous.”</i>")),
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
                "entry count &mdash; it counted every markdown file, including one index per "
                "scope, so quoting the header made the hosted copy look like a superset of "
                "the store it mirrors. An entire ratio argument was then <b>deleted "
                "outright</b>, because every audit round but the first found its new defect "
                "inside that one paragraph: <i>“a number nobody needs is a place for the "
                "next error to live.”</i>"))),
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
                "<b>Shrinking the telemetry table</b> &mdash; the largest conceivable saving "
                "was about twenty megabytes against a hundred-and-twenty-gigabyte store, and "
                "it was the only option that lost data. <i>Size the prize first.</i>",
                "<b>A stale-directory reap estimated at tens of gigabytes</b> &mdash; "
                "self-retracted: it multiplied a sample <i>mean</i> over a heavy-tailed "
                "distribution, the actual reap freed about 1.4 GiB, and the median was "
                "visible at the time. <i>Never extrapolate a mean over a heavy tail when the "
                "median is in front of you.</i>",
                "<b>A reported flake rate</b> &mdash; retracted: measured over a window the "
                "measuring session was itself saturating, 4% before the load burst and 24% "
                "during it. <i>A rate measured inside your own load is measuring you.</i>",
                "<b>“The runner can exit zero while printing FAIL”</b> &mdash; retracted, "
                "does not reproduce; the real defect was a status destroyed by a pipeline. "
                "<i>A false green is usually a status read through a pipe.</i>",
            ]),
        ),
    ),

    Section(
        slug="evidence", number="11", title="The evidence bar",
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
                 "surface; every route using that middleware was."),
                ("2. Retire a legacy mode — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>reading the comment already in the source</b>: the mode had "
                 "been made opt-in days earlier and the win was already banked. (Its "
                 "supporting evidence was also n=3, on a single day.)"),
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
                 "(The <code>--rearm</code> refusal from <a href=\"#negative\">§10</a>.)"),
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
                "“nothing was ever proposed”</i>. Take the slug from the remote URL.",
                "<b>A definition is not a mechanism.</b> Expect a definition <i>and</i> a "
                "call site; a definition alone is a dormant field. The first revision of "
                "the correction got this wrong by quoting a document instead of checking "
                "the code &mdash; the same failure, inside the correction.",
                "<b>Engage the existing counter-argument.</b> Re-proposing something whose "
                "refusal is written down, without engaging it, is a regression in the "
                "reasoning rather than a finding.",
                "<b>No closing condition, no work item.</b> A proposal that cannot name "
                "what would end it &mdash; a mechanical check, or a named human judgement "
                "over named evidence &mdash; is not a work item. Say so, and why, rather "
                "than mint an object nobody can close.",
            ]),
            ("h3", "The same bar, applied to a test"),
            ("p", (
                "Stricter than “the suite is green”. The vocabulary &mdash; <i>invariant "
                "guard</i>, <i>seam guard</i>, <i>positive control</i> &mdash; is defined in "
                "<a href=\"#glossary\">§12</a>; these are the obligations.")),
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
                "the combined state. Ask which surface your fixture does <i>not</i> load "
                "&mdash; the same question <a href=\"#seams\">§6</a> asks of the system.",
                "<b>Never derive an expectation from the implementation it tests.</b> "
                "Stubbing one function to a no-op once left thirty-one integration tests "
                "green.",
            ]),
        ),
    ),

    Section(
        slug="glossary", number="12", title="Glossary",
        lede="The local vocabulary is not guessable. These appear in commit messages, test "
             "names and skill bodies, and mean specific things here.",
        blocks=(
            ("kv", [
                ("session",
                 "One agent instance doing work in one multiplexer pane, with one transcript "
                 "on disk. Not a record anything keeps &mdash; every surface that reports on "
                 "sessions <i>infers</i> them, which is why they disagree."),
                ("claimable task",
                 "A unit of work something other than a human can pick up and be judged on: "
                 "dispatch, an agent, acceptance criteria, a status gate. Three other "
                 "systems here mint work without being this."),
                ("scope",
                 "The key an index-store entry is filed under &mdash; usually a repository "
                 "or a subsystem. What a recall asks for."),
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
                 "something the operator can approve remotely. Its Tasks board is the "
                 "canonical claimable task here."),
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
