#!/usr/bin/env node

/**
 * Gates for the `claw:obj` agent-object marker on the ClickUp side
 * (Phase 0 of agent-object hygiene). Hermetic — no credentials, no network.
 *
 * Five tiers, and the last three are the ones that could actually be missing:
 *
 *  1. UNIT — build/parse round-trip, the enumerated `cond` allowlist, malformed
 *     input, optional fields absent.
 *  2. CROSS-LANGUAGE — this file and talos-infra's
 *     `scripts/lib/agent_obj_marker.py` are two implementations of ONE grammar,
 *     and no byte-mirror can bridge Python and JS. The VECTORS below are the
 *     bridge: literal strings and fingerprints computed from the PYTHON side and
 *     pinned here, so a change to one implementation goes red until the other
 *     follows. Without this the two drift silently and a reconciler written
 *     against one stops seeing objects stamped by the other.
 *  3. SEAM — createTask/createSubtask actually stamp. 🔴 Tiers 1 and 2 can be
 *     entirely green with the stamp wired to nothing; the leak Phase 0 exists to
 *     close lives on the producer side, not in the helper.
 *  4. CHOKE POINT — the stamp is applied in ONE place, not per call site. A
 *     per-call-site stamp regenerates the same omission at every new caller.
 *  5. PROSE — flows/task-hygiene.md's load-bearing sentences, pinned as WHOLE
 *     NORMALISED STRINGS. 🔴 Its no-enforcement disclaimer was guarded by
 *     nothing: flipping it into a false claim of enforcement went fully green.
 *     A guard on WORDS is walkable by rewording, so this pins the paragraph.
 *
 * Usage:
 *   node test/agent-marker.test.mjs
 *   node --test test/agent-marker.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

import {
  MARKER_VERSION,
  COND_KINDS,
  COND_KINDS_NO_ARG,
  COND_UNSTATED,
  MarkerError,
  AGENT_LABEL_PREFIX,
  labelFor,
  validateCond,
  fingerprint,
  buildMarker,
  parseMarker,
  agentIdentity,
  stampDescription,
  DEFAULT_PRODUCER,
} from '../lib/agent-marker.mjs';
import { applyAgentStamp, createTask } from '../api/tasks.mjs';
import { validatePlanConds } from '../lib/batch-create.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TASKS_SRC = readFileSync(resolve(__dirname, '..', 'api', 'tasks.mjs'), 'utf8');
const QUERY_SRC = readFileSync(resolve(__dirname, '..', 'query.mjs'), 'utf8');
const MAINTAINING_SRC = readFileSync(resolve(__dirname, '..', 'reference', 'maintaining.md'), 'utf8');
const HYGIENE_SRC = readFileSync(resolve(__dirname, '..', 'flows', 'task-hygiene.md'), 'utf8');

/** Collapse whitespace so a line-wrap in prose is not a false failure. */
const normalise = (s) => s.replace(/\s+/g, ' ').trim();

// ── Tier 2: the cross-language pin ──────────────────────────────────────────
// Every value here was produced by the PYTHON implementation
// (talos-infra scripts/lib/agent_obj_marker.py) and pasted in. Do not
// "regenerate" them from this file — that would make the pin self-referential
// and it would agree with any drift.
const VECTORS = Object.freeze({
  minimal: {
    args: { srcProducer: 'capacity-sweep', srcRunId: 'sweep-29159872-abcde', cond: 'cmd_exit_zero:drift-check' },
    marker: '<!-- claw:obj v=1 src=capacity-sweep/sweep-29159872-abcde cond=cmd_exit_zero:drift-check -->',
  },
  full: {
    args: {
      srcProducer: 'capacity-sweep',
      srcRunId: 'run-1',
      cond: 'gh_pr_merged:civitai/talos-infra#1234',
      fp: '0123456789ab',
      init: 'agent-hygiene',
    },
    marker:
      '<!-- claw:obj v=1 fp=0123456789ab src=capacity-sweep/run-1 '
      + 'cond=gh_pr_merged:civitai/talos-infra#1234 init=agent-hygiene -->',
  },
  fingerprints: [
    [{ producer: 'capacity-sweep', surface: 'node-disk-free' }, 'ffa496f6e083'],
    [{ b: 1, a: { z: 2, y: [3, 'x'] } }, 'f71b983349d5'],
    [{ producer: 'reliability-sweep-advisor', claim: 'index-candidates' }, '633d2c372bd0'],
  ],
  condKinds: ['alert_cleared', 'cmd_exit_zero', 'gh_pr_merged', 'manual', 'metric_below'],
  // A LEGACY marker carrying the bare `manual` NEITHER side emits any more but
  // both must still READ — objects stamped before the arity rule carry it and are
  // live right now. (An earlier note called this "Python-produced": Python stopped
  // emitting it in civitai/talos-infra#1286, which merges before this. The corpus,
  // not the producer, is what keeps the read path open.)
  legacyBareManual: '<!-- claw:obj v=1 src=capacity-sweep/run-1 cond=manual -->',
});

// ── The Python half: WHAT was measured, and FROM WHAT ───────────────────────
//
// 🔴 A CROSS-LANGUAGE CLAIM MUST CARRY ITS OWN PROVENANCE, or it is a sentence
// that was true once. The previous version of this block was three JS-side
// booleans asserted against their own literals — self-referential, so Python-side
// drift was structurally invisible AND the claims were already wrong by the time
// they were read (they described the pre-#1286 Python). Nothing here can make a
// hermetic JS suite READ Python, but it can make a stale claim TRACEABLE: the
// exact file, the exact immutable commit, its sha256, and the date measured.
const PYTHON_SIDE = Object.freeze({
  repo: 'civitai/talos-infra',
  path: 'scripts/lib/agent_obj_marker.py',
  // An IMMUTABLE ref, deliberately — `origin/trunk` moves, so a claim pinned to
  // it cannot be re-checked later and reads as current forever.
  ref: '9e21058fc6a420bb89a4a0826d5163f9de10b622',
  branch: 'zach/marker-manual-names-checker', // PR civitai/talos-infra#1286, merges BEFORE this
  sha256: 'ea39f91abd3dc031a58b710fa930fe9404d54e8aac3e4403335feea169dbe4cb',
  measuredOn: '2026-08-24',
  // Re-measure with:
  //   git -C <talos-infra> show <ref>:scripts/lib/agent_obj_marker.py
  // or arm the staleness check below:
  //   AGENT_OBJ_MARKER_PY=<talos-infra>/scripts/lib/agent_obj_marker.py node --test …
  armEnv: 'AGENT_OBJ_MARKER_PY',
});

