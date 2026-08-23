// Agent-object hygiene — the ONE definition of the `claw:obj` marker grammar
// for the JS side (ClickUp task creation).
//
// Phase 0: make every object an agent creates SELF-IDENTIFYING, so a later
// ledger + reconciler can find it and close it. Measured 2026-08-23 over a
// complete enumeration of 741 open GitHub objects and 64,137 ClickUp tasks:
// agent-created ISSUES leak (47.4% 30-day survival) while agent PRs do not
// (5.2%), and on ClickUp the API token resolves to a HUMAN identity, so an
// agent-filed task is currently indistinguishable from a hand-typed one. That
// last part is why this exists here rather than only on the GitHub side.
//
//   <!-- claw:obj v=1 fp=<12hex> src=<producer>/<run-id> cond=<condition-id> init=<slug> -->
//
//   v     REQUIRED  grammar version. Always 1 in Phase 0.
//   fp    OPTIONAL  12 lowercase hex — sha256 of a STABLE claim tuple. Emitted
//                   only by a deterministic reconciler that owns one. A session
//                   filing a one-off has NO stable claim: omit it rather than
//                   invent one, because a fingerprint that changes every run
//                   defeats the dedupe it exists to provide.
//   src   REQUIRED  `<producer>/<run-id>`.
//   cond  REQUIRED  the machine-checkable condition that CLOSES the object,
//                   from the enumerated allowlist COND_KINDS below.
//   init  OPTIONAL  initiatives slug.
//
// 🔴 THE ASYMMETRY IS DELIBERATE: buildMarker() THROWS on anything invalid, so a
// producer cannot emit a marker a reconciler is unable to act on; parseMarker()
// RETURNS null on anything malformed, so a reconciler sweeping bodies written by
// humans and other tools cannot be crashed by one.
//
// 🔴 THIS IS A SECOND IMPLEMENTATION OF ONE GRAMMAR, and saying so is the honest
// framing. The canonical Python module is talos-infra
// `scripts/lib/agent_obj_marker.py`, byte-mirrored into the in-cluster
// producers' ConfigMaps and drift-gated there. Python and JS cannot share a
// byte-mirror, so what keeps THESE two in step is the shared VECTORS array in
// test/agent-marker.test.mjs — the same literal strings the Python suite pins.
// If you change the grammar, change it in both, and move the vectors together.

import { createHash } from 'node:crypto';

export const MARKER_VERSION = '1';

// 🔴 ENUMERATED ALLOWLIST, not free text. Adding a kind is a deliberate act:
// something downstream has to be able to EVALUATE it, so a kind with no
// evaluator is a promise nobody keeps.
//
//   gh_pr_merged:<owner>/<repo>#<n>  closes when that PR is merged
//   alert_cleared:<alertname>        closes when that Prometheus alert stops firing
//   cmd_exit_zero:<id>               closes when a registered command exits 0
//   metric_below:<id>                closes when a registered metric drops below its bound
//   manual                           the escape hatch — no machine check, takes NO arg
export const COND_KINDS = Object.freeze([
  'gh_pr_merged',
  'alert_cleared',
  'cmd_exit_zero',
  'metric_below',
  'manual',
]);

// The one kind that is complete on its own. Every OTHER kind REQUIRES an
// argument: a bare `metric_below` names no metric and is exactly as unactionable
// as the free text the allowlist exists to forbid.
export const COND_KINDS_NO_ARG = Object.freeze(['manual']);

export const AGENT_LABEL_PREFIX = 'agent/';

const PRODUCER_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const RUN_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;
const INIT_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const FP_RE = /^[0-9a-f]{12}$/;
// A cond ARGUMENT may hold most printable ASCII (repo slugs, `#`, `:`, `/`) but
// NEVER whitespace and never `>` — either would terminate or split the HTML
// comment token stream this grammar rides on.
const COND_ARG_RE = /^[!-=?-~]{1,200}$/;

const MARKER_RE = /<!--\s*claw:obj\s+([^>]*?)\s*-->/g;
const TOKEN_RE = /^([a-z]+)=(\S+)$/;

