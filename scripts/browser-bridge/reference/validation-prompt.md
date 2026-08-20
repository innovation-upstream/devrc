# The browser VALIDATION-PROMPT contract — cite it, don't retype it

**Load this when:** you are about to WRITE or DISPATCH a browser validation /
audit / "go check X on the live site" prompt · you are about to type a wall of
READ-ONLY safety rails into a prompt · someone handed you a prompt and you want
to know which rails it is already standing on.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## 🔴 FIRST: can your reader open this file?

The rails below are carried by a filesystem path. That works only for a reader
with a file-read tool.

- **A Claude Code subagent, or you** — can `Read` the path. **CITE** it: use the
  template's citation line and keep the prompt short.
- **`browser agent`** — **CANNOT.** Its model is given exactly one typed tool
  with an 11-op browser-only surface (`text`/`html`/`eval`/`nav`/`screenshot`/
  `frames`/`click`/`type`/`key`/`wake`/`whoami`), and the agent def denies
  `bash`/`read`/`edit`/`write`/`webfetch` — enforced at runtime by a fail-closed
  gate before the model is invoked
  (`~/workspace/devrc/scripts/browser-bridge/reference/agent.md`). A citation
  reaches it as an unreadable string. **INLINE the block below instead.**

Getting this backwards is the failure this file could otherwise cause: a cited
prompt to `browser agent` ships with **zero** rails while still reading
complete, and that model has `click`/`type`/`key`/`nav`/`eval` on the operator's
live logged-in Brave.

## The inline rails — paste this when the reader cannot read files

```text
RAILS (standing browser-validation contract):
1 ORIENT: run `whoami` first; the wrong profile is the commonest wasted run.
  If extension_stale is true, say so in the report rather than fighting it.
2 STAY IN YOUR OWN TAB: whatever dispatched you already put you in a tab --
  stay in it, and do not try to acquire another. Never `nav` a tab the
  operator may be using, and never `nav` away to something unrelated: a tab
  can hold unsaved work.
3 READ-ONLY: no Connect/Follow/Message/Invite/Save/Subscribe/Send. No form
  submit except the one named in WRITES ALLOWED. Change no account, privacy or
  notification setting. NEVER log out. Never click a control that spends money
  or quota -- Generate/Render/Create/Buy/Publish -- however obvious it looks.
4 BLAST RADIUS: touch only what WRITES ALLOWED permits. DO NOT TOUCH is a hard
  list. Anything you create for the test is yours to delete.
5 A FAILURE IS A FINDING: report it and STOP. Do not retry aggressively or
  engineer around it. A 429, a throttle notice, a permission wall or an
  unexpected redirect is DATA. If a selector misses, try ONE alternative, then
  report the miss.
6 READS: you cannot see images -- your tool never returns pixels. Read with
  `text [selector]` and with `js` returning JSON. Do not plan around writing
  files you intend to read back.
7 A HIDDEN TAB IS A CONFOUND, NOT A RESULT: an empty or half-built read from a
  background tab is throttling, not a broken site. `wake` and re-read, and
  re-`wake` after a reload. Never `activate` -- it takes the operator's screen.
8 CLEAN UP: delete throwaway records you created and confirm they are gone.
  Do not try to close your tab -- whatever opened it closes it for you.
9 REPORT HONESTLY: quote verbatim rather than summarising away the raw ids,
  counts and strings. State the sample size and what it cannot support. Say
  what you could NOT check. Do not round toward a conclusion.
```

## Why this file exists

Measured over 2026-08-16..19 (`activity.events`, `source='opencode'`,
`kind='prompt'`): of 125 prompts, **8** named the bridge, the `browser` skill or
`browser agent` — and **7 of those 8 ran 3.0–5.6 KB**. (A lower bound: the count
is what that literal filter matched, and a handful of shorter `Goal:` prompts
drove the browser by another spelling.) The task-specific part of each was a few
hundred bytes. The rest was the SAME safety scaffolding retyped every time —
READ-ONLY prohibitions, "don't log out", "report and stop, don't retry", "don't
screenshot", a step budget, a report format.

Retyping it is not just cost, it is drift: each copy was slightly different, and
one copy's omission is invisible. **One rule, one place** — so the rails live
HERE, once, and a prompt either cites them or pastes the block above.

## The template

Fill the `<slots>`. Everything not in a slot is already covered by the contract
and does not belong in your prompt.

