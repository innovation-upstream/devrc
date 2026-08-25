#!/usr/bin/env node

/**
 * The JS half of the CROSS-LANGUAGE contract for
 * "the newest comment on this task is NOT the token owner's".
 *
 * 🔴 WHY THIS FILE EXISTS AT ALL. That predicate is implemented TWICE in this
 * repo, in two languages:
 *
 *   * here, as `isAwaiting()` in `lib/awaiting.mjs` — the `awaiting` command;
 *   * in `scripts/check-clickup-addressed/`, where `recent-comments.py` reports
 *     the owner's newest reply and `check-addressed.py::_reply_answers_the_comment`
 *     derives the same predicate from it.
 *
 * ccua already shells out to THIS CLI (`recent-comments.py` runs
 * `node query.mjs my-tasks|comments|me`), so the obvious consolidation is for it
 * to call `query.mjs awaiting` and stop re-deriving. It CANNOT — see the
 * `blockers` array in the fixture and `reference/awaiting-vs-ccua.md`. The
 * decisive one is asserted below rather than argued: `awaiting` structurally
 * drops every task the owner answered last, and that is precisely the population
 * check-addressed.py's suppression note is built from.
 *
 * So the single definition available is a shared TABLE, not shared code:
 * `awaiting-contract.fixtures.json`, read by this file and by
 * `scripts/check-clickup-addressed/tests/test_awaiting_contract.py`. Each side
 * MEASURES its own column — neither copies the other's — and both recompute the
 * divergence ledger from the two columns and pin it, so a divergence appearing
 * OR disappearing is red on both sides.
 *
 * 🔴 THESE ARE SEAM / INVARIANT GUARDS, NOT REGRESSION COVERAGE. No defect is
 * being fixed here; nothing below was red before this file existed because
 * nothing below existed. Their evidence is the MUTATION matrix in the PR body:
 * break either implementation's predicate and a named test here (or in the
 * Python half) goes red with its own message.
 *
 * Every value in the fixture is SYNTHETIC. This repo is public.
 *
 * Usage:
 *   node test/awaiting-contract.test.mjs
 *   node --test test/awaiting-contract.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { isAwaiting, selectAwaiting } from '../lib/awaiting.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(__dirname, 'awaiting-contract.fixtures.json');
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, 'utf8'));

const OWNER = FIXTURE.owner_id;
const CASES = FIXTURE.cases;
const byName = (name) => {
  const c = CASES.find((x) => x.name === name);
  assert.ok(c, `fixture has no case named ${name} — the table and this file disagree`);
  return c;
};

/**
 * What `isAwaiting` yields for a case, in the fixture's own vocabulary.
 *
 * `order-dependent` is DERIVED, never trusted from the table: the list is
 * reversed and the two answers compared. A label nobody can watch fire is
 * indistinguishable from a label wired to nothing, and "the API's ordering
 * decides the verdict" is the exact claim worth being able to watch.
 */
function measureJs(comments) {
  const forward = isAwaiting(comments, OWNER);
  const reversed = isAwaiting([...comments].reverse(), OWNER);
  return forward === reversed ? forward : 'order-dependent';
}

// ── The measured column ──────────────────────────────────────────────────────

for (const c of CASES) {
  test(`CONTRACT[js]: ${c.name}`, () => {
    assert.equal(measureJs(c.comments), c.js,
      `the fixture records js=${JSON.stringify(c.js)} for ${c.name}, but isAwaiting() `
      + `yields ${JSON.stringify(measureJs(c.comments))}. One of the two is wrong; the `
      + 'implementation is the authority, so re-read it before editing the table.');
  });
}

test('CONTRACT[js]: an "order-dependent" label is proven by REVERSING the list, not asserted', () => {
  // The positive control for `measureJs`. Without it, every case could be
  // labelled order-dependent and the label would mean nothing.
  const flippy = CASES.filter((c) => c.js === 'order-dependent');
  assert.ok(flippy.length > 0, 'no order-dependent case left in the table — if isAwaiting() '
    + 'became order-independent that is a FIX, but this control and the fixture must move with it');
  for (const c of flippy) {
    const forward = isAwaiting(c.comments, OWNER);
    const reversed = isAwaiting([...c.comments].reverse(), OWNER);
    assert.notEqual(forward, reversed,
      `${c.name} is labelled order-dependent but isAwaiting() returned ${forward} in BOTH `
      + 'directions — the label is now false');
  }
});

test('CONTRACT[js]: an "undecidable" contract row has a non-boolean on at least one side', () => {
  // Otherwise the word is decoration: if both implementations answer a clean
  // boolean, the fact WAS decidable and the table is lying about the spec.
  for (const c of CASES.filter((x) => x.awaiting === 'undecidable')) {
    assert.ok(typeof c.js !== 'boolean' || typeof c.py !== 'boolean',
      `${c.name} is marked undecidable but both sides record a boolean `
      + `(js=${c.js}, py=${c.py}) — then it is decidable and the contract column is wrong`);
  }
});