// 🔴 THE DIVERGENCE FROM PYTHON, ENUMERATED — because tier 2 above otherwise
// reads as "these two implementations agree", and in ONE place they do not.
//
// AGREEMENTS (recorded, because "they agree" is a claim and this is its evidence):
//   * bare `manual`  — BOTH refuse to emit it; BOTH still parse it. Python's
//     `validate_cond('manual')` raises "cond kind 'manual' must NAME who checks
//     it — write `manual:<who>`"; `validate_cond('manual', allow_bare_manual=True)`
//     returns it, and `parse_marker` passes that opt-in.
//   * `unstated`     — BOTH refuse it as caller input; BOTH parse it.
//     Python: "cond %r records a MISSING condition and is never accepted as input".
//   * the ALLOWLIST OF KINDS is identical, which is why the VECTORS pin holds.
//
// THE ONE DIVERGENCE — the missing-cond FALLBACK:
//   JS has one and Python does not. `agentIdentity()` observes an absent/unusable
//   `CLAW_AGENT_COND` and yields `unstated`, which `applyAgentStamp()` then emits
//   through the single `allowUnstated` call site. On the Python side `cond` is a
//   REQUIRED POSITIONAL of `build_marker(src_producer, src_run_id, cond, …)` and
//   `build_marker` calls `validate_cond(cond)` with NO opt-ins, so "no condition"
//   is a TypeError at authoring time and `cond=unstated` is unemittable there.
//   Consequence for a reconciler: every `cond=unstated` object in the corpus was
//   stamped by THIS side.
//
// Closing the divergence means giving Python a fallback (or removing JS's) and
// moving these notes with it. Until then this block is the honest record.
const PYTHON_DIVERGENCE = Object.freeze({
  // Asserted BEHAVIOURALLY below — this half lives in this repo.
  jsProducesUnstatedFromItsFallback: true,
  // RECORDED from PYTHON_SIDE — not observable from here.
  pythonCanEmitUnstated: false,
  bothRefuseToEmitBareManual: true,
  bothStillParseBareManual: true,
  bothRejectUnstatedAsInput: true,
});

// ── Tier 1: unit ────────────────────────────────────────────────────────────

test('buildMarker: minimal marker omits the optional fields', () => {
  const got = buildMarker(VECTORS.minimal.args);
  assert.equal(got, VECTORS.minimal.marker);
  assert.ok(!got.includes('fp='));
  assert.ok(!got.includes('init='));
});

test('buildMarker: full marker emits fields in canonical order', () => {
  assert.equal(buildMarker(VECTORS.full.args), VECTORS.full.marker);
});

test('buildMarker: every allowlisted cond kind builds', () => {
  for (const kind of COND_KINDS) {
    const cond = COND_KINDS_NO_ARG.includes(kind) ? kind : `${kind}:x`;
    assert.ok(buildMarker({ srcProducer: 'p', srcRunId: 'r', cond }).includes(`cond=${cond}`));
  }
});

test('buildMarker: a cond outside the allowlist is REJECTED', () => {
  for (const cond of ['someone_looks_at_it', 'gh_pr_closed:civitai/x#1', '', 'MANUAL']) {
    assert.throws(
      () => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond }),
      MarkerError,
      `should reject cond ${JSON.stringify(cond)}`,
    );
  }
});

test('buildMarker: a cond kind that needs an argument is rejected bare', () => {
  for (const kind of COND_KINDS.filter((k) => !COND_KINDS_NO_ARG.includes(k))) {
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: kind }), MarkerError);
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: `${kind}:` }), MarkerError);
  }
});

// ── `manual` must NAME a checker ────────────────────────────────────────────
// The rule: a task may be filed only when its closing condition can be named,
// together with who or what checks it (definition: question 1 of
// ~/.claude/skills/clawgate/flows/task-authoring.md). A bare `manual` names
// nobody, so it recorded a condition that satisfied neither half of that.

test('validateCond: `manual:<who>` is the valid form and is returned verbatim', () => {
  assert.equal(validateCond('manual:zach'), 'manual:zach');
  assert.equal(validateCond('manual:koen'), 'manual:koen');
  assert.equal(validateCond('manual:support-rota'), 'manual:support-rota');
});

test('validateCond: a BARE `manual` is rejected, and the message says what to write instead', () => {
  let err;
  assert.throws(
    () => validateCond('manual'),
    (e) => { err = e; return e instanceof MarkerError; },
    'a bare `manual` must not validate — it names nobody',
  );
  // 🔴 Pin the REMEDY, not just the rejection. A rejection with no instruction
  // is how a caller ends up deleting --cond altogether.
  assert.match(err.message, /manual:<who>/,
    `the rejection must tell the caller to write \`manual:<who>\`; got: ${err.message}`);
  assert.match(err.message, /names nobody/,
    `the rejection must say WHY; got: ${err.message}`);
  // `manual:` with an empty argument is the same defect wearing a colon — and it
  // must fail for the SAME reason, not merely fail.
  //
  // 🔴 PINNING ONLY `MarkerError` HERE WAS A SURVIVING MUTANT (M18). Every
  // rejection path in validateCond throws MarkerError, so a mutant that lets
  // `manual:` fall through to the ARGUMENT charset check still throws — and the
  // caller is then told their argument "contains whitespace or `>`" about an
  // argument they never wrote. Same colour, wrong instruction. Assert the message.
  let colonErr;
  assert.throws(
    () => validateCond('manual:'),
    (e) => { colonErr = e; return e instanceof MarkerError; },
  );
  assert.match(colonErr.message, /must NAME who checks it/,
    `\`manual:\` must be refused as an unnamed checker, not as a bad argument; got: ${colonErr.message}`);
  assert.match(colonErr.message, /manual:<who>/,
    `and it must still name the remedy; got: ${colonErr.message}`);
  assert.doesNotMatch(colonErr.message, /whitespace/,
    `\`manual:\` must NOT be re-routed to the argument-charset error; got: ${colonErr.message}`);
  // The same value must be unbuildable, and for the same reason.
  assert.throws(
    () => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: 'manual:' }),
    (e) => e instanceof MarkerError && /must NAME who checks it/.test(e.message),
  );
});

