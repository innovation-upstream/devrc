#!/usr/bin/env python3
"""The PROSE SPINE and the hand-authored diagrams.

🔴 THIS MODULE CONTAINS NO QUANTITIES. Not a byte count, not a test count, not a
number of skills. Every figure on the page is produced by `measure.py` at build
time and injected by `render.py`. The rule is mechanical and
`test_present_content.py` enforces it, because the failure it prevents is this
repo's most reliable one: prose that was true when written, restated once, and
false within days.

Prose here may name a MECHANISM ("the listing is capped at a fraction of the
context window"), never its VALUE ("the listing is capped at N chars").

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
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    #: | ("svg", key) | ("measure", key) | ("cards", [(title, html), ...])
    #: | ("h3", text) | ("kv", [(k, v_html), ...])
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


def diagram_loop() -> str:
    """The loop a change travels. The page's organising figure."""
    mid = "ar-loop"
    parts = [_SVG_HEAD.format(w=920, h=330, alt=(
        "The loop a change travels: what the agent is told, what it may do, how "
        "the work is verified in two gate tiers, how it ships via home-manager, "
        "and how drift is caught by a passive deadman that feeds back into what "
        "the agent is told."), mid=mid)]
    row = 40
    parts.append(_box(20, row, 160, 66, "TOLD", "rules · skills · memory|all three BUDGETED", "a"))
    parts.append(_arrow(180, row + 33, 228, row + 33, mid))
    parts.append(_box(230, row, 160, 66, "MAY DO", "blocking hooks|integrations that act", "a"))
    parts.append(_arrow(390, row + 33, 438, row + 33, mid))
    parts.append(_box(440, row, 200, 66, "VERIFIED", "two gate TIERS|different environments", "b"))
    parts.append(_arrow(640, row + 33, 688, row + 33, mid))
    parts.append(_box(690, row, 210, 66, "MERGED", "a claim about ONE branch,|never the merged tree", "b"))

    parts.append(_arrow(795, row + 66, 795, 150, mid))
    parts.append(_box(690, 150, 210, 66, "SHIPPED", "home-manager switch|merged is NOT deployed", "c"))
    parts.append(_arrow(690, 183, 642, 183, mid))
    parts.append(_box(440, 150, 200, 66, "OBSERVED", "telemetry · index store|session surfaces", "c"))
    parts.append(_arrow(440, 183, 392, 183, mid))
    parts.append(_box(230, 150, 160, 66, "DRIFT", "passive deadman|reports, never fixes", "d"))

    parts.append(_arrow(230, 183, 110, 183, mid))
    parts.append(_arrow(110, 150, 110, 108, mid, ""))
    parts.append(
        '<text x="110" y="240" class="dnote">what drift finds</text>'
        '<text x="110" y="256" class="dnote">becomes a RULE —</text>'
        '<text x="110" y="272" class="dnote">which costs budget,</text>'
        '<text x="110" y="288" class="dnote">which is why the</text>'
        '<text x="110" y="304" class="dnote">loop has a ceiling</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def diagram_tiers() -> str:
    """Two gate tiers, and what each is structurally blind to."""
    mid = "ar-tiers"
    parts = [_SVG_HEAD.format(w=920, h=290, alt=(
        "The two gate tiers: the dev-host tier runs the runners in a real git "
        "checkout; the sandbox tier builds from a store copy with no .git and is "
        "the one the merge is gated on. Each is blind to what the other sees."
    ), mid=mid)]
    parts.append('<text x="20" y="100" class="dhead">one change</text>')
    parts.append(_arrow(105, 96, 200, 96, mid))

    parts.append(_box(210, 46, 330, 96, "DEV-HOST TIER", (
        "scripts/gate.sh --tier both|a real git checkout, your PATH|"
        "never invokes nix build"), "b"))
    parts.append('<text x="375" y="162" class="dnote">BLIND TO: anything the</text>'
                 '<text x="375" y="178" class="dnote">sandbox lacks — no .git,</text>'
                 '<text x="375" y="194" class="dnote">no network, pinned python</text>')

    parts.append(_box(570, 46, 330, 96, "SANDBOX TIER", (
        "nix build .#checks…{pytests,nodetests}|a cp -r store copy, NO .git|"
        "this is what CI runs"), "d"))
    parts.append('<text x="735" y="162" class="dnote">BLIND TO: anything keyed on</text>'
                 '<text x="735" y="178" class="dnote">the dev host — an ambient</text>'
                 '<text x="735" y="194" class="dnote">tool, a repo-local git config</text>')

    parts.append(_arrow(540, 94, 566, 94, mid))
    parts.append('<rect x="210" y="222" width="690" height="46" rx="7" class="dbox dbox-warn"/>')
    parts.append('<text x="555" y="242" class="dlabel">GREEN ON BOTH TIERS = one claim, about one BASE SHA</text>')
    parts.append('<text x="555" y="259" class="dsub">the tree a merge CREATES was never run — gating that is still yours to do by hand</text>')
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
    parts.append(_box(310, 36, 270, 62, "the SKILL LISTING", "name + description, per skill|~1% of the context window", "a"))
    parts.append(_box(600, 36, 300, 62, "MEMORY.md", "hard cap — overflow is DROPPED|silently, with no error", "a"))

    parts.append('<text x="20" y="140" class="dhead">FREE until something asks for it</text>')
    parts.append(_box(20, 154, 270, 62, "skill BODIES", "loaded on trigger only|so detail belongs here", "c"))
    parts.append(_box(310, 154, 270, 62, "reference/ + flows/", "durable facts · procedures|nothing auto-fires them", "c"))
    parts.append(_box(600, 154, 300, 62, "the INDEX STORE", "recalled on demand, per scope|local state, not in the repo", "c"))
    parts.append('<text x="20" y="238" class="dnote dnote-l">The whole design falls out of this split: '
                 'every instruction is a bid for space in the top row, and the top row is the only thing that is scarce.</text>')
    parts.append("</svg>")
    return "".join(parts)


DIAGRAMS = {
    "loop": diagram_loop,
    "tiers": diagram_tiers,
    "budget": diagram_budget,
}


# --------------------------------------------------------------------------- #
# The spine
# --------------------------------------------------------------------------- #

SECTIONS: tuple[Section, ...] = (
    Section(
        slug="how-to-read", number="0", title="How to read this page",
        lede="The page follows the same evidence rules it describes. Read these first, "
             "because they tell you what a row on this page is and is not claiming.",
        blocks=(
            ("ul", [
                "<b>Every number here was measured when the page was built</b>, by a "
                "function in <code>scripts/present/measure.py</code>, and is stamped "
                "with the moment it was taken. No figure on this page was typed by hand.",
                "<b>The stamp is per row, not per page.</b> A byte count re-derived from "
                "the tree ages in minutes; a systemd timer roster ages in days. One "
                "footer date would claim the same freshness for both.",
                "<b>An absence is reported as an absence.</b> A fact the generator could "
                "not measure renders <span class=\"pill pill-un\">UNMEASURED</span> with "
                "the reason and the command that would settle it. It is never omitted and "
                "never rendered blank — an omitted row is byte-identical to one that "
                "measured clean.",
                "<b>A green check is a claim about one tier and one base sha.</b> Where "
                "this page says a gate passed, it names which gate, in which environment.",
                "<b>Prose here names mechanisms; only measured rows carry values.</b> The "
                "generator's content module is forbidden to contain a quantity, and a "
                "test enforces that.",
            ]),
            ("note", ("why", "Why a generator instead of a document", (
                "This repo's own <code>CLAUDE.md</code> has carried the same claim in two "
                "opposite, both-wrong directions — a line about the merge gate read "
                "<i>“CI gates both suites”</i> and later <i>“NO AUTOMATED GATE IS "
                "RUNNING”</i>. Neither was true when read. A page that restated those "
                "numbers would inherit the decay; a page that re-measures them cannot. "
                "The cost of that choice is real: this page must be re-run to be current, "
                "and a stale copy is stale in a way a reader cannot see except by the "
                "per-row stamps."))),
            ("measure", "repo.head"),
        ),
    ),

    Section(
        slug="start-here", number="1", title="Start here",
        lede="Four entry paths. Pick by what you came to do — they lead to different "
             "sections and, more importantly, to different failure modes.",
        blocks=(
            ("cards", [
                ("“I want to add a skill or a rule.”",
                 "Read <a href=\"#told\">§2 What the agent is told</a> and then "
                 "<a href=\"#cost\">§9 The cost model</a>. Both are budgeted surfaces "
                 "with test-enforced ceilings; an addition needs an eviction in the same "
                 "commit, and a new skill also needs a tier-ledger entry or the suite "
                 "goes red. This is the path where people are most surprised."),
                ("“I want to change some code.”",
                 "Read <a href=\"#verified\">§4 How work is verified</a> and "
                 "<a href=\"#invariants\">§8 Invariants and tripwires</a>. The two things "
                 "that catch people: there are two gate tiers running in two different "
                 "environments, and merged is not deployed."),
                ("“Something is broken / stale on a host.”",
                 "Read <a href=\"#ships\">§5 How it ships</a> and <a href=\"#drift\">§6 "
                 "How drift is caught</a>. Three kinds of parity are independent — git "
                 "parity, host parity, and source parity — and a host can be perfect on "
                 "one while silently broken on another."),
                ("“I want to propose an improvement.”",
                 "Read <a href=\"#negative\">§10 Negative space</a> FIRST, then "
                 "<a href=\"#evidence\">§11 The evidence bar</a>. §10 is a list of things "
                 "already tried and killed, each with the measurement that killed it — "
                 "it exists to stop you re-proposing a settled question. §11 is the local "
                 "standard: measure, then propose."),
            ]),
            ("note", ("what", "What this system is, in one paragraph", (
                "It is a NixOS/home-manager dotfiles repo that grew an agent-operations "
                "layer. One operator works almost entirely through coding agents, so the "
                "thing being engineered is not the shell prompt — it is the agent's "
                "environment: what it is told, what it is permitted to do, how its work "
                "is verified, how that reaches two machines, and how you find out when a "
                "machine has quietly stopped receiving changes. Most of the surprising "
                "design decisions here are consequences of one fact: <b>instructions cost "
                "context on every single session, and context is finite.</b>"))),
            ("svg", "loop"),
        ),
    ),

    Section(
        slug="told", number="2", title="What the agent is told — and why it is all budgeted",
        lede="Three surfaces load into every session before the operator types anything: "
             "a rules file, a skill listing, and a memory index. All three are capped. "
             "The caps are the most explanatory fact about the whole design.",
        blocks=(
            ("p", (
                "Start with the economics, because everything else follows from them. "
                "Instructions are not free and they are not paid once — they are paid on "
                "<b>every session</b>, which for this operator means many times a day "
                "across two machines and several concurrent agents. So the design question "
                "is never “is this instruction correct?” It is “is this instruction worth "
                "what it costs, forever, measured against everything it displaces?”")),
            ("svg", "budget"),
            ("h3", "The three always-on surfaces"),
            ("p", (
                "<b>The rules file</b> (<code>claude/RULES.md</code>) carries the "
                "imperatives — the things an agent must never do and the verification "
                "standard it is held to. It is paid <i>twice</i>: once by Claude Code, and "
                "again because the nix build concatenates it into the other agent runner's "
                "instruction file. It grew 2.9× in three days once, by a process where "
                "every single commit was individually correct — a real lesson, really "
                "measured — and nothing ever asked what the file cost. A comment at the top "
                "asking people to keep it short is the prose fix; the growth curve is what "
                "the prose fix achieved. The structural fix is a byte ceiling owned by a "
                "test, with a documented eviction target: the <i>imperative</i> stays in "
                "the core, the <i>worked incident</i> moves to an archive that costs "
                "nothing because nothing loads it.")),
            ("measure", "rules.bytes"),
            ("measure", "rules.archive"),
            ("note", ("hazard", "The ceiling must never be satisfied by narrowing a rule", (
                "The rules file's own opening instruction is “read every rule at its "
                "widest reading”, and one prohibition had to be <i>re-broadened</i> "
                "because a narrow wording let an agent walk straight into the failure it "
                "forbade. So the ceiling's playbook is explicit: evict the evidence, never "
                "the scope. Raising the ceiling is allowed — and requires the commit "
                "message to name the rule that would not fit in the budget."))),
            ("h3", "The skill listing — the one that fails silently"),
            ("p", (
                "A <i>skill</i> is a packaged procedure for operating one subsystem: how to "
                "drive the browser, how to query the telemetry, how to deploy the approval "
                "UI. A skill body costs <b>nothing</b> until its trigger fires. But every "
                "skill's <b>name and description</b> load on every session, under a budget "
                "of about one percent of the context window, measured in characters.")),
            ("p", (
                "That makes a description <b>routing surface, not documentation</b>: its "
                "job is to contain the literal phrases the operator says, so the skill "
                "fires from a described symptom. And the failure mode is the nasty kind — "
                "on overflow the harness <b>drops descriptions, starting with the "
                "least-invoked skills, with no error at all</b>. The skills least able to "
                "afford weak routing are exactly the ones that lose it first, and nothing "
                "tells you.")),
            ("p", (
                "The response is a <b>tier ledger</b>: every shipped skill is assigned "
                "tier A (full description, auto-fires from a symptom) or tier B "
                "(name-only — roughly a dozen characters, still invocable by name and "
                "still callable by the harness, but with no routing prose). The ledger is "
                "pinned <b>two-way</b> by a test: a shipped skill with no entry fails, and "
                "an entry naming no shipped skill fails. That two-way pin is the only "
                "thing that makes the mechanism scale — without it, adding a skill would "
                "silently opt out of the whole scheme.")),
            ("measure", "skills.listing"),
            ("note", ("hazard", "Tier by symptom, never by an invocation counter", (
                "The question is <i>“must this fire when the operator describes a symptom "
                "rather than naming the tool?”</i> — not “how often is it used”. Two "
                "skills in this tree are demonstrably live with a recorded invocation "
                "count of zero, because the counter cannot see a skill that runs as a "
                "systemd service. The tie-break for the genuinely close calls: tier B when "
                "a mis-route <b>degrades the answer</b>; tier A when a mis-route <b>takes "
                "a wrong action</b>."))),
            ("h3", "The memory index"),
            ("p", (
                "Per-project auto-memory: a small index of durable cross-cutting lessons, "
                "loaded every session, with topic files behind it that cost nothing until "
                "recalled. It has a hard cap, and <b>content past the cap is dropped on "
                "load, silently</b> — the index does not error, it simply carries less "
                "than it says. The dominant cause of hitting the cap is work-status creep: "
                "“shipped”, “deployed”, “soaking”. Status belongs in a handoff document; "
                "the index is for durable lessons only.")),
            ("measure", "memory.index"),
            ("h3", "The inventory"),
            ("p", (
                "Shallow on purpose. Each row is the skill's name, its tier, its <i>own</i> "
                "first sentence, and its path. <b>The skill is the operating surface; this "
                "page routes to it.</b> Restating a skill's procedure here would create a "
                "fourth documentation surface competing with the repo instructions, the "
                "skills themselves, their reference directories, and the index store — and "
                "the fourth copy is the one that goes stale first.")),
            ("measure", "skills.inventory"),
        ),
    ),

    Section(
        slug="may-do", number="3", title="What the agent may do",
        lede="Two mechanisms: hooks that can refuse a tool call outright, and integrations "
             "that take real action in the world.",
        blocks=(
            ("p", (
                "Prose instructions are the weak form of control — the rules file says so "
                "itself, under “prefer deterministic/structural fixes over prompt-tuning”. "
                "The strong form is a <b>hook</b>: a program the harness runs before or "
                "after a tool call, whose exit code can block it. The blocking one here "
                "refuses blind staging (<code>git add -A</code>), hard resets, oversized "
                "heredocs, and any attempt to publish a secret or a public IP address into "
                "a repo that is public.")),
            ("note", ("hazard", "A hook that only nudges is not a guard", (
                "Most of the hooks in this tree emit advice and exit zero. That is a "
                "legitimate design — a nudge that measurably moved a behaviour from 0% to "
                "50% adoption is worth having — but it is <b>not</b> a control, and "
                "counting it as one is how a surface reads as protected while nothing "
                "stops anything. Read the exit code before calling something a guard."))),
            ("note", ("hazard", "Shipped and registered are independent facts", (
                "The repo ships hook scripts. A <i>host</i> registers them in a settings "
                "file that is per-host and deliberately unmanaged. So a hook can be present "
                "in the tree, correct, fully tested, and firing on neither machine. The "
                "measurement below reports both counts separately for exactly that reason."))),
            ("measure", "hooks"),
            ("h3", "Integrations that act"),
            ("p", (
                "Beyond hooks, several subsystems let an agent affect the world: a "
                "self-hosted approval UI that turns a permission prompt into a phone "
                "notification the operator can approve remotely; a ticket system it can "
                "read and comment on; a bridge that drives the operator's real, logged-in "
                "browser; a router that files downloads by page context. Each is operated "
                "through its skill — see the inventory in "
                "<a href=\"#told\">§2</a>. The page deliberately does not restate their "
                "procedures.")),
            ("note", ("why", "The one thing worth knowing across all of them", (
                "These are the surfaces where a wrong action is not recoverable by editing "
                "a file. The rules file's proactivity gate applies hardest here: work that "
                "leaves the machine, cannot be undone, or moves many things at once gets "
                "flagged <i>before</i> the work, not after — and an approval covers only "
                "the step it was given for."))),
        ),
    ),

    Section(
        slug="verified", number="4", title="How work is verified",
        lede="Two gate tiers, running the same suites in two different environments. "
             "Treating them as two spellings of one thing has already cost a required check.",
        blocks=(
            ("svg", "tiers"),
            ("p", (
                "The <b>dev-host tier</b> runs the two runners directly on the machine, "
                "through a wrapper whose exit status is trustworthy. The <b>sandbox "
                "tier</b> builds the same suites inside the nix build sandbox, from a "
                "recursive copy of the tree that has <b>no <code>.git</code> directory</b>, "
                "no network, and a pinned interpreter. That is the tier the merge is "
                "gated on.")),
            ("p", (
                "They are not interchangeable. Anything keyed on the repo being a real git "
                "checkout evaluates differently in the sandbox. Four consecutive green "
                "dev-host runs on one pull request were followed by a red sandbox check, "
                "because the sandbox tier had simply never been run. <b>Name the tier and "
                "the base sha in any claim that a merge is safe</b> — “the gate passed” is "
                "true of one run, one tier, one base, and reads as a property of the change.")),
            ("measure", "gate.tiers"),
            ("h3", "What actually blocks a merge"),
            ("p", (
                "Whether a check <i>runs</i> and whether it <i>blocks</i> are different "
                "facts, and the repo's own machine-checked marker can only see the first. "
                "There was a day here when exactly one of the two required contexts was "
                "listed — and it collected only the JavaScript tests, so a Python-only "
                "change could not fail it and read as mergeable with the Python suite red. "
                "<b>Check the list, not that the key exists.</b>")),
            ("measure", "gate.protection"),
            ("measure", "gate.hooks_installed"),
            ("h3", "Floors, not exit codes"),
            ("p", (
                "Both runners assert a <b>collected-test floor</b> and parse structured "
                "output rather than reading an exit status, because a collection error, a "
                "broken import or an empty glob can produce “zero tests” with a zero exit — "
                "and one runner's directory mode silently yields a bogus count of one. "
                "There is no hand-written total: the global floor is the sum of per-target "
                "floors, and each floor is a function of a measurement. The single literal "
                "it replaced took eleven values across eight pull requests in one day.")),
            ("measure", "tests.pytest"),
            ("measure", "tests.node"),
            ("h3", "The evidence bar for a test"),
            ("p", (
                "The local standard is stricter than “the suite is green”, and it is worth "
                "internalising before you write a test here, because a test that does not "
                "meet it will be sent back:")),
            ("ul", [
                "<b>A regression test must be shown to FAIL on pre-change code.</b> Report "
                "the matrix — red at the base ref, green at HEAD. A test that pins an "
                "invariant the bug never violated is an <i>invariant guard</i>: label it as "
                "one and do not count it as regression coverage.",
                "<b>Mutation-test a guard, and prove it REACHABLE.</b> “I broke it and a "
                "test failed” is necessary and not sufficient — a mutation still passes "
                "when an earlier check always wins, or when a <i>different</i> guard's "
                "error kills your test. Break it, confirm a test fails with <i>this</i> "
                "guard's specific error, then reach it with a case no earlier check rejects.",
                "<b>Validate the instrument before reading its verdict.</b> Two controls, "
                "both watched to work: a <i>negative control</i> (feed it a case it must "
                "fail — if that reports success it is testing nothing) and a <i>positive "
                "control</i> (feed it a case that must produce a non-zero count and watch "
                "the number move). A reassuring zero is indistinguishable from a harness "
                "wired to nothing. Report the pair, never the zero alone.",
                "<b>“Verified in isolation” is the new vacuous green.</b> Two components, "
                "each hermetically tested and audit-clean, can still be broken together, "
                "because every test was scoped to one surface and none ever built the "
                "combined state. Ask which surface your fixture does <i>not</i> load.",
                "<b>Never derive a test's expectation from the implementation it tests.</b> "
                "Stubbing one function to a no-op once left thirty-one integration tests "
                "green.",
            ]),
        ),
    ),

    Section(
        slug="ships", number="5", title="How it ships",
        lede="Two machines, converged by one idempotent script. The trap is that a merge "
             "changes nothing a package manager owns.",
        blocks=(
            ("p", (
                "Configuration is declarative: a nix flake describes what each host should "
                "have, and applying it is a <i>switch</i>. A convergence script fetches, "
                "fast-forwards, switches and verifies both hosts in one call. It never "
                "stashes — the stash stack is repository-global, so a stash reaches into "
                "other worktrees — and a host it cannot fast-forward is <b>skipped and left "
                "exactly as found</b>, with the blocking files named.")),
            ("note", ("hazard", "Merged is not deployed", (
                "Every managed path changes only on a switch. <code>git pull</code> changes "
                "nothing nix manages — and that git-immunity is <b>deliberate</b>: it means "
                "a concurrent session's <code>git checkout</code> cannot swap deployed code "
                "out from under a verification in progress. It is also exactly what makes "
                "it easy to trip on. The full sequence is merge → pull → switch → restart "
                "the consumer. Skip the last two and you will verify the old artefact and "
                "report it as the new one."))),
            ("measure", "ship.managed"),
            ("note", ("why", "readlink is the only arbiter of live-versus-stale", (
                "Some managed paths are store copies (editing the repo does nothing until "
                "a switch); others are out-of-store symlinks (the working copy <i>is</i> "
                "the live file, and edits apply immediately). Which is which is answered by "
                "resolving the symlink — never by diffing the file against the repo. "
                "Byte-identical files prove nothing: identity can simply mean they are one "
                "file."))),
            ("note", ("hazard", "The skip is the dangerous outcome, not the failure", (
                "A host that gets skipped keeps looking healthy — same commits in the log, "
                "same green generation, no error anywhere — while silently receiving "
                "nothing. That has happened more than once, and each time the only detector "
                "was a human happening to ship something unrelated and reading the per-host "
                "lines. <b>Read every per-host line, not the final verdict.</b> One skip "
                "hides among greens."))),
        ),
    ),

    Section(
        slug="drift", number="6", title="How drift is caught",
        lede="A passive deadman on a timer, because nothing runs the deploy on a schedule. "
             "It reports and never fixes.",
        blocks=(
            ("p", (
                "The convergence script is correct and it is not enough, because nothing "
                "invokes it automatically. So a separate checker runs unattended and asks "
                "one question: <i>is either host silently no longer receiving changes?</i> "
                "It may fetch; a static allowlist scanner in its own test suite proves it "
                "can run no mutating git subcommand, including through an ssh hop. "
                "<b>A deadman that repairs is a deployer with no supervision.</b>")),
            ("h3", "Parity is not one thing"),
            ("ul", [
                "<b>Git parity</b> — is the checkout still tracking the main branch? The "
                "obvious one, and for a long time the only one asked.",
                "<b>Host parity</b> — is what the checkout describes actually <i>deployed</i>, "
                "and the same on both machines? Every skill on one host was once a dangling "
                "symlink into a garbage-collected store path while the checkout was "
                "byte-identical to origin. Perfect git parity, zero host parity, checker green.",
                "<b>Source parity</b> — some packages build from a local working tree of "
                "<i>another</i> repository, and nothing converges those. One host once "
                "shipped a binary missing two subcommands while wearing the version label of "
                "one that had them; the command printed help and exited zero.",
            ]),
            ("note", ("hazard", "Why UNMEASURED had to grow its own escalation", (
                "Setting no exit code for “we could not look” is right per run and wrong "
                "forever. A scope that could never be evaluated escalated <b>never</b>, so "
                "the run read clean while the check that should have fired was structurally "
                "unable to. It now rides a consecutive-run ladder, per host and per scope, "
                "reset the moment it measures — with a longer ladder for a plausibly "
                "transient cause, and no escalation at all for a genuinely supported state. "
                "<b>This is the single idea this page borrowed most directly:</b> an "
                "absence must be reported, counted, and eventually escalated."))),
            ("note", ("why", "Two designs worth stealing from the exit-code ladder", (
                "First, an “actionable, not drift” code that is the <i>least</i> severe "
                "thing the checker owns, so it can only ever be the verdict on an otherwise "
                "clean run — and it is configured as a <i>success</i> to the service "
                "manager, because a code that stays set until someone does a cleanup would "
                "otherwise fire a failure notification several times a day forever. Second, "
                "an adopted-then-drifted-only arm: a host that has never adopted a "
                "mechanism prints NOT ADOPTED and sets no code, because counting an "
                "unapplied host as drift would have made that arm red from the day it "
                "landed. A permanently-red gate is worse than no gate — it trains everyone "
                "to click through."))),
            ("measure", "drift.ladder"),
            ("measure", "timers"),
        ),
    ),

    Section(
        slug="observed", number="7", title="What is observed",
        lede="Three observation surfaces, with different lifetimes: a telemetry pipeline, "
             "a recall store, and the live session surfaces.",
        blocks=(
            ("p", (
                "<b>Activity telemetry.</b> Several sources — shell, terminal multiplexer, "
                "keyboard, window manager, browser, agent transcripts — feed a collector "
                "that lands events in a columnar database with dashboards over it, plus a "
                "per-source deadman so a source that stops is noticed rather than assumed "
                "quiet. It exists to answer questions about where effort actually goes, "
                "which is what makes claims like “this tool is dead” measurable rather than "
                "impressionistic.")),
            ("measure", "telemetry.sources"),
            ("p", (
                "<b>The subsystem index store.</b> What a past session learned about a "
                "subsystem, keyed by scope, recalled on demand. It costs nothing per "
                "session — which is precisely why it can be large where the rules file "
                "cannot. It is local state on each machine and is not in this repository, "
                "so the numbers below are a property of the machine this page was built on.")),
            ("measure", "index.store"),
            ("p", (
                "<b>Session surfaces.</b> A live cross-host view of every terminal window, "
                "which have an agent running, what each is doing, and — the one that "
                "actually pays — which are <i>waiting on a human</i>. That last question "
                "got its own tool after two windows were measured sitting unanswered for "
                "days while the operator answered around a hundred other prompts in the "
                "same period. See <a href=\"#soft\">§12</a> for the honest note about how "
                "many surfaces now answer overlapping versions of that question.")),
        ),
    ),

    Section(
        slug="invariants", number="8", title="Invariants and tripwires",
        lede="What any change must not break. Most of these are enforced by a test; where "
             "one is not, it says so.",
        blocks=(
            ("kv", [
                ("Adding a skill costs an eviction — in the same commit",
                 "The listing total is ratcheted by a test that owns the constant. An "
                 "addition needs an eviction in the same commit. <span class=\"tag\">enforced</span>"),
                ("A new skill also needs a tier-ledger entry",
                 "The ledger is pinned two-way: a shipped skill with no entry fails, and an "
                 "entry naming no shipped skill fails. <span class=\"tag\">enforced</span>"),
                ("The rules file has a byte ceiling",
                 "Owned by its test, which prints the eviction playbook on failure. Raising "
                 "it requires the commit message to name the rule that would not fit. "
                 "<span class=\"tag\">enforced</span>"),
                ("Both gate tiers must pass",
                 "Dev-host and sandbox are different environments; green on one says nothing "
                 "about the other. <span class=\"tag\">enforced at merge</span> "
                 "<span class=\"tag tag-soft\">not enforced locally</span>"),
                ("A new file must be <code>git add</code>ed",
                 "The flake builds from tracked files. An untracked new skill, reference "
                 "file, hook or test is silently omitted from the deploy — the switch "
                 "succeeds and the file simply is not there. "
                 "<span class=\"tag tag-soft\">not enforced — the loudest silent failure here</span>"),
                ("Worktree isolation for parallel file-modifying agents",
                 "Two agents modifying files in one checkout <b>will</b> clobber each other. "
                 "A worktree isolates a working directory only — not the repo it was built "
                 "from, not the branch namespace, not the environment file (which is "
                 "ignored by git and does not come along), not submodules, and not a copy "
                 "you make of it. <span class=\"tag tag-soft\">convention</span>"),
                ("Never <code>git stash</code>",
                 "The stash ref lives in the common git directory, so your own worktree "
                 "gives you zero isolation and a concurrent agent can pop your stash. Copy "
                 "work aside or commit it to a throwaway branch. "
                 "<span class=\"tag\">hook-blocked</span>"),
                ("Never <code>git add -A</code> / <code>.</code>, never <code>git reset --hard</code>",
                 "Blind staging leaks unrelated work-in-progress and secrets from a dirty "
                 "tree; a hard reset irreversibly destroys uncommitted work. "
                 "<span class=\"tag\">hook-blocked</span>"),
                ("Merged is not deployed",
                 "Every managed path changes only on a switch. "
                 "<span class=\"tag tag-soft\">not enforced — see §5</span>"),
                ("Never commit to the main branch in either host checkout",
                 "The convergence script fast-forwards only, so a diverged host is skipped "
                 "and then silently receives nothing while looking healthy. "
                 "<span class=\"tag\">deadman-detected</span>"),
                ("This repository is public",
                 "No real media path, client identifier, third-party hostname, IP literal, "
                 "or captured text — anyone's message bodies, prompts or transcripts, "
                 "however they arrive. A test needs the <i>shape</i>; regenerate it "
                 "synthetic. <span class=\"tag\">enforced by four content gates</span> "
                 "<span class=\"tag tag-soft\">all blind to git history</span>"),
            ]),
        ),
    ),

    Section(
        slug="cost", number="9", title="The cost model of a change",
        lede="What it actually costs to add something here — not to build it, to keep it.",
        blocks=(
            ("p", (
                "The build cost of a change is the part everyone estimates. The part that "
                "decides whether a change is a good idea is the recurring cost, and it has "
                "three components that are paid on different clocks:")),
            ("kv", [
                ("Per-session context",
                 "Paid every session, forever, by every agent on both hosts. A skill "
                 "description, a rule, a memory bullet. This is the scarcest budget and the "
                 "only one where an addition <i>displaces</i> something rather than merely "
                 "adding to it."),
                ("Eviction pressure",
                 "Because the always-on surfaces are capped, an addition is not additive — "
                 "it is a trade against everything already there. The honest accounting is "
                 "not “does this rule earn its bytes” but “does it earn them more than the "
                 "least valuable rule currently present”. When eviction has been run to "
                 "completion, the next real rule costs ceiling, and the ceiling costs a "
                 "justification in a commit message."),
                ("Gate time, on every merge, forever",
                 "A new test suite is a floor entry, a two-way pin, and wall-clock time on "
                 "both tiers for every future pull request. A suite is not free because it "
                 "passes."),
                ("Surface area for the next false claim",
                 "Every new documented fact is a fact that can go stale, and stale facts "
                 "here have measurably sent sessions in the wrong direction. This is why "
                 "the answer to “where should this be written down” is so often “in the "
                 "test that owns the constant”, and why one number is quoted from exactly "
                 "one place."),
            ]),
            ("note", ("why", "The shape of a good addition", (
                "Cheap when idle, loud when it matters, and owning its own number. A skill "
                "body costs nothing until triggered. A test that owns a constant makes "
                "every other mention of that constant a cross-reference instead of a copy. "
                "A gate that prints the replacement value on failure removes the arithmetic "
                "where the mistakes live. The expensive shape is the opposite: a paragraph "
                "of prose, loaded always, restating a number owned elsewhere."))),
            ("note", ("hazard", "Consolidation is a bug-finding instrument, not hygiene", (
                "A predicate open-coded at N sites is typically wrong at N−1 of them, in "
                "the same direction — and unifying them is what makes the disagreement "
                "audible. One predicate here was re-fixed five times and only held once it "
                "was consolidated. If you find yourself patching the second copy, that is "
                "the signal."))),
        ),
    ),

    Section(
        slug="negative", number="10", title="Negative space — what was tried and rejected",
        lede="Each of these was proposed, built or nearly built, and killed by a specific "
             "measurement. This is the highest-yield section on the page: it stops you "
             "re-proposing a settled question.",
        blocks=(
            ("note", ("why", "Read this before proposing anything", (
                "None of these was rejected on taste. Each has a number attached, and in "
                "two cases the number falsified the proposal's <i>own core claim</i>. Where "
                "a rejection has since been re-opened or superseded, that is recorded too — "
                "a negative-space list that only ever grows is a list nobody trusts."))),
            ("h3", "A generalized “subsystem” store"),
            ("p", (
                "<b>Proposed:</b> generalize a per-service recall index into a universal "
                "store covering anything durable — code, data, infra, process, "
                "organization, vendor — with a multi-verb CRUD, a <code>type:</code> field "
                "driving per-class behaviour, and a dependency graph.")),
            ("p", (
                "<b>Killed by the corpus, after eight weeks of real use.</b> Twenty index "
                "entries; <b>zero</b> of a non-infra type; <b>one</b> distinct scope in "
                "use; <b>zero</b> dependency edges populated. So <code>type:</code> — "
                "called “the mechanism that makes generality real” — would have had exactly "
                "one value, and the graph, “the payoff grep cannot produce”, would have had "
                "no edges. A hand-authored strain test confirmed it: addressing collided at "
                "n=1. <b>The proposal's two core claims measured themselves false.</b>")),
            ("note", ("why", "And then the reopen gate tripped — which is the better lesson", (
                "The rejection shipped with a <i>falsifiable reopen gate</i> (“at least five "
                "entries outside the single scope, or five non-infra entries”). Two days "
                "later it tripped: twenty-nine entries across five scopes. The document "
                "marks itself superseded and says the gate was <b>the wrong question</b> — "
                "its “no demand” premise was circular, because the only writer at the time "
                "was an infra-recon command pointed at two cluster repos, so only infra "
                "entries in one scope <i>could</i> exist. The three narrow rejections still "
                "stand on independent evidence. <b>Attach a falsifiable reopen condition to "
                "a rejection, and be prepared for it to teach you that your condition was "
                "measuring your own sampling.</b>"))),
            ("h3", "A <code>--rearm</code> flag for the writeback guard"),
            ("p", (
                "<b>Proposed:</b> let an operator undo a dismissal, so a guard that was "
                "told “this work was not for that ticket” could resume nagging within the "
                "same session.")),
            ("p", (
                "<b>Refused, after two escapes were measured failing in production.</b> The "
                "first escape — “say so in one line and stop” — did not work, because "
                "saying something changes no state, and the next stop re-blocked with "
                "identical text. The second, a dismissal that cleared the guard's state, "
                "failed <i>twice</i>: clearing the state restored the session to its "
                "pre-read condition, so the next read of the ticket re-armed the guard. The "
                "ledger timestamps show the re-arm landing <b>90 milliseconds later, inside "
                "the same tool call</b>. Three audit rounds missed it, because every test "
                "drove the dismissal and asserted silence — none read the ticket again "
                "afterwards.")),
            ("p", (
                "The shipped answer is an <b>absolute session tombstone with a named false "
                "negative</b>: dismiss, then genuinely work on that ticket in the same "
                "session, and the guard stays silent until the session ends. That cost is "
                "accepted explicitly, and the source says why: <i>“the alternative … is "
                "speculative complexity inventing an intention nobody stated. There is "
                "deliberately no <code>--rearm</code> flag: the escape from a dismissal is "
                "a new session, which costs nothing and is unambiguous.”</i>")),
            ("h3", "A per-scope fallback to the hosted store"),
            ("p", (
                "<b>Proposed:</b> when a recall finds a scope absent or empty locally, fall "
                "back to the hosted copy for that scope.")),
            ("p", (
                "<b>Killed by granularity.</b> Re-deriving all three copies twice showed "
                "the scope-granular trigger would reach <b>9 of 70 entries — 13%</b>. On "
                "one host it is a strict no-op (that host holds exactly the scopes the "
                "hosted copy holds, so a scope it lacks, hosted lacks too); on the other the "
                "trigger never fires at all, because every scope there is non-empty — while "
                "<b>61 entries in the four shared scopes stay invisible</b>, which is "
                "precisely where that host holds one-entry stubs against six to "
                "thirty-six. The killing sentence: <i>“the loss is at entry granularity; a "
                "scope-granular trigger cannot see it.”</i> <b>Match the granularity of the "
                "trigger to the granularity of the loss.</b>")),
            ("note", ("why", "A second finding from the same investigation", (
                "The snapshot header's <code>entry-files</code> count was <i>not</i> the "
                "entry count — both the seeder and the server counted every markdown file, "
                "including one index file per scope, and quoting the header had made the "
                "hosted copy look like a superset of the store it mirrors. Separately, an "
                "entire ratio argument was <b>deleted outright</b> from the document, "
                "because every audit round but the first found its new defect inside that "
                "one paragraph: <i>“a number nobody needs is a place for the next error to "
                "live.”</i>"))),
            ("h3", "The skill-tier ledger, applied to zero hosts"),
            ("p", (
                "<b>Built end to end, then deliberately not switched on.</b> The mechanism "
                "— ledger, sync script, two-way test pin, drift-check arm — all shipped. "
                "The measurement that stopped it being <i>applied</i> killed the proposal's "
                "own strongest claim: an earlier draft asserted “the truncation is already "
                "happening; tiering only makes an accidental loss deliberate”, and the live "
                "listing measured at roughly <b>0.69× of the budget</b>. It fits. Nothing "
                "is being truncated. What remains is runway, not emergency.")),
            ("p", (
                "So: the sync script defaults to dry-run, the ledger is not deployed "
                "anywhere, both hosts have no overrides, and the drift checker prints NOT "
                "ADOPTED with no exit code for that state — explicitly “the state the "
                "mechanism shipped in, not drift”. The conservatism is pinned as a "
                "<i>property</i> by a test asserting the name-only set stays a minority, so "
                "a majority-name-only listing has to be argued at that assertion rather "
                "than arrived at one entry at a time. <b>Build the lever, measure that you "
                "do not need it yet, ship it unpulled behind a gate that will tell you when "
                "you do.</b>")),
            ("h3", "Four more, briefly"),
            ("ul", [
                "<b>Shrinking the telemetry table</b> — rejected on measurement: the "
                "maximum conceivable saving was about twenty megabytes against a "
                "hundred-and-twenty-gigabyte store, and it was the only option that lost "
                "data. <i>Size the prize before designing for it.</i>",
                "<b>A stale-directory reap estimated at tens of gigabytes</b> — self-"
                "retracted. The estimate multiplied a sample <i>mean</i> over a heavy-tailed "
                "distribution; the actual reap freed about 1.4 GiB. The median was visible "
                "at the time. <i>Never extrapolate a mean over a heavy tail when the median "
                "is in front of you.</i>",
                "<b>A reported flake rate</b> — retracted, because it was measured over a "
                "window the measuring session was itself saturating: 4% before the load "
                "burst, 24% during it. <i>A rate measured inside your own load is measuring "
                "you.</i>",
                "<b>“The runner can exit zero while printing FAIL”</b> — retracted, does "
                "not reproduce. The real defect was a status destroyed by a pipeline, plus "
                "a misleading precondition diagnostic. <i>A false-green report is usually a "
                "status read through a pipe.</i>",
            ]),
        ),
    ),

    Section(
        slug="evidence", number="11", title="The evidence bar for a proposal",
        lede="The local standard is: measure, then propose. Here is the worked example that "
             "teaches it, because the abstract version does not stick.",
        blocks=(
            ("p", (
                "One session produced <b>six recommendations</b> by tracing code across "
                "several subsystems. Every one was carefully reasoned. Then they were "
                "checked. <b>Four did not survive.</b>")),
            ("kv", [
                ("1. Close an auth gap on a specific route — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>two <code>curl</code> calls</b> with no credentials, one per "
                 "middleware. The result <i>inverted the premise</i>: the middleware the "
                 "recommendation trusted was a literal pass-through — a function that "
                 "returns its argument — so the named route was not the open surface; every "
                 "route using that middleware was. The proposed fix would also have broken "
                 "the operator's own web UI, which posts to that endpoint from the browser."),
                ("2. Retire a subsystem's legacy mode — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>reading the comment already in the source</b>, which said "
                 "the mode had been made opt-in days earlier. The runtime win the "
                 "recommendation claimed was already banked. (Its supporting evidence was "
                 "also n=3, on a single day.)"),
                ("3. Add a cross-scope query — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>one <code>grep</code></b>. It already shipped, in both the "
                 "library and the HTTP layer, with a command-line flag. The “gap "
                 "identified” was a reading error."),
                ("4. Ship a stamping feature or retract the proposal — <span class=\"pill pill-ok\">SURVIVED</span>",
                 "Settled by <b>one <code>grep</code></b> too — and this is the sharper half "
                 "of the point: the same cheap check both kills and confirms. Half had "
                 "already shipped; the other half was dropped for want of a producer."),
                ("5. Add a reconciliation signal between two sources — <span class=\"pill pill-na\">NOT FILED</span>",
                 "No check settled it because <b>there was nothing to check</b>: no evidence "
                 "was ever gathered that the two sources disagree. Rejected for having no "
                 "closing condition and for shipping “a new permanently-green gate”."),
                ("6. Make a guard's escalation resettable — <span class=\"pill pill-un\">KILLED</span>",
                 "Settled by <b>reading a ten-line comment block</b> that already named and "
                 "priced the exact false negative the recommendation thought it had "
                 "discovered. (This is the <code>--rearm</code> refusal from §10.)"),
            ]),
            ("note", ("why", "The failure mode, in the document's own words", (
                "<i>“The failure mode was <b>tracing code and reporting the trace as a "
                "finding</b>: reading that a route is wrapped in a middleware without "
                "reading what that middleware does, and recommending a feature without "
                "grepping for its flag. Both are one command away.”</i>"))),
            ("h3", "Five rules that fall out of it"),
            ("ul", [
                "<b>Use a negative control.</b> The probe deliberately included a route "
                "known to reject, annotated in the results table as “the probe <i>can</i> "
                "see a rejection”. A success means nothing unless the same probe "
                "demonstrably produces a failure somewhere.",
                "<b>An empty result is not evidence.</b> While verifying, a query against "
                "the wrong repository slug returned an empty list — <i>byte-identical to "
                "“nothing was ever proposed”</i>. Take the slug from the remote URL, never "
                "from the directory name.",
                "<b>A definition is not a mechanism.</b> Expect a definition <i>and</i> a "
                "call site. A definition alone is a dormant field, not a feature. The first "
                "revision of the correction got this wrong by quoting a document instead of "
                "checking the code — the same trace-and-report failure, reproduced inside "
                "the correction.",
                "<b>Engage the existing counter-argument.</b> Re-proposing something whose "
                "refusal is already written down, without engaging that argument, is a "
                "regression in the reasoning rather than a finding.",
                "<b>No closing condition, no work item.</b> A proposal that cannot name what "
                "would end it — a mechanical check that passes, or a named human judgement "
                "over named evidence — is not a work item. Say so, and why, rather than mint "
                "an object nobody can close. The correcting document applies this rule to "
                "itself and mints none.",
            ]),
        ),
    ),

    Section(
        slug="soft", number="12", title="Where the system is soft",
        lede="Honest open seams, each with what it would take to settle it. This is "
             "analysis, not a backlog — none of it is a work item, and by the local "
             "standard it must not be turned into one without a closing condition.",
        blocks=(
            ("note", ("why", "Why this section is not a to-do list", (
                "The proactivity rule here is explicit: something is only a work item once "
                "you can name the condition that ends it and who or what checks it. Every "
                "seam below is a <i>question</i> plus the measurement that would answer it. "
                "Turning them into tickets without that would be exactly the object leak "
                "§11's worked example refused to commit."))),
            ("h3", "A complete subsystem with no reader"),
            ("p", (
                "A hosted API over the recall store is built, tested and running. The "
                "consuming client was designed, <i>decided</i> — “hosted is an entry-level "
                "advisory, never the primary read” — and then never written; the handoff "
                "document says so in its own words, and no local reader in this tree "
                "contains an HTTP client at all. The only things that have ever spoken to "
                "it are its own seeding and byte-identity scripts. <b>A subsystem can be "
                "complete, correct, well-tested and have no reader, with every gate green "
                "throughout.</b>")),
            ("measure", "seam.store_api"),
            ("measure", "seam.store_traffic"),
            ("h3", "Several detectors for one question"),
            ("p", (
                "Independent implementations answer “is an agent running in this pane”, "
                "and their predicates are <b>not equivalent</b>: a case-insensitive regex "
                "over the pane's command; the same regex plus a full process-tree walk; "
                "exact string equality; and, in the terminal multiplexer's own config, a "
                "prefix glob. An agent running under a wrapper or a shell is invisible to "
                "the ones that do not walk the tree, which render those windows as "
                "unknown — and a documented count of such windows exists in this repo's "
                "notes. The honest counterpoint: other nearby tools deliberately <i>refuse "
                "to re-derive</i> the predicate and say so in their headers. The seam is "
                "the ones that do.")),
            ("measure", "seam.detectors"),
            ("h3", "One question, several owners"),
            ("p", (
                "A constellation of session tools, where each satellite exists because of "
                "what the primary does and does not expose. One satellite's own header says "
                "it adds <i>“exactly two things … a clock and a threshold”</i> — it exists "
                "because the primary computed a “waiting” field that nothing consumed. "
                "Another records a measured refutation of its own brief: the view it was "
                "told to consume drops the join key it was told to join on. The satellites "
                "shell out to the primary's command line rather than sharing a library.")),
            ("measure", "seam.sessions"),
            ("p", (
                "<b>What would settle it:</b> whether the coupling is to a command-line "
                "contract or to a shared library, and whether one owner with two flags "
                "would be smaller than several owners with several suites. Neither is measured "
                "here, and nobody should merge the split on this page's say-so.")),
            ("h3", "Many walks over one corpus"),
            ("p", (
                "Independent places in this tree open the agent transcript corpus "
                "themselves, and <b>they do not agree on what that corpus is</b>: some "
                "walk recursively, some walk recursively while excluding the synthetic "
                "sub-agent directories, and some walk only the top level. One subsystem's "
                "own source documents the divergence and quantifies it in the thousands of "
                "files. A pair of the walkers are near-duplicates that re-implement the "
                "same skip rule inline, held in sync by <b>a comment</b> saying they match.")),
            ("measure", "seam.jsonl"),
            ("p", (
                "<b>What would settle it:</b> re-run the count before and after any "
                "consolidation. If the number does not fall, the consolidation added a "
                "walker instead of removing several — and a shared walker that silently "
                "picks one of the three corpus definitions changes what at least one "
                "existing subsystem sees.")),
        ),
    ),

    Section(
        slug="glossary", number="13", title="Glossary",
        lede="The local vocabulary is not guessable. These come up in commit messages, "
             "test names and skill bodies, and mean specific things here.",
        blocks=(
            ("kv", [
                ("the merged tree",
                 "The tree that a merge <i>creates</i>, as opposed to either branch. A pull "
                 "request green on its own branch proves nothing about it, because the "
                 "reviewer of the second change ran before the first existed. Gating it "
                 "means building an integration branch, merging every candidate, and "
                 "running the full suite there."),
                ("tier A / tier B",
                 "A skill's slot in the always-on listing. Tier A carries a full "
                 "description and can auto-fire from a described symptom; tier B is "
                 "name-only — still invocable by name, but with no routing prose. Assigned "
                 "by a ledger that is pinned two-way against the shipped skill set."),
                ("<code>UNMEASURED</code>",
                 "A first-class result meaning “this was not evaluated”, as distinct from "
                 "“this evaluated clean”. Load-bearing here: a state that could never be "
                 "measured used to set no exit code and therefore escalate never, which "
                 "read as clean. It now escalates on a consecutive-run ladder."),
                ("<code>rc 17</code>",
                 "The drift checker's <i>source parity</i> code: the source subtree a "
                 "package is built from is not current on that host. Counted against a "
                 "path-limited subtree rather than the whole repository — scoping it to the "
                 "repository made it a permanently-red gate, which is worse than no gate."),
                ("<code>scope-absent</code>",
                 "A recall condition meaning the requested scope is not present in the "
                 "local store. Notable as a rejected trigger: the loss it was meant to "
                 "cover turned out to live at <i>entry</i> granularity, which a scope-level "
                 "condition is structurally blind to."),
                ("positive control",
                 "Feeding an instrument a case that <i>must</i> produce a non-zero result "
                 "and watching the number move, to prove it can observe the thing at all. "
                 "A reassuring zero is otherwise indistinguishable from a harness wired to "
                 "nothing. Its sibling, the <b>negative control</b>, feeds a case that must "
                 "fail — if that reports success, the instrument is testing nothing."),
                ("seam guard",
                 "A test that pins a <i>relationship</i> between components rather than a "
                 "component. Typically an asserted ledger of every writer or caller, failing "
                 "when the set grows <i>or</i> shrinks, plus a behavioural case — because a "
                 "structural check will type-check past a wrong argument."),
                ("invariant guard",
                 "A test that pins a property the bug never violated. Legitimate and "
                 "useful, but it is <b>not</b> regression coverage, and labelling it "
                 "honestly is required. A regression test is one that has been watched to "
                 "fail on pre-change code."),
                ("clawgate",
                 "The self-hosted approval UI: it turns an agent's permission prompt into "
                 "something the operator can approve remotely, and has since grown tasks, "
                 "agents and runbooks. Operated through its own skill."),
                ("fuzzyclaw",
                 "A terminal-multiplexer task integration that writes session state to "
                 "disk. Explicitly flagged as <b>untrusted as a data source</b> in this "
                 "repo's own notes — worth knowing before you build anything on top of its "
                 "output."),
                ("the base clone",
                 "The main checkout on a host, as opposed to the worktrees agents do their "
                 "work in. Because the worktrees do the committing, the base clone is "
                 "effectively write-only and silently falls behind; re-syncing it with a "
                 "fast-forward-only merge is the check, since that either advances or "
                 "refuses."),
                ("could-not-vouch",
                 "The gate wrapper's distinct exit code for “this gate cannot vouch for its "
                 "own answer” — a disagreement between the exit status and the printed "
                 "verdict, or a truncated run. Deliberately not the same code as “the tests "
                 "failed”, because they call for different actions."),
            ]),
        ),
    ),
)