test('CONTRACT[js]: where both sides answer a boolean, they equal the contract', () => {
  const checked = [];
  for (const c of CASES) {
    if (typeof c.js !== 'boolean' || typeof c.py !== 'boolean') continue;
    assert.equal(typeof c.awaiting, 'boolean',
      `${c.name}: both implementations answer a boolean, so the contract must too`);
    assert.equal(c.js, c.awaiting, `${c.name}: JS disagrees with the contract`);
    assert.equal(c.py, c.awaiting, `${c.name}: Python disagrees with the contract`);
    checked.push(c.name);
  }
  assert.ok(checked.length >= 5,
    `only ${checked.length} case(s) exercised the agreement claim — a table whose cases are `
    + 'nearly all divergent proves the two are equivalent nowhere');
});

// ── The divergence ledger, recomputed from the two columns ───────────────────

test('CONTRACT[js]: the divergence ledger is exactly the set the two columns produce', () => {
  // 🔴 Asserted as a SET that fails when it GROWS *or* SHRINKS. A ledger that
  // only catches growth blesses a silent narrowing, and a divergence that
  // disappears is a behaviour change on a live command.
  const derived = CASES.filter((c) => c.js !== c.py).map((c) => c.name).sort();
  const pinned = [...FIXTURE.divergences].sort();
  assert.deepEqual(derived, pinned,
    'the pinned `divergences` ledger no longer matches the table.\n'
    + `  derived: ${JSON.stringify(derived)}\n`
    + `  pinned:  ${JSON.stringify(pinned)}\n`
    + 'If an implementation changed, update BOTH the case row and this ledger, and say in '
    + 'the commit which behaviour moved.');
});

test('CONTRACT[js]: isAwaiting() cannot express the UNIDENTIFIED distinction', () => {
  // The single most important row in the table: it is why a consolidation onto
  // this side would be LOSSY, and it is a property of the RETURN TYPE, so it
  // cannot be fixed by care inside the function.
  const c = byName('the_owners_own_comment_has_an_unreadable_date');
  assert.equal(c.py, 'unverified',
    'the Python pipeline is supposed to decline to claim here (UNIDENTIFIED)');
  assert.equal(typeof isAwaiting(c.comments, OWNER), 'boolean',
    'isAwaiting() grew a third state — if that is deliberate, this guard and the fixture '
    + 'both move, and ccua may finally be able to consume this command');
  assert.equal(isAwaiting(c.comments, OWNER), true,
    'and the boolean it returns is a CONFIDENT true over a thread it cannot rank');
});

// ── The structural blocker, asserted rather than argued ──────────────────────

test('CONTRACT[js]: `awaiting` emits NO row for a task the owner answered last', () => {
  // 🔴 This is the blocker. The rows dropped here are exactly the ones
  // check-addressed.py's ANSWERED/suppression note is built from — the note
  // that carries the bot-identity caveat. A consumer fed by this command would
  // lose that whole block with nothing failing.
  const c = byName('owner_commented_last');
  const sel = selectAwaiting({
    tasks: [{ id: 'T-SYNTH-1', name: 'synthetic task', url: 'https://example.invalid/t/1' }],
    commentsByTaskId: new Map([['T-SYNTH-1', c.comments]]),
    ownerUserId: OWNER,
    now: FIXTURE.now_ms,
  });
  assert.equal(sel.rows.length, 0,
    'selectAwaiting() now emits a row for a task the owner answered last. That would be a '
    + 'change to the command\'s definition — and it would also retire blocker "population" '
    + 'in the fixture, so move both.');
  assert.equal(sel.withComments, 1,
    'the task was examined and HAD comments — the zero above is a verdict, not an empty scan');
});

test('CONTRACT[js]: an `awaiting` row carries exactly these fields, and no comment body', () => {
  // An exact key ledger rather than "does not contain `text`": a spelled guard
  // on one absent word passes while the hazard exists under another spelling,
  // and this ledger is what the `field` blockers are measured against.
  const c = byName('colleague_commented_last');
  const sel = selectAwaiting({
    tasks: [{
      id: 'T-SYNTH-2',
      name: 'another synthetic task',
      url: 'https://example.invalid/t/2',
      list: { name: 'Synthetic List' },
      status: { status: 'to do' },
    }],
    commentsByTaskId: new Map([['T-SYNTH-2', c.comments]]),
    ownerUserId: OWNER,
    now: FIXTURE.now_ms,
  });
  assert.equal(sel.rows.length, 1, 'the fixture\'s canonical matching case stopped matching');
  assert.deepEqual(Object.keys(sel.rows[0]).sort(), [...FIXTURE.awaiting_row_keys].sort(),
    'the `awaiting` row shape changed. Update `awaiting_row_keys` in the fixture AND re-read '
    + 'the `blockers` entries — a new field may have retired one of them.');
});

test('CONTRACT[js]: every blocker carries a kind and a reason', () => {
  // A blocker with no reason is a sentence that was true once. The Python half
  // asserts the same over the same array, so neither reader can drift alone.
  const KINDS = new Set(['population', 'field', 'failure-mode']);
  assert.ok(FIXTURE.blockers.length >= 5,
    'the blocker ledger shrank — if a blocker was genuinely closed, say which in the commit');
  for (const b of FIXTURE.blockers) {
    assert.ok(KINDS.has(b.kind), `unknown blocker kind ${JSON.stringify(b.kind)}`);
    assert.ok(typeof b.field === 'string' && b.field.length > 0, `blocker ${b.kind} names no field`);
    assert.ok(typeof b.why === 'string' && b.why.length > 40,
      `blocker ${b.kind}/${b.field} carries no usable reason`);
  }
});