test('buildMarker: refuses to EMIT a bare `manual`, and round-trips `manual:<who>`', () => {
  assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: 'manual' }), MarkerError);
  const marker = buildMarker({ srcProducer: 'capacity-sweep', srcRunId: 'run-1', cond: 'manual:zach' });
  assert.equal(marker, '<!-- claw:obj v=1 src=capacity-sweep/run-1 cond=manual:zach -->');
  const got = parseMarker(`body text\n${marker}\ntrailing`);
  assert.ok(got, 'a `manual:<who>` marker must parse');
  assert.equal(got.cond, 'manual:zach');
  assert.equal(got.condKind, 'manual');
  assert.equal(got.condArg, 'zach', 'the CHECKER is the whole point — it must survive the round-trip');
});

test('validateCond: the other allowlisted kinds are unchanged', () => {
  assert.equal(validateCond('gh_pr_merged:civitai/devrc#796'), 'gh_pr_merged:civitai/devrc#796');
  assert.equal(validateCond('alert_cleared:KubeNodeNotReady'), 'alert_cleared:KubeNodeNotReady');
  assert.equal(validateCond('cmd_exit_zero:drift-check'), 'cmd_exit_zero:drift-check');
  assert.equal(validateCond('metric_below:capacity:node-disk-free'), 'metric_below:capacity:node-disk-free');
  for (const bare of ['gh_pr_merged', 'alert_cleared', 'cmd_exit_zero', 'metric_below']) {
    assert.throws(() => validateCond(bare), MarkerError, `bare ${bare} must still be rejected`);
  }
});

// ── `unstated` is produced, never accepted ──────────────────────────────────

test('validateCond: `unstated` is REJECTED by default, and accepted only under the named opt-in', () => {
  let err;
  assert.throws(
    () => validateCond(COND_UNSTATED),
    (e) => { err = e; return e instanceof MarkerError; },
    '`unstated` must not validate by default — it is an observation, not a claim',
  );
  assert.match(err.message, /never accepted as input/,
    `the rejection must say it is fallback-only; got: ${err.message}`);
  // The opt-in exists and is narrow: the value, not the value plus an argument.
  assert.equal(validateCond(COND_UNSTATED, { allowUnstated: true }), COND_UNSTATED);
  assert.throws(() => validateCond('unstated:whatever'), MarkerError);
  assert.throws(() => validateCond('unstated:whatever', { allowUnstated: true }), MarkerError);
  // …and it is NOT in the allowlist a caller is shown.
  assert.equal(COND_KINDS.includes(COND_UNSTATED), false,
    '`unstated` must stay out of COND_KINDS — that list is the menu offered to callers');
  assert.deepEqual([...COND_KINDS_NO_ARG], [COND_UNSTATED],
    'the fallback kind is the ONLY kind that is complete without an argument');
});

test('buildMarker: `unstated` is emittable ONLY under the explicit fallback opt-in', () => {
  // 🔴 THE HOLE THIS CLOSES. buildMarker used to pass `allowUnstated: true`
  // unconditionally, so `unstated` was emittable by anyone who could reach it —
  // and via applyAgentStamp that was every create path. The default is now false;
  // exactly one call site (the missing-cond fallback) opts in.
  const args = { srcProducer: 'claude-code', srcRunId: 'r', cond: COND_UNSTATED };
  assert.throws(() => buildMarker(args), MarkerError,
    'buildMarker must NOT emit `unstated` for a caller that did not declare the fallback');
  const marker = buildMarker({ ...args, allowUnstated: true });
  assert.equal(marker, '<!-- claw:obj v=1 src=claude-code/r cond=unstated -->');
  const got = parseMarker(marker);
  assert.ok(got, 'an `unstated` marker must be readable — that is what makes the gap COUNTABLE');
  assert.equal(got.cond, COND_UNSTATED);
  assert.equal(got.condArg, null);
});

test('buildMarker: a value that would break the HTML comment is rejected', () => {
  for (const cond of ['metric_below:a b', 'metric_below:a>b']) {
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond }), MarkerError);
  }
  for (const bad of ['Capacity Sweep', 'cap sweep', '-lead', '', 'A']) {
    assert.throws(() => buildMarker({ srcProducer: bad, srcRunId: 'r', cond: 'manual:zach' }), MarkerError);
  }
  for (const bad of ['run 1', 'run>1', '']) {
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: bad, cond: 'manual:zach' }), MarkerError);
  }
});

test('buildMarker: invalid optional fields are rejected', () => {
  const base = { srcProducer: 'p', srcRunId: 'r', cond: 'manual:zach' };
  assert.throws(() => buildMarker({ ...base, fp: 'deadbeef' }), MarkerError);      // too short
  assert.throws(() => buildMarker({ ...base, fp: '0123456789AB' }), MarkerError);  // uppercase
  assert.throws(() => buildMarker({ ...base, init: 'Not A Slug' }), MarkerError);
});

test('parseMarker: full round-trip', () => {
  const got = parseMarker(`body\n${VECTORS.full.marker}\nmore`);
  assert.equal(got.v, MARKER_VERSION);
  assert.equal(got.fp, '0123456789ab');
  assert.equal(got.producer, 'capacity-sweep');
  assert.equal(got.runId, 'run-1');
  assert.equal(got.condKind, 'gh_pr_merged');
  assert.equal(got.condArg, 'civitai/talos-infra#1234');
  assert.equal(got.init, 'agent-hygiene');
  assert.equal(got.label, 'agent/capacity-sweep');
});

test('parseMarker: round-trip with the optional fields ABSENT', () => {
  const got = parseMarker(VECTORS.minimal.marker);
  assert.equal(got.fp, null);
  assert.equal(got.init, null);
  assert.equal(got.cond, 'cmd_exit_zero:drift-check');
  assert.equal(got.condArg, 'drift-check');
});

test('parseMarker: an absent marker is null, never a throw', () => {
  for (const text of ['', 'no marker here', null, undefined, 42, '<!-- fp:capacity:x -->']) {
    assert.equal(parseMarker(text), null);
  }
});