export class MarkerError extends Error {
  constructor(message) {
    super(message);
    this.name = 'MarkerError';
  }
}

// The ClickUp TAG / GitHub LABEL that makes this producer's objects queryable
// without parsing a single body. That is the single highest-value half of
// Phase 0 — 112 of the 120 agent-attributable open issues carried zero labels.
export function labelFor(producer) {
  if (!PRODUCER_RE.test(producer ?? '')) {
    throw new MarkerError(`invalid producer ${JSON.stringify(producer)} (want ${PRODUCER_RE})`);
  }
  return AGENT_LABEL_PREFIX + producer;
}

export function validateCond(cond) {
  if (typeof cond !== 'string' || cond === '') {
    throw new MarkerError('cond is required');
  }
  const i = cond.indexOf(':');
  const kind = i < 0 ? cond : cond.slice(0, i);
  const arg = i < 0 ? '' : cond.slice(i + 1);
  if (!COND_KINDS.includes(kind)) {
    throw new MarkerError(
      `cond kind ${JSON.stringify(kind)} is not in the allowlist ${JSON.stringify([...COND_KINDS].sort())}`,
    );
  }
  if (COND_KINDS_NO_ARG.includes(kind)) {
    if (i >= 0) throw new MarkerError(`cond kind ${JSON.stringify(kind)} takes no argument`);
    return cond;
  }
  if (i < 0 || arg === '') {
    throw new MarkerError(`cond kind ${JSON.stringify(kind)} requires an argument (\`${kind}:<arg>\`)`);
  }
  if (!COND_ARG_RE.test(arg)) {
    throw new MarkerError(`cond argument ${JSON.stringify(arg)} contains whitespace or \`>\``);
  }
  return cond;
}

