#!/usr/bin/env node

/**
 * Help-coverage gate (hermetic — no credentials, no network).
 *
 * SKILL.md deliberately does NOT list the commands: it routes to
 * `node query.mjs` and lets showUsage() be the command reference. That
 * only works if showUsage() is COMPLETE, so this pins it:
 *
 *   every command query.mjs can dispatch  ==  every command showUsage() prints
 *
 * Before this gate existed, SKILL.md's hand-maintained tables had drifted to
 * 56 of 68 commands — the whole webhook-watcher group and batch-create were
 * undocumented. Deleting those tables is only safe while this stays green.
 *
 * Written in `node:test` form and named `*.test.mjs` because that is what
 * devrc's node gate DISCOVERS (`scripts/run-node-tests.sh`, which globs
 * `scripts/**` and `claude/**`). A bespoke script with its own RESULT line was
 * invisible to it — an ungated gate, the shape that runner's header is entirely
 * about. It still runs standalone: `node test/help-coverage.test.mjs`.
 *
 * Usage:
 *   node test/help-coverage.test.mjs
 *   node --test test/help-coverage.test.mjs
 *   VERBOSE=1 node test/help-coverage.test.mjs   # print both resolved sets
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { blankComments, balancedBlockAfter, topLevelCaseLabels } from './js-source.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const QUERY = resolve(__dirname, '..', 'query.mjs');
const VERBOSE = process.argv.includes('--verbose') || process.env.VERBOSE === '1';

// A parser that silently matches nothing would report "0 missing" and look
// like an all-clear. These floors are the positive control: they assert each
// extractor actually observed something before any comparison is believed.
const MIN_DISPATCHABLE = 50;
const MIN_DOCUMENTED = 50;

const src = readFileSync(QUERY, 'utf8');

/** The template literal inside showUsage(). */
function extractUsageText() {
  const start = src.indexOf('function showUsage()');
  if (start === -1) throw new Error('showUsage() not found in query.mjs');
  const open = src.indexOf('`', start);
  const close = src.indexOf('`', open + 1);
  if (open === -1 || close === -1) {
    throw new Error('showUsage() template literal not found');
  }
  return src.slice(open + 1, close);
}

/**
 * Command names printed by showUsage().
 *
 * A command line is two-space indented, e.g. `  get <url|id>   Get task...`.
 * Excluded: `--flags` (Options section) and `node query.mjs ...` illustration
 * lines (Bulk Operations / Examples sections), which are not command entries.
 */
function documentedCommands(usage) {
  const out = new Set();
  for (const line of usage.split('\n')) {
    if (/^\s{2,}node\s/.test(line)) continue; // illustration, not an entry
    const m = /^ {2}([a-z][a-z0-9-]*)(?:\s|$)/.exec(line);
    if (m) out.add(m[1]);
  }
  return out;
}

/**
 * Commands query.mjs can actually dispatch. Two dispatch forms coexist:
 *   1. `if (command === 'x')` guards, ahead of the main switch
 *   2. `case 'x':` labels inside `switch (command) {`
 * Both are read, because missing either understates the real surface.
 *
 * 🔴 NEITHER READER MAY BE PINNED TO A STYLE. Both were:
 *   * `command === '([a-z…])'` required single quotes and exactly one space
 *     either side of `===`;
 *   * `^ {6}case '…':` required SIX spaces of indentation and single quotes.
 * A `case "x":`, an eight-space `case` inside a nested block, or a reformatted
 * `command==='x'` was dispatchable, undocumented, and green — and nothing in
 * this repo enforces the assumed style: there is no prettier/eslint config for
 * `claude/skills/`, so the layout these guards depend on is a coincidence.
 *
 * The `case` reader is now DEPTH-based over the brace-matched body of
 * `switch (command) { … }`: quote- and indentation-agnostic, and it still
 * excludes the labels of any NESTED switch (which are not command dispatch and
 * would inflate the set with phantom commands).
 */
