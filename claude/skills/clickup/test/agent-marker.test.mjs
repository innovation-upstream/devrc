#!/usr/bin/env node

/**
 * Gates for the `claw:obj` agent-object marker on the ClickUp side
 * (Phase 0 of agent-object hygiene). Hermetic — no credentials, no network.
 *
 * Four tiers, and the last two are the ones that could actually be missing:
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
 *
 * Usage:
 *   node test/agent-marker.test.mjs
 *   node --test test/agent-marker.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

import {
  MARKER_VERSION,
  COND_KINDS,
  COND_KINDS_NO_ARG,
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
import { applyAgentStamp } from '../api/tasks.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TASKS_SRC = readFileSync(resolve(__dirname, '..', 'api', 'tasks.mjs'), 'utf8');
const QUERY_SRC = readFileSync(resolve(__dirname, '..', 'query.mjs'), 'utf8');

// ── Tier 2: the cross-language pin ──────────────────────────────────────────
// Every value here was produced by the PYTHON implementation
// (talos-infra scripts/lib/agent_obj_marker.py) and pasted in. Do not
// "regenerate" them from this file — that would make the pin self-referential
// and it would agree with any drift.
const VECTORS = Object.freeze({
  minimal: {
    args: { srcProducer: 'capacity-sweep', srcRunId: 'sweep-29159872-abcde', cond: 'manual' },
    marker: '<!-- claw:obj v=1 src=capacity-sweep/sweep-29159872-abcde cond=manual -->',
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

test('buildMarker: `manual` rejects an argument', () => {
  assert.throws(
    () => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond: 'manual:because-i-said-so' }),
    MarkerError,
  );
});

test('buildMarker: a value that would break the HTML comment is rejected', () => {
  for (const cond of ['metric_below:a b', 'metric_below:a>b']) {
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: 'r', cond }), MarkerError);
  }
  for (const bad of ['Capacity Sweep', 'cap sweep', '-lead', '', 'A']) {
    assert.throws(() => buildMarker({ srcProducer: bad, srcRunId: 'r', cond: 'manual' }), MarkerError);
  }
  for (const bad of ['run 1', 'run>1', '']) {
    assert.throws(() => buildMarker({ srcProducer: 'p', srcRunId: bad, cond: 'manual' }), MarkerError);
  }
});

test('buildMarker: invalid optional fields are rejected', () => {
  const base = { srcProducer: 'p', srcRunId: 'r', cond: 'manual' };
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
  assert.equal(got.condArg, null);
  assert.equal(got.cond, 'manual');
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
  assert.equal(id.cond, 'manual');
  assert.equal(id.init, null);
  assert.equal(id.label, `agent/${DEFAULT_PRODUCER}`);
  // The whole point: whatever the environment holds, the result must BUILD.
  assert.ok(parseMarker(buildMarker({ srcProducer: id.producer, srcRunId: id.runId, cond: id.cond })));
});

test('agentIdentity: a hostile environment still yields a buildable identity', () => {
  const id = agentIdentity({
    CLAW_AGENT_PRODUCER: 'Capacity Sweep!!',
    CLAW_AGENT_RUN_ID: 'run id/with spaces>and brackets',
    CLAW_AGENT_COND: 'whatever_i_feel_like',
    CLAW_AGENT_INIT: 'Some Slug',
  });
  assert.equal(id.producer, 'capacity-sweep--');
  assert.equal(id.runId, 'run-id-with-spaces-and-brackets');
  // 🔴 Falls back to `manual`, NOT to silence. `manual` is an honest "no machine
  // closes this"; dropping the marker would make the object invisible again,
  // which is the entire defect Phase 0 addresses.
  assert.equal(id.cond, 'manual');
  assert.equal(id.init, 'some-slug');
  const marker = buildMarker({ srcProducer: id.producer, srcRunId: id.runId, cond: id.cond, init: id.init });
  assert.ok(parseMarker(marker), 'a sanitised identity must produce a PARSEABLE marker');
});

test('agentIdentity: an allowlisted CLAW_AGENT_COND is honoured verbatim', () => {
  assert.equal(
    agentIdentity({ CLAW_AGENT_COND: 'gh_pr_merged:civitai/talos-infra#1277' }).cond,
    'gh_pr_merged:civitai/talos-infra#1277',
  );
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

test('SEAM: a plain create is stamped with a parseable marker and a tag', () => {
  const { body, tag } = applyAgentStamp({});
  const got = parseMarker(body.markdown_description);
  assert.ok(got, `no marker in ${JSON.stringify(body)}`);
  assert.equal(got.cond, 'manual');
  assert.equal(got.fp, null, 'a session-filed one-off has no stable claim — fp must be OMITTED');
  assert.equal(tag, got.label);
  assert.equal(tag, `agent/${got.producer}`, 'the tag and src= must name the SAME producer');
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

test('CHOKE POINT: validateCond is importable by the CLI and rejects free text', () => {
  assert.equal(validateCond('metric_below:capacity:node-disk-free'), 'metric_below:capacity:node-disk-free');
  assert.throws(() => validateCond('when someone gets round to it'), MarkerError);
});
