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
 */
function dispatchableCommands() {
  const out = new Set();

  for (const m of src.matchAll(/command === '([a-z][a-z0-9-]*)'/g)) {
    out.add(m[1]);
  }

  const sw = src.indexOf('switch (command)');
  if (sw === -1) throw new Error('switch (command) not found in query.mjs');
  for (const m of src.slice(sw).matchAll(/^ {6}case '([a-z][a-z0-9-]*)':/gm)) {
    out.add(m[1]);
  }
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