function dispatchableCommands(source = src) {
  const out = new Set();

  // Comments blanked: a `// case 'foo':` note or a commented-out dispatch is
  // not a dispatchable command, and counting one would make this gate demand
  // documentation for a command that does not exist.
  const code = blankComments(source);

  for (const m of code.matchAll(/command\s*===\s*(['"])([a-z][a-z0-9-]*)\1/g)) {
    out.add(m[2]);
  }

  const block = balancedBlockAfter(code, 'switch (command)');
  if (!block) throw new Error('switch (command) { … } not found in query.mjs');
  for (const name of topLevelCaseLabels(code, block)) out.add(name);
  return out;
}

const usage = extractUsageText();
const documented = documentedCommands(usage);
const dispatchable = dispatchableCommands();

if (VERBOSE) {
  console.log(`dispatchable (${dispatchable.size}): ${[...dispatchable].sort().join(', ')}`);
  console.log(`documented   (${documented.size}): ${[...documented].sort().join(', ')}`);
}

// ── Positive controls: each extractor must have observed something ──────────

test('POSITIVE CONTROL: the dispatch extractor found a plausible number of commands', () => {
  assert.ok(
    dispatchable.size >= MIN_DISPATCHABLE,
    `EXTRACTOR BROKEN: found only ${dispatchable.size} dispatchable commands ` +
      `(floor ${MIN_DISPATCHABLE}). The dispatch shape in query.mjs changed — ` +
      `fix dispatchableCommands(), do not lower the floor.`
  );
});

test('POSITIVE CONTROL: the showUsage() extractor found a plausible number of commands', () => {
  assert.ok(
    documented.size >= MIN_DOCUMENTED,
    `EXTRACTOR BROKEN: found only ${documented.size} documented commands ` +
      `(floor ${MIN_DOCUMENTED}). showUsage()'s format changed — ` +
      `fix documentedCommands(), do not lower the floor.`
  );
});

// ── CONTROLS for the dispatch extractor's style-independence ───────────────
//
// The floors above prove the extractor found SOMETHING. They cannot prove it
// would find a command written in a style query.mjs does not currently use —
// and an UNDER-count is the dangerous direction: it makes an undocumented
// command invisible to the gate that exists to notice it. So: a synthetic
// query.mjs, written in every style no formatter forbids.

const SYNTHETIC = `
const command = process.argv[2];
if (command === 'alpha') { doAlpha(); }
if (command === "bravo") { doBravo(); }
if (command==='charlie') { doCharlie(); }
// if (command === 'ghost-guard') { never(); }
switch (command) {
      case 'delta':
        return doDelta();
      case "echo":
        return doEcho();
  case 'foxtrot':
        return doFoxtrot();
        case 'golf': {
          switch (subcommand) {
            case 'nested-not-a-command':
              return nested();
          }
          return doGolf();
        }
      // case 'ghost-case':
      case 'hotel':
        return doHotel();
}
`;

test('CONTROL: the dispatch extractor is quote- and indent-agnostic', () => {
  const found = dispatchableCommands(SYNTHETIC);
  const expected = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel'];
  const missed = expected.filter((c) => !found.has(c));
  assert.ok(missed.length === 0,
    `the dispatch extractor MISSED ${missed.join(', ')} — each is dispatchable and each ` +
      'is written in a style nothing in this repo forbids (double quotes, 2/6/8-space ' +
      'indentation, no spaces around ===). A missed command is an undocumented command ' +
      'this gate reports as fine.');
});

test('CONTROL: the dispatch extractor counts no phantom commands', () => {
  const found = [...dispatchableCommands(SYNTHETIC)].sort();
  const expected = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel'];
  assert.deepEqual(found, expected,
    'the dispatch extractor reported commands that are NOT command dispatch. A NESTED ' +
      "switch's labels and a commented-out `case` are the two ways this happens, and each " +
      'would make the gate demand showUsage() document something that does not exist.');
});

// ── The gate itself, both directions ───────────────────────────────────────

test('every dispatchable command is printed by showUsage()', () => {
  const undocumented = [...dispatchable].filter((c) => !documented.has(c)).sort();
  assert.ok(
    undocumented.length === 0,
    `HELP INCOMPLETE: ${undocumented.length} command(s) dispatch but are not ` +
      `printed by showUsage(): ${undocumented.join(', ')}\n` +
      `  SKILL.md routes users to \`node query.mjs\` instead of listing commands, ` +
      `so an unprinted command is invisible. Add it to showUsage().`
  );
});

test('every command printed by showUsage() can actually dispatch', () => {
  const dead = [...documented].filter((c) => !dispatchable.has(c)).sort();
  assert.ok(
    dead.length === 0,
    `HELP LIES: ${dead.length} command(s) are printed by showUsage() but cannot ` +
      `dispatch: ${dead.join(', ')}\n` +
      `  Remove them from showUsage(), or implement them.`
  );
});