// sha256 of sorted-key compact JSON, first 12 hex. `claim` must be the STABLE
// tuple identifying what the object asserts, and must NOT contain anything that
// varies per run (a timestamp, a session id, a measured value).
//
// 🔴 Key-sorted RECURSIVELY, matching Python's json.dumps(sort_keys=True).
// JSON.stringify preserves insertion order, so a shallow sort would make two
// identical claims with differently-ordered NESTED objects fingerprint apart —
// silently, as two objects instead of one.
export function fingerprint(claim) {
  return createHash('sha256').update(canonicalJson(claim), 'utf8').digest('hex').slice(0, 12);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function buildMarker({ srcProducer, srcRunId, cond, fp = null, init = null }) {
  if (!PRODUCER_RE.test(srcProducer ?? '')) {
    throw new MarkerError(`invalid producer ${JSON.stringify(srcProducer)} (want ${PRODUCER_RE})`);
  }
  if (!RUN_ID_RE.test(srcRunId ?? '')) {
    throw new MarkerError(`invalid run-id ${JSON.stringify(srcRunId)} (want ${RUN_ID_RE})`);
  }
  validateCond(cond);
  const parts = [`v=${MARKER_VERSION}`];
  if (fp !== null && fp !== undefined) {
    if (!FP_RE.test(fp)) throw new MarkerError(`invalid fp ${JSON.stringify(fp)} (want 12 lowercase hex)`);
    parts.push(`fp=${fp}`);
  }
  parts.push(`src=${srcProducer}/${srcRunId}`);
  parts.push(`cond=${cond}`);
  if (init !== null && init !== undefined) {
    if (!INIT_RE.test(init)) throw new MarkerError(`invalid init slug ${JSON.stringify(init)} (want ${INIT_RE})`);
    parts.push(`init=${init}`);
  }
  return `<!-- claw:obj ${parts.join(' ')} -->`;
}

// Returns the FIRST well-formed marker in `text` as an object, else null.
// Never throws. A marker that is present but MALFORMED reads the same as absent
// — deliberately, so a half-written marker cannot be acted on as if complete.
export function parseMarker(text) {
  if (typeof text !== 'string') return null;
  MARKER_RE.lastIndex = 0;
  let m;
  while ((m = MARKER_RE.exec(text)) !== null) {
    const parsed = parseFields(m[1]);
    if (parsed) return parsed;
  }
  return null;
}

function parseFields(blob) {
  const fields = new Map();
  for (const tok of blob.split(/\s+/).filter(Boolean)) {
    const tm = TOKEN_RE.exec(tok);
    if (!tm) return null;
    if (fields.has(tm[1])) return null; // a repeated key is ambiguous, not a preference
    fields.set(tm[1], tm[2]);
  }
  for (const k of fields.keys()) {
    if (!['v', 'fp', 'src', 'cond', 'init'].includes(k)) return null;
  }
  if (fields.get('v') !== MARKER_VERSION) return null;
  const src = fields.get('src');
  const cond = fields.get('cond');
  if (!src || !cond) return null;
  const si = src.indexOf('/');
  if (si < 0) return null;
  const producer = src.slice(0, si);
  const runId = src.slice(si + 1);
  if (!PRODUCER_RE.test(producer) || !RUN_ID_RE.test(runId)) return null;
  try {
    validateCond(cond);
  } catch {
    return null;
  }
  const fp = fields.has('fp') ? fields.get('fp') : null;
  if (fp !== null && !FP_RE.test(fp)) return null;
  const init = fields.has('init') ? fields.get('init') : null;
  if (init !== null && !INIT_RE.test(init)) return null;
  const ci = cond.indexOf(':');
  return {
    v: MARKER_VERSION,
    fp,
    src,
    producer,
    runId,
    cond,
    condKind: ci < 0 ? cond : cond.slice(0, ci),
    condArg: ci < 0 ? null : cond.slice(ci + 1),
    init,
    label: AGENT_LABEL_PREFIX + producer,
  };
}

// ── Who is filing this, and what closes it ──────────────────────────────────
//
// Resolved from the environment so a CronJob, a session and a one-off script all
// stamp correctly without any of them restating the grammar.
//
// 🔴 SANITISED, NOT TRUSTED. buildMarker THROWS on a value outside its charset,
// and a stray session id must never be able to abort a task the caller asked
// for. An unusable value degrades to a valid default rather than an exception.
export const DEFAULT_PRODUCER = 'claude-code';

export function agentIdentity(env = process.env) {
  const producer = sanitise(env.CLAW_AGENT_PRODUCER, PRODUCER_RE, (s) => s.toLowerCase().replace(/[^a-z0-9-]/g, '-'))
    ?? DEFAULT_PRODUCER;
  const runId = sanitise(
    env.CLAW_AGENT_RUN_ID || env.CLAUDE_SESSION_ID || env.CLAUDE_CODE_SESSION_ID,
    RUN_ID_RE,
    (s) => s.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 128),
  ) ?? 'unknown';
  // 🔴 An unrecognised cond falls back to `manual`, NOT to silence. `manual` is
  // an honest "no machine can close this"; dropping the marker entirely would
  // make the object invisible again, which is the whole defect.
  let cond = 'manual';
  try {
    if (env.CLAW_AGENT_COND) cond = validateCond(env.CLAW_AGENT_COND);
  } catch {
    cond = 'manual';
  }
  const init = sanitise(env.CLAW_AGENT_INIT, INIT_RE, (s) => s.toLowerCase().replace(/[^a-z0-9-]/g, '-')) ?? null;
  return { producer, runId, cond, init, label: AGENT_LABEL_PREFIX + producer };
}

function sanitise(raw, re, clean) {
  if (typeof raw !== 'string' || raw === '') return null;
  if (re.test(raw)) return raw;
  const fixed = clean(raw).replace(/^-+/, '');
  return re.test(fixed) ? fixed : null;
}

// Append the marker to a task description, idempotently: a body that already
// carries a well-formed marker is returned untouched, so a caller that pre-built
// one (a deterministic reconciler with its own fp) keeps ITS marker rather than
// getting a second, contradictory one.
export function stampDescription(description, marker) {
  const body = typeof description === 'string' ? description : '';
  if (parseMarker(body)) return body;
  return body === '' ? marker : `${body.replace(/\s+$/, '')}\n\n${marker}`;
}
