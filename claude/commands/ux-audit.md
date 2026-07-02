---
name: ux-audit
description: "Dispatch a subagent to click through an app/flow with Playwright, screenshot every view, and evaluate the UX from a non-technical, easily-deterred user's perspective (broken/missing, friction, confusion, overwhelm, can-it-be-simpler). Use to sanity-check a UI before shipping or after a change."
argument-hint: "<app/flow> [url] — e.g. 'the vet signup and onboarding flow', 'the admin dashboard http://localhost:3000'"
allowed-tools: Bash, Read, Agent
---

# /ux-audit — first-impression UX sweep

Goal: kill the repeatedly hand-typed "dispatch a subagent to use playwright to click through … and evaluate" ritual. The lens is a **non-technical, easily-deterred user** — not a developer.

Target: `$ARGUMENTS` (the app/flow to walk; may include a URL). If no target, ask what flow + how to reach it.

## Setup (before dispatching)
- **Get the app reachable.** If a URL is given, use it. Otherwise find how this project runs (project `/run` skill, README, `package.json` scripts, an already-running dev server) and start it if needed; capture the base URL. If it needs auth, note/obtain the test creds. If you can't reach a running app, say so and stop — don't fake it.

## Dispatch a subagent (keep the screenshots out of the main context)
Hand the subagent the base URL + target flow and have it use the **Playwright MCP** (`mcp__playwright__browser_*`) to:
1. **Click through the whole surface** — every page/view/screen/component of the target flow (and the features reachable from it), as a real user would. Don't stop at the happy path; open menus, submit forms, hit empty/error states.
2. **Capture screenshots of every view** it lands on (name them by view).
3. **Evaluate the screenshots + flow** against this rubric (verbatim — the standing rubric):
   - is it intuitive and simple to use from the perspective of a non-technical, easily deterred user
   - is anything broken or missing
   - is the information overwhelming
   - is anything confusing, out of place, or non-obvious
   - are there any walls, points of friction, points of confusion
   - is there any unnecessary friction
   - could it be simplified or made easier
4. **Check responsive design across breakpoints** — resize the viewport to the standard set and re-look at each key view (`mcp__playwright__browser_resize`, then screenshot named by view+width):
   - **375** (mobile) · **768** (tablet) · **1024** (laptop) · **1440** (desktop) · **2560** (ultrawide)

   At each width check, from the same easily-deterred-user lens:
   - **No horizontal overflow / scroll**, especially on small screens. Verify with `document.documentElement.scrollWidth <= window.innerWidth` (via `mcp__playwright__browser_evaluate`); if it overflows, find the widest offending element and name it.
   - **Large screens actually USE the width** — content is not trapped in a thin column with huge empty margins on 1440/2560. Measure the main content container's width across viewports (`getBoundingClientRect().width`); it should grow with the viewport on desktop and cap at a sane readable max on ultrawide — not stay pinned to a narrow column.
   - **Tap targets ≥ ~40px** and reachable on mobile (nothing clipped off-screen or hidden behind chrome).
   - **Layout reflows cleanly** — no clipped, overlapping, or cut-off elements at any width; multi-column layouts collapse to a single readable column on mobile.
   - **Text stays readable** — line length isn't absurdly long on ultrawide (caps via max-width), and isn't crushed/wrapping awkwardly on mobile.

The subagent must actually drive the browser and look at what rendered — not infer from the code.

## Output
A prioritized report (worst-first), each finding with the **view/screenshot**, what a first-time non-technical user would hit, and a concrete fix:
- 🔴 **Broken / blocking** — broken, missing, or dead-end; a user can't get through.
- 🟡 **Friction / confusion** — walls, confusing/overwhelming/out-of-place, unnecessary steps.
- 🟢 **Simplify** — works, but could be easier/cleaner.

End with the **top 3 changes** that would most improve a non-technical user's first run. No marketing language; report what you actually saw, and flag anything you couldn't reach.

Pair: `/verify` (does a specific fix work), `/audit-pr` (code-level review before merge).