test('parseMarker: a MALFORMED marker reads as absent', () => {
  // Each is exactly one field away from valid. A half-written marker must not
  // be actionable.
  const bad = [
    '<!-- claw:obj v=1 cond=manual -->',                 // no src
    '<!-- claw:obj v=1 src=p/r -->',                     // no cond
    '<!-- claw:obj src=p/r cond=manual -->',             // no v
    '<!-- claw:obj v=2 src=p/r cond=manual -->',         // wrong version
    '<!-- claw:obj v=1 src=p cond=manual -->',           // src has no run-id
    '<!-- claw:obj v=1 src=p/r cond=whenever -->',       // cond off-allowlist
    '<!-- claw:obj v=1 src=p/r cond=metric_below -->',   // arg-less kind
    // 🔴 A colon with no checker is MALFORMED, not legacy. The read-side
    // `allowBareManual` opt-in covers the bare word only — objects stamped
    // before the arity rule carry `cond=manual`, never `cond=manual:`. Widening
    // the opt-in to "kind is manual" would silently make this parse. (The
    // Python half's docstring says the same, in the same words.)
    '<!-- claw:obj v=1 src=p/r cond=manual: -->',        // colon, no checker
    '<!-- claw:obj v=1 src=p/r cond=manual fp=xyz -->',  // fp not hex
    '<!-- claw:obj v=1 src=p/r cond=manual junk=1 -->',  // unknown field
    '<!-- claw:obj v=1 v=1 src=p/r cond=manual -->',     // repeated field
    '<!-- claw:obj v=1 src=p/r cond=manual bare -->',    // non key=value token
    '<!-- clawobj v=1 src=p/r cond=manual -->',          // wrong sentinel
  ];
  for (const b of bad) assert.equal(parseMarker(b), null, `should not parse: ${b}`);
});

test('parseMarker: a malformed marker does not shadow a later good one', () => {
  const text = `<!-- claw:obj v=1 cond=manual -->\n${VECTORS.minimal.marker}`;
  assert.equal(parseMarker(text).producer, 'capacity-sweep');
});

test('labelFor: reproduces capacity-sweep\'s existing live label exactly', () => {
  assert.equal(labelFor('capacity-sweep'), 'agent/capacity-sweep');
  assert.equal(AGENT_LABEL_PREFIX, 'agent/');
  for (const bad of ['Capacity Sweep', '', '-x', 'A']) {
    assert.throws(() => labelFor(bad), MarkerError);
  }
});

// ── Tier 2: cross-language ──────────────────────────────────────────────────

test('CROSS-LANGUAGE: the cond allowlist matches the Python implementation', () => {
  assert.deepEqual([...COND_KINDS].sort(), VECTORS.condKinds);
});

test('CROSS-LANGUAGE: markers are byte-identical to the Python implementation', () => {
  assert.equal(buildMarker(VECTORS.minimal.args), VECTORS.minimal.marker);
  assert.equal(buildMarker(VECTORS.full.args), VECTORS.full.marker);
});

const PY = `${PYTHON_SIDE.repo} ${PYTHON_SIDE.path} @ ${PYTHON_SIDE.ref}`
  + ` (branch ${PYTHON_SIDE.branch}, measured ${PYTHON_SIDE.measuredOn})`;

test('CROSS-LANGUAGE: the ONE divergence is the missing-cond FALLBACK, and JS has it', () => {
  // The half that lives in THIS repo is asserted for real, not recorded.
  assert.equal(PYTHON_DIVERGENCE.jsProducesUnstatedFromItsFallback, true);
  const [id, warned] = captureStderr(() => agentIdentity({}));
  assert.equal(id.cond, COND_UNSTATED,
    `JS's fallback must PRODUCE ${COND_UNSTATED} — that is the divergence from ${PY}`);
  assert.equal(warned, '', 'an ABSENT CLAW_AGENT_COND is not a degradation and must not warn');
  const [{ body }] = captureStderr(() => applyAgentStamp({}));
  assert.ok(body.markdown_description.includes(`cond=${COND_UNSTATED}`),
    `and it must reach a stamped marker — every cond=${COND_UNSTATED} object in the corpus came from `
    + `this side, because ${PY} takes cond as a REQUIRED POSITIONAL and validates it with no opt-ins`);
  // …and the recorded Python half says it cannot do the same.
  assert.equal(PYTHON_DIVERGENCE.pythonCanEmitUnstated, false, `recorded from ${PY}`);
});

test('CROSS-LANGUAGE: bare `manual` — both sides refuse to EMIT it and both still PARSE it', () => {
  assert.equal(PYTHON_DIVERGENCE.bothRefuseToEmitBareManual, true);
  assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: 'manual' }), MarkerError,
    `this side must not emit a bare \`manual\`; neither does ${PY}`);

  assert.equal(PYTHON_DIVERGENCE.bothStillParseBareManual, true);
  const legacy = parseMarker(VECTORS.legacyBareManual);
  assert.ok(legacy,
    'a legacy bare `manual` MUST still parse — rejecting it on the READ path would blind '
    + 'a JS reconciler to every already-stamped object carrying one, which is the invisibility this '
    + 'marker exists to end');
  assert.equal(legacy.condKind, 'manual');
  assert.equal(legacy.condArg, null, 'condArg === null is how a consumer counts the ones naming nobody');

  assert.equal(PYTHON_DIVERGENCE.bothRejectUnstatedAsInput, true);
  assert.throws(() => validateCond(COND_UNSTATED), MarkerError, `as does ${PY}`);

  // The kind ALLOWLIST is unchanged, which is why the VECTORS pin above holds.
  assert.deepEqual([...COND_KINDS].sort(), VECTORS.condKinds);
});

test('CROSS-LANGUAGE: the Python provenance is pinned to an IMMUTABLE ref and cited in the docs', () => {
  // 🔴 A recorded cross-language claim decays. Nothing hermetic can read Python,
  // so these are the two things that CAN be checked from here:
  //   (a) the ref is a full commit sha, not a moving branch name — a claim pinned
  //       to `origin/trunk` cannot be re-checked and reads as current forever;
  //   (b) the SAME provenance appears in reference/maintaining.md, so the prose
  //       record and the test record cannot drift apart silently.
  assert.match(PYTHON_SIDE.ref, /^[0-9a-f]{40}$/, 'pin an immutable commit, never a branch ref');
  assert.match(PYTHON_SIDE.sha256, /^[0-9a-f]{64}$/);
  assert.match(PYTHON_SIDE.measuredOn, /^\d{4}-\d{2}-\d{2}$/);
  const doc = normalise(MAINTAINING_SRC);
  assert.ok(doc.includes(PYTHON_SIDE.ref.slice(0, 9)),
    `reference/maintaining.md must cite the same commit (${PYTHON_SIDE.ref.slice(0, 9)}) this suite pins`);
  assert.ok(doc.includes(PYTHON_SIDE.measuredOn),
    `reference/maintaining.md must cite the same measurement date (${PYTHON_SIDE.measuredOn})`);
  assert.ok(doc.includes(PYTHON_SIDE.path),
    `reference/maintaining.md must name the same Python file (${PYTHON_SIDE.path})`);
});

