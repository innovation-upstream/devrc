# The browser VALIDATION-PROMPT contract — cite it, don't retype it

**Load this when:** you are about to WRITE or DISPATCH a browser validation /
audit / "go check X on the live site" prompt — for a subagent, for `browser
agent`, or for yourself · you are about to type a wall of READ-ONLY safety rails
into a prompt · someone handed you a prompt and you want to know which rails it
is already standing on.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## Why this file exists

Measured over 2026-08-16..19 (`activity.events`, `source='opencode'`,
`kind='prompt'`): 17 of 114 prompts drove the browser, and the heavy ones ran
**3.0–5.6 KB each**. The task-specific part of each was a few hundred bytes. The
rest was the SAME safety scaffolding retyped every time — READ-ONLY
prohibitions, "don't log out", "report and stop, don't retry", "don't
screenshot", a step budget, a report format.

Retyping it is not just cost, it is drift: each copy was slightly different, and
one copy's omission is invisible. **One rule, one place** — so the rails live
HERE, once, and a prompt CITES them.

## The template

Fill the `<slots>`. Everything not in a slot is already covered by the contract
below and does not belong in your prompt.

```text
TASK: <one sentence — what to find out, and why it matters>
SITE: <url>
INSTANCE: <profile key, or: pick the one logged in as Zach>
BUDGET: <N> pages / <N> ops — a live account, so do not paginate deeply.

Follow the standing browser-validation contract at
~/workspace/devrc/scripts/browser-bridge/reference/validation-prompt.md
(orient, own tab, read-only, report-and-stop, inline reads, cleanup, honesty).

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
6. **INLINE READS ONLY, WHEN DELEGATING.** A dispatched subagent commonly
   cannot read a file back out of `/tmp/*`, and `browser agent` is
   **structurally blind** — the tool layer never puts pixels in its context
   (`reference/agent.md`). So a delegated run must not screenshot and must not
   write files: read with `text [selector]` and with `js` returning JSON.
   ⚠ This rail is about DELEGATION. Driving the browser yourself, `screenshot`
   then `Read` the `.png` is the correct and documented path (SKILL.md ops
   table) — do not let this line talk you out of the one op that can see.
7. **A HIDDEN TAB IS A CONFOUND, NOT A RESULT.** An empty or half-built read
   from a background tab is throttling, not a broken site — `wake` and re-read
   before concluding anything, and re-`wake` after a reload. Never `activate` to
   fix it: that takes the operator's screen. `reference/spa-wake.md`.
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
  run in the prompt; `browser agent`'s own `--steps` is a separate, later bound
  (`reference/agent.md`), and its `steps_used` is not trustworthy.
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
  `reference/agent.md`
- iframe / `--frame` mechanics — `reference/frames-cdp.md`