```text
TASK: <one sentence — what to find out, and why it matters>
SITE: <url>
INSTANCE: <profile key, or: pick the one logged in as Zach>
BUDGET: <N> pages / <N> ops — a live account, so do not paginate deeply.

Follow the standing browser-validation contract at
~/workspace/devrc/scripts/browser-bridge/reference/validation-prompt.md
— all nine rails: orient, own tab, read-only, blast radius,
failure-is-a-finding, reads, hidden-tab confound, clean up, report honestly.
(Cannot read that path? Say so and stop — do not proceed unrailed.)

WRITES ALLOWED — nothing else: <e.g. the search box + its Submit; or: none>
DO NOT TOUCH: <records/entities that are not ours>

OBSERVE
1. <specific observation>
2. <specific observation>

THE DISCRIMINATOR
<the ONE observation that changes the conclusion — and what each outcome
would mean. Without this the report comes back agreeable and useless.>

REPORT
1. <deliverable>
2. <deliverable>
3. anything that errored, surprised you, or that you could NOT check
```

## The standing contract — what the prompt no longer has to say

A prompt citing this file is asking for all nine. Do not weaken one silently; if
a task needs an exception, name it in the prompt.

1. **ORIENT FIRST.** `browser whoami` before anything else — both hosts are
   hostname `nixos`, and picking the wrong profile is the single commonest
   wasted run. If `extension_stale` is true, SAY SO in the report rather than
   fighting it.
2. **YOUR OWN TAB.** `open` a new tab this session owns. Never `nav` a tab the
   operator may be using — it can hold unsaved work.
3. **READ-ONLY BY DEFAULT.** No Connect / Follow / Message / Invite / Save /
   Subscribe / Send. No form submit except the one named in WRITES ALLOWED. No
   account, privacy or notification setting changed. **Never log out.** Never
   click a control that spends money or quota — Generate / Render / Create /
   Buy / Publish — even when it looks like the obvious next step.
4. **BLAST RADIUS IS THE NAMED SET.** Touch only what WRITES ALLOWED permits.
   DO NOT TOUCH is a hard list, not a preference. Anything you create for the
   test is a throwaway and is yours to delete (see 8).
5. **A FAILURE IS A FINDING — REPORT AND STOP.** Do not retry aggressively and
   do not engineer around it. A 429, a throttle notice, a permission wall or an
   unexpected redirect is DATA about the system under test. If a selector
   misses, try ONE alternative, then report the miss; do not grind.
6. **INLINE READS ONLY, WHEN DELEGATING.** `browser agent` is **structurally
   blind** — its tool layer never puts pixels in the model's context, on any
   model (`~/workspace/devrc/scripts/browser-bridge/reference/agent.md`). A
   dispatched subagent MAY also be unable to read a file back out of `/tmp/*`;
   one run in the corpus died exactly there, and others read `/tmp` fine — so
   **probe it, do not assume it**: have the delegate `screenshot` once and
   `Read` the `.png`, and report-and-stop if that fails. Absent a passing probe,
   read with `text [selector]` and with `js` returning JSON.
   ⚠ This rail is about DELEGATION. Driving the browser yourself, `screenshot`
   then `Read` the `.png` is the correct and documented path (SKILL.md ops
   table) — do not let this line talk you out of the one op that can see.
7. **A HIDDEN TAB IS A CONFOUND, NOT A RESULT.** An empty or half-built read
   from a background tab is throttling, not a broken site — `wake` and re-read
   before concluding anything, and re-`wake` after a reload. Never `activate` to
   fix it: that takes the operator's screen.
   `~/workspace/devrc/scripts/browser-bridge/reference/spa-wake.md`.
8. **CLEAN UP.** Close the tab you opened. Delete throwaway records you created,
   and confirm they are gone. If anything took the operator's screen, restore
   BOTH the focused window and the workspace — including on failure.
9. **REPORT HONESTLY.** Quote verbatim; do not summarise away the raw ids,
   counts or strings that were the point. State the sample size and what it
   cannot support. Say what you could NOT check. Do not round toward a
   conclusion — "ambiguous" is a valid answer and a useful one.

## Slots worth thinking about

- **INSTANCE** — the profile matters more than it looks: access differs per
  profile, and the wrong one returns a plausible 404 rather than an error.
- **BUDGET** — a live logged-in account throttles on unusual activity. Bound the
  run in the prompt; `browser agent`'s own `--steps` is a separate, later bound,
  and its `steps_used` is not trustworthy.
- **THE DISCRIMINATOR** — the highest-value slot. Name the observation whose two
  outcomes point at different conclusions, and say what each would mean. A
  prompt without one gets a report that agrees with whatever you implied.
- **DO NOT TOUCH** — name the records explicitly. "Be careful" is not a guard;
  an enumerated list is.

## What NOT to put in the prompt

These are already true and restating them only adds bytes and drift surface:

- how to call the bridge, or its path — SKILL.md's Quick start has it
- that `js` evaluates ONE expression — SKILL.md trap 1
- that GitHub-class CSP blocks injection so `text`/`html` are the way — trap 2
- the `browser agent` output envelope, guardrails or privacy boundary —
  `~/workspace/devrc/scripts/browser-bridge/reference/agent.md`
- iframe / `--frame` mechanics —
  `~/workspace/devrc/scripts/browser-bridge/reference/frames-cdp.md`