test('CROSS-LANGUAGE: ARMED-ONLY — the recorded Python file still hashes to what was measured', () => {
  // 🔴 UNARMED THIS TEST PROVES NOTHING, and saying so is the point: without the
  // env var it asserts only that the guard is wired, so do not read a green run
  // as evidence the Python side is unchanged. Arm it in a checkout:
  //   AGENT_OBJ_MARKER_PY=<talos-infra>/scripts/lib/agent_obj_marker.py \
  //     node --test test/agent-marker.test.mjs
  // It is opt-in because a hermetic suite must not depend on another repo being
  // present — that is a flaky gate, not a gate.
  const p = process.env[PYTHON_SIDE.armEnv];
  if (!p) {
    assert.equal(typeof PYTHON_SIDE.sha256, 'string', 'unarmed: nothing was measured');
    return;
  }
  assert.ok(existsSync(p), `${PYTHON_SIDE.armEnv}=${p} does not exist`);
  const got = createHash('sha256').update(readFileSync(p)).digest('hex');
  assert.equal(got, PYTHON_SIDE.sha256,
    `\n${PYTHON_SIDE.path} has CHANGED since ${PY}.\n`
    + '  Re-measure the divergence block above against the new content and update\n'
    + '  PYTHON_SIDE (ref, sha256, measuredOn) and reference/maintaining.md together.');
});

test('CROSS-LANGUAGE: fingerprints match Python json.dumps(sort_keys=True)', () => {
  // The nested case is the one that matters: JSON.stringify preserves insertion
  // order, so a SHALLOW key sort silently fingerprints two identical claims
  // apart whenever a nested object's keys were built in a different order.
  for (const [claim, expected] of VECTORS.fingerprints) {
    assert.equal(fingerprint(claim), expected, `fingerprint drift for ${JSON.stringify(claim)}`);
  }
});

// ── agentIdentity ───────────────────────────────────────────────────────────

test('agentIdentity: defaults are usable with an empty environment', () => {
  const id = agentIdentity({});
  assert.equal(id.producer, DEFAULT_PRODUCER);
  assert.equal(id.runId, 'unknown');
  // 🔴 NOT `manual`. A missing condition is recorded as MISSING.
  assert.equal(id.cond, COND_UNSTATED,
    'a caller who named no condition must not be stamped as though they had');
  assert.equal(id.init, null);
  assert.equal(id.label, `agent/${DEFAULT_PRODUCER}`);
  // The whole point: whatever the environment holds, the result must BUILD —
  // through the fallback opt-in, which is the ONLY path allowed to emit it.
  assert.ok(parseMarker(buildMarker({
    srcProducer: id.producer, srcRunId: id.runId, cond: id.cond, allowUnstated: true,
  })));
});

test('agentIdentity: a hostile environment still yields a buildable identity, and WARNS on the degradation', () => {
  const [id, err] = captureStderr(() => agentIdentity({
    CLAW_AGENT_PRODUCER: 'Capacity Sweep!!',
    CLAW_AGENT_RUN_ID: 'run id/with spaces>and brackets',
    CLAW_AGENT_COND: 'whatever_i_feel_like',
    CLAW_AGENT_INIT: 'Some Slug',
  }));
  assert.equal(id.producer, 'capacity-sweep--');
  assert.equal(id.runId, 'run-id-with-spaces-and-brackets');
  // 🔴 Falls back to `unstated`, NOT to silence and NOT to `manual`. An honest
  // marker of ABSENCE beats a dishonest marker of presence; dropping the marker
  // would make the object invisible again, which is the defect Phase 0 addresses.
  assert.equal(id.cond, COND_UNSTATED);
  assert.equal(id.init, 'some-slug');
  // 🔴 …AND THE DEGRADATION IS ANNOUNCED WHERE IT HAPPENS. An operator who
  // exported a value believes they named a condition; the create-site warning
  // says "no closing condition was named", which reads as FALSE to them. This
  // one quotes the value and the reason it was refused.
  assert.match(err, /CLAW_AGENT_COND="whatever_i_feel_like" was REFUSED/,
    `the degradation must quote the value it threw away; got: ${JSON.stringify(err)}`);
  assert.match(err, /not in the allowlist/,
    `the degradation must give the REASON, from validateCond; got: ${JSON.stringify(err)}`);
  assert.match(err, new RegExp(`Recording cond=${COND_UNSTATED} instead`),
    `the degradation must name what it recorded instead; got: ${JSON.stringify(err)}`);
  const marker = buildMarker({
    srcProducer: id.producer, srcRunId: id.runId, cond: id.cond, init: id.init, allowUnstated: true,
  });
  assert.ok(parseMarker(marker), 'a sanitised identity must produce a PARSEABLE marker');
});

test('agentIdentity: CLAW_AGENT_COND=manual is a DEGRADATION and says so', () => {
  // The specific case the audit named: `manual` is no longer emittable, so this
  // environment silently became `unstated`. Silently is the defect.
  const [id, err] = captureStderr(() => agentIdentity({ CLAW_AGENT_COND: 'manual' }));
  assert.equal(id.cond, COND_UNSTATED);
  assert.match(err, /CLAW_AGENT_COND="manual" was REFUSED/, `got: ${JSON.stringify(err)}`);
  assert.match(err, /must NAME who checks it/,
    "the reason must be manual's own, not a generic one; got: " + JSON.stringify(err));
  assert.match(err, /CLAW_AGENT_COND=manual:zach/,
    `the warning must name the remedy; got: ${JSON.stringify(err)}`);
});

test('agentIdentity: an allowlisted CLAW_AGENT_COND is honoured verbatim, and silently', () => {
  const [id, err] = captureStderr(
    () => agentIdentity({ CLAW_AGENT_COND: 'gh_pr_merged:civitai/talos-infra#1277' }),
  );
  assert.equal(id.cond, 'gh_pr_merged:civitai/talos-infra#1277');
  assert.equal(err, '', `a valid value must not warn; got: ${JSON.stringify(err)}`);
});

test('stampDescription: idempotent — an existing marker is never doubled', () => {
  const once = stampDescription('hello', VECTORS.minimal.marker);
  assert.ok(once.includes(VECTORS.minimal.marker));
  const twice = stampDescription(once, VECTORS.full.marker);
  assert.equal(twice, once, 'a body that already carries a marker must be untouched');
  assert.equal(stampDescription('', VECTORS.minimal.marker), VECTORS.minimal.marker);
  assert.equal(stampDescription(undefined, VECTORS.minimal.marker), VECTORS.minimal.marker);
});

// ── Tier 3: the producer seam (behavioural) ─────────────────────────────────

/** Run `fn` with process.stderr.write captured; returns [result, stderrText]. */
function captureStderr(fn) {
  const real = process.stderr.write;
  let out = '';
  process.stderr.write = (chunk) => { out += String(chunk); return true; };
  try {
    return [fn(), out];
  } finally {
    process.stderr.write = real;
  }
}

test('SEAM: a plain create is stamped with a parseable marker and a tag', () => {
  const [{ body, tag }] = captureStderr(() => applyAgentStamp({}));
  const got = parseMarker(body.markdown_description);
  assert.ok(got, `no marker in ${JSON.stringify(body)}`);
  assert.equal(got.cond, COND_UNSTATED);
  assert.equal(got.fp, null, 'a session-filed one-off has no stable claim — fp must be OMITTED');
  assert.equal(tag, got.label);
  assert.equal(tag, `agent/${got.producer}`, 'the tag and src= must name the SAME producer');
});

test('SEAM: a create with NO condition warns LOUDLY on stderr and records cond=unstated', () => {
  const [{ body }, err] = captureStderr(() => applyAgentStamp({}));
  // The stamp itself must be honest…
  assert.ok(body.markdown_description.includes('cond=unstated'),
    `the marker must carry the greppable absence; got: ${body.markdown_description}`);
  // …and the gap must be visible AT CREATE TIME, not only to a later grep.
  assert.match(err, /cond=unstated/, `stderr must name the value it recorded; got: ${JSON.stringify(err)}`);
  assert.match(err, /no closing condition was named/,
    `stderr must say what is missing; got: ${JSON.stringify(err)}`);
  assert.match(err, /manual:<who>/,
    `stderr must name the remedy, including the human form; got: ${JSON.stringify(err)}`);
});

test('SEAM: a create WITH a condition is silent — the warning is not noise on every create', () => {
  const [{ body }, err] = captureStderr(
    () => applyAgentStamp({ agentCond: 'gh_pr_merged:civitai/devrc#796' }),
  );
  assert.ok(body.markdown_description.includes('cond=gh_pr_merged:civitai/devrc#796'));
  assert.equal(err, '',
    `a compliant create must not warn, or the warning stops being read; got: ${JSON.stringify(err)}`);
  const [, errManual] = captureStderr(() => applyAgentStamp({ agentCond: 'manual:zach' }));
  assert.equal(errManual, '', '`manual:<who>` names a checker and is therefore compliant');
});

test('SEAM: a CALLER may not pass `unstated` — the create path REJECTS it', async () => {
  // 🔴 THIS IS THE TEST THAT WAS MISSING, and its absence is why the claim was
  // false for six months of call sites. The old test with this name only ever
  // called validateCond() with default options — it never touched the create
  // seam it was named after, while applyAgentStamp / createTask / createSubtask /
  // batch-create all accepted `agentCond: 'unstated'` and stamped it.
  //
  // `unstated` means the CODE OBSERVED AN ABSENCE. A caller who can assert it has
  // converted an observation back into a claim, and `cond=unstated` stops
  // counting authorial omission.
  let err;
  assert.throws(
    () => applyAgentStamp({ agentCond: COND_UNSTATED }),
    (e) => { err = e; return e instanceof MarkerError; },
    'applyAgentStamp must refuse a caller-supplied `unstated`',
  );
  assert.match(err.message, /never accepted as input/,
    `the refusal must say it is fallback-only; got: ${err.message}`);
  // 🔴 ATTRIBUTED TO THE SEAM, and that is not cosmetic. buildMarker validates
  // too, so a mutant deleting the seam's own guard dies to buildMarker's instead
  // — green for the wrong reason, and still green with this guard removed.
  // Asserting WHICH guard refused is what makes this test about the seam.
  // (Measured: without this assertion the seam-deletion mutant SURVIVED.)
  assert.match(err.message, /agentCond rejected at the create seam/,
    `the refusal must come from the seam's own guard; got: ${err.message}`);

  // createTask is the real producer, and it validates BEFORE any network call —
  // so this rejects hermetically, with no transport and no credentials.
  await assert.rejects(
    () => createTask('900000000001', 'a task', { agentCond: COND_UNSTATED }),
    (e) => e instanceof MarkerError
      && /agentCond rejected at the create seam/.test(e.message)
      && /never accepted as input/.test(e.message),
    'createTask must refuse it too — the CLI is not the only caller',
  );

  // The same seam still refuses everything else off-allowlist, including the
  // bare `manual` this side will not emit.
  for (const bad of ['manual', 'manual:', 'someone_looks_at_it', 'unstated:x']) {
    assert.throws(() => applyAgentStamp({ agentCond: bad }), MarkerError, `should reject ${bad}`);
  }
  // …and a legitimate condition still goes through.
  const [{ body }] = captureStderr(() => applyAgentStamp({ agentCond: 'manual:zach' }));
  assert.equal(parseMarker(body.markdown_description).cond, 'manual:zach');
});

test('SEAM: createSubtask can NAME a condition — a subtask is a stamped object too', () => {
  // 🔴 WHAT THIS PROTECTS IS THE MEANING OF THE COUNT. Until this landed,
  // `create-subtask` and batch-create structurally could not supply a cond, so
  // every subtask and every batch task was `unstated` BY CONSTRUCTION — and a
  // 20-task plan added 20. The `unstated` count is supposed to measure authorial
  // omission; an interface gap would have dominated it.
  const [{ body }, err] = captureStderr(
    () => applyAgentStamp({ parent: 'abc123', agentCond: 'cmd_exit_zero:drift-check' }),
  );
  assert.equal(parseMarker(body.markdown_description).cond, 'cmd_exit_zero:drift-check');
  assert.equal(err, '', 'a subtask that named its condition must not warn');
  // The CLI wires it: `subtask` routes through the same one validator as `create`.
  assert.ok(QUERY_SRC.includes('createSubtask(taskId, arg2, withAgentCond({}))'),
    'the subtask command must thread --cond through, or every CLI subtask is `unstated` by construction');
  // 🔴 Match the CALL SHAPES, not the identifier: a bare `withAgentCond\(` count
  // also matches the declaration AND the prose that points at it, so it reads 4
  // on a perfectly correct tree. (The same trap is annotated twice in the CHOKE
  // POINT test below; it is the third time in this file.)
  assert.equal((QUERY_SRC.match(/function withAgentCond\(options\) \{/g) || []).length, 1,
    'exactly one validator');
  assert.equal((QUERY_SRC.match(/\bwithAgentCond\(options\);/g) || []).length, 1,
    'the `create` command must route through it');
  assert.equal((QUERY_SRC.match(/\bwithAgentCond\(\{\}\)/g) || []).length, 1,
    'the `subtask` command must route through it');
});

test('SEAM: a batch plan\'s conds are validated BEFORE anything is created', () => {
  // Positive control first: a plan whose conds are all sayable reports nothing.
  assert.deepEqual(validatePlanConds({
    tasks: [
      { ref: 'a', name: 'A', cond: 'manual:zach', subtasks: [{ name: 's', cond: 'gh_pr_merged:civitai/devrc#803' }] },
      { name: 'B' }, // omission is legal — it records `unstated`
    ],
  }), []);
  // A caller-asserted `unstated` and an off-allowlist value are both refused, and
  // the report NAMES which task, because a fan-out failure that says only "bad
  // cond" is unactionable across 20 tasks.
  const problems = validatePlanConds({
    tasks: [
      { ref: 'a', name: 'A', cond: COND_UNSTATED },
      { name: 'B', cond: 'manual', subtasks: [{ name: 'sub', cond: 'whenever' }] },
    ],
  });
  assert.equal(problems.length, 3, `expected one problem per bad cond; got ${JSON.stringify(problems)}`);
  assert.match(problems[0], /^a: /);
  assert.match(problems[0], /never accepted as input/);
  assert.match(problems[1], /^B: /);
  assert.match(problems[1], /must NAME who checks it/);
  assert.match(problems[2], /^B > sub: /);
  assert.match(problems[2], /not in the allowlist/);
});

test('SEAM: an existing markdown description keeps its content and gains the marker', () => {
  const { body } = applyAgentStamp({ markdown_description: '# Title\n\nSome body.' });
  assert.ok(body.markdown_description.startsWith('# Title\n\nSome body.'));
  assert.ok(parseMarker(body.markdown_description));
});

test('SEAM: a caller using the plain `description` field is stamped there', () => {
  const { body } = applyAgentStamp({ description: 'plain text' });
  assert.equal(body.markdown_description, undefined);
  assert.ok(parseMarker(body.description));
});

test('SEAM: agentCond and agentFp reach the marker and are NOT sent to ClickUp', () => {
  const fp = fingerprint({ producer: 'x', claim: 'y' });
  const { body } = applyAgentStamp({
    agentCond: 'gh_pr_merged:civitai/talos-infra#1277',
    agentFp: fp,
  });
  const got = parseMarker(body.markdown_description);
  assert.equal(got.condKind, 'gh_pr_merged');
  assert.equal(got.condArg, 'civitai/talos-infra#1277');
  assert.equal(got.fp, fp);
  // 🔴 These are OUR options, not ClickUp's. Leaking them into the POST body
  // would send unknown fields to the API on every single create.
  for (const k of ['agentCond', 'agentFp', 'agentStamp']) {
    assert.equal(k in body, false, `${k} must not be forwarded to ClickUp`);
  }
});

test('SEAM: opt-out returns the caller\'s options untouched and no tag', () => {
  const { body, tag } = applyAgentStamp({ agentStamp: false, markdown_description: 'raw' });
  assert.equal(body.markdown_description, 'raw');
  assert.equal(tag, null);
  assert.equal('agentStamp' in body, false);
});

test('SEAM: CLICKUP_AGENT_STAMP=0 disables the stamp', () => {
  const prev = process.env.CLICKUP_AGENT_STAMP;
  process.env.CLICKUP_AGENT_STAMP = '0';
  try {
    const { body, tag } = applyAgentStamp({ markdown_description: 'raw' });
    assert.equal(body.markdown_description, 'raw');
    assert.equal(tag, null);
  } finally {
    if (prev === undefined) delete process.env.CLICKUP_AGENT_STAMP;
    else process.env.CLICKUP_AGENT_STAMP = prev;
  }
});

// ── Tier 4: the choke point ─────────────────────────────────────────────────

test('CHOKE POINT: every ClickUp task-create call site stamps', () => {
  // Both creators must route through applyAgentStamp + attachAgentTag. A new
  // creator that POSTs to /list/<id>/task without them would be unstamped by
  // default — which is how this defect class regenerates.
  // 🔴 Match the POST, not the ENDPOINT. `/list/<id>/task` is also the GET that
  // getTasksInList uses, so an endpoint-only count reads 3 and fails on a
  // perfectly correct tree — which is exactly what this assertion did on its
  // first run. A read is not a create.
  const endpoints = TASKS_SRC.match(/`\/list\/\$\{listId\}\/task`/g) || [];
  assert.ok(endpoints.length >= 2, 'positive control: the endpoint literal is findable at all');
  const posts = TASKS_SRC.match(/`\/list\/\$\{listId\}\/task`,\s*\{\s*method: 'POST'/g) || [];
  assert.equal(posts.length, 2, 'expected exactly 2 task-create POST sites (createTask, createSubtask)');
  // 🔴 `applyAgentStamp(options)` alone matches its own DECLARATION too, so an
  // unqualified count reads 3. Match the CALL shape — the destructure — not the
  // identifier. (Same defect one line up, same session, twice.)
  assert.equal(
    (TASKS_SRC.match(/const \{ body: stamped, tag \} = applyAgentStamp\(options\);/g) || []).length, 2,
    'both creators must route through the single stamping helper');
  assert.equal(
    (TASKS_SRC.match(/await attachAgentTag\(response\?\.id, tag\);/g) || []).length, 2,
    'both creators must attach the agent tag');
});

test('CHOKE POINT: --cond is validated at the CLI, not silently downgraded', () => {
  assert.ok(QUERY_SRC.includes("arg === '--cond'"), '--cond flag is not parsed');
  assert.ok(QUERY_SRC.includes('options.agentCond = validateCond(condArg)'),
    '--cond must be validated against the allowlist before it is stored');
  assert.ok(QUERY_SRC.includes('--cond'), '--cond must appear in showUsage()');
});

test('CHOKE POINT: a refused --cond prints the allowlist AND the arity rule', () => {
  // 🔴 M23 SURVIVED BECAUSE THIS LINE WAS UNTESTED. The rejection is only useful
  // if it says what to write instead — a bare "not in the allowlist" leaves the
  // caller's most likely next move (drop --cond) unaddressed, and the `manual`
  // arity rule is exactly the one they are most likely to have broken.
  assert.ok(QUERY_SRC.includes('`Allowed --cond kinds: ${[...COND_KINDS].sort().join(\', \')}`'),
    'the refusal must enumerate the allowlist from COND_KINDS, not a restated literal list');
  assert.ok(
    QUERY_SRC.includes(
      "'Every kind takes an argument; manual takes the NAME of who checks it.'"),
    'the refusal must state the arity rule verbatim — that is the half a caller gets wrong');
  // One writer, so both creating commands print the same thing.
  assert.equal((QUERY_SRC.match(/Every kind takes an argument/g) || []).length, 1,
    'the hint must exist in exactly one place (withAgentCond), or the two creators can drift apart');
});

test('CHOKE POINT: batch-create validates the WHOLE plan before creating anything', () => {
  const src = readFileSync(resolve(__dirname, '..', 'lib', 'batch-create.mjs'), 'utf8');
  assert.ok(src.includes('const condProblems = validatePlanConds(plan);'),
    'the plan-wide validation must run inside executeBatchCreate');
  // Order is the whole point: validating lazily leaves 16 objects on the board
  // when task 17 throws, and there is no rollback.
  assert.ok(src.indexOf('validatePlanConds(plan)') < src.indexOf('await createTask('),
    'the plan-wide cond check must precede the first create');
  // 🔴 PIN THE WHOLE STATEMENT, GUARD INCLUDED. `src.includes('createOpts.agentCond
  // = t.cond')` still matched after a mutant rewrote the guard to `if (false)` —
  // measured, it SURVIVED. `executeBatchCreate` takes no injectable transport, so
  // its wiring is covered structurally rather than behaviourally; a structural
  // check that omits the condition is a check of the wrong half.
  assert.ok(src.includes('      if (t.cond) createOpts.agentCond = t.cond;\n'),
    'a batch task must be able to name its condition');
  assert.ok(src.includes('              agentCond: sub.cond || undefined,\n'),
    'so must a batch subtask');
});

// ── Tier 5: the prose the flow rests on ─────────────────────────────────────
//
// 🔴 THE ARTIFACT UNDER TEST IS PROSE, so this pins WHOLE NORMALISED SENTENCES,
// not keywords — `claude/RULES.md`: a guard on words is walkable by rewording.
// The pattern (and the reasoning behind pinning whole strings) is
// `scripts/tests/test_closing_condition_single_source.py`. A cosmetic reword
// SHOULD fail here; update the constant in the same commit, which is the moment
// somebody should notice they are editing a load-bearing disclaimer.

// The most load-bearing sentence in flows/task-hygiene.md. Flipping it to a
// FALSE gate claim ("a PreToolUse hook blocks…") was a fully green mutant (M24):
// nothing read this file. A doc that reads as a gate while providing none is
// worse than none — it stops anyone looking.
const HYGIENE_NO_ENFORCEMENT = normalise(`
🔴 **NOTHING ENFORCES ANY OF THIS.** clawgate's equivalents
(\`~/.claude/skills/clawgate/flows/task-authoring.md\`,
\`~/.claude/skills/clawgate/flows/task-pickup.md\`) work because two PreToolUse/Stop
hooks route to them and BLOCK. There is no ClickUp hook, no server check, and no
gate anywhere in this skill that reads this file. It is a convention you follow or
do not follow, and it is written down here so it can at least be *cited*.

That warning is first because a doc that reads as a gate while providing none is
worse than none — it stops anyone looking. Do not summarise this file elsewhere as
"the ClickUp task gate". It is not one.
`);

test('PROSE: the hygiene flow still says NOTHING ENFORCES IT, in exactly those words', () => {
  const doc = normalise(HYGIENE_SRC);
  assert.ok(doc.includes(HYGIENE_NO_ENFORCEMENT),
    '\n\nflows/task-hygiene.md\'s no-enforcement disclaimer no longer matches the pinned text.\n\n'
    + `  expected (normalised):\n    ${HYGIENE_NO_ENFORCEMENT}\n\n`
    + '  If you edited it deliberately, update HYGIENE_NO_ENFORCEMENT in the SAME commit —\n'
    + '  and check the edit did not turn a disclaimer into a claim of enforcement that does\n'
    + '  not exist. If a gate ever DOES land, this test is where you record that it did.');
  // It must be FIRST — a disclaimer buried under the advice it qualifies is one
  // most readers never reach.
  const bodyStart = HYGIENE_SRC.indexOf('\n', HYGIENE_SRC.indexOf('# flow:'));
  assert.ok(normalise(HYGIENE_SRC.slice(bodyStart, bodyStart + 400)).startsWith('🔴 **NOTHING ENFORCES ANY OF THIS.**'),
    'the disclaimer must remain the first thing in the body');
});

test('PROSE: the measured figure in the hygiene flow cites a source in this repo', () => {
  // 🔴 It used to quote "0 addressed / 1 partially addressed / 2 not established"
  // as measured, with nothing to cite: that run happened in a session, not in the
  // repo, and the in-repo record says something different. An uncitable number
  // presented as measurement is the shape a claim takes just before it is wrong.
  const doc = normalise(HYGIENE_SRC);
  const cited = 'claude/skills/check-clickup-addressed/reference/validation-history.md';
  assert.ok(doc.includes(cited), `the measured figure must cite ${cited}`);
  assert.ok(doc.includes('**0 addressed, 0 partial, 0 open, 2 unclear**'),
    'the figure quoted must be the one that record actually holds');
  const record = normalise(readFileSync(
    resolve(__dirname, '..', '..', 'check-clickup-addressed', 'reference', 'validation-history.md'), 'utf8'));
  assert.ok(record.includes('Summary: 0 addressed, 0 partial, 0 open, 2 unclear'),
    'and the cited record must still hold it — this is the half that goes stale');
});

test('CHOKE POINT: validateCond is importable by the CLI and rejects free text', () => {
  assert.equal(validateCond('metric_below:capacity:node-disk-free'), 'metric_below:capacity:node-disk-free');
  assert.throws(() => validateCond('when someone gets round to it'), MarkerError);
});
