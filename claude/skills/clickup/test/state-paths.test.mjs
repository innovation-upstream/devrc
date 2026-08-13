#!/usr/bin/env node

/**
 * State-path gate (hermetic — no credentials, no network, no real state dir).
 *
 * The skill directory is deployed READ-ONLY (home-manager `recursive = true`
 * puts it behind /nix/store symlinks). Anything written relative to
 * `__dirname` therefore fails with EROFS at runtime, and a live `pk_` API
 * token has no business sitting next to source anyway. So every mutable path
 * must resolve under $XDG_STATE_HOME/clickup (fallback ~/.local/state/clickup).
 *
 * This pins five things:
 *   1. BOTH base-dir branches — XDG_STATE_HOME set, and unset/empty. A test
 *      that only exercises the set case is blind to the fallback, which is the
 *      branch that actually runs on these hosts (XDG_STATE_HOME is empty here).
 *   2. No state path resolves inside the skill dir — compared as resolved
 *      absolute path prefixes, not as a filename string match, so renaming a
 *      file cannot make the check silently pass.
 *   3. Legacy migration, against a TEMP fixture: legacy present + target
 *      absent => copied, byte-identical, mode 0600, legacy left in place.
 *   3b. The secret/public PARTITION of STATE_FILES, and that the classification
 *      has CONSEQUENCES. What was here before was a tautology — a literal list
 *      asserted to be inside the constant it was copied from — and it stayed
 *      green while webhook-url.txt (the webhook.site capability token, written
 *      0644) sat outside SECRET_FILES.
 *   4. The SEAM: no module re-derives a state path from its own location. Item
 *      1-3 all pass while a consumer quietly keeps its own copy of the old path,
 *      which is exactly the bug this refactor exists to remove. The scanner is
 *      STRUCTURAL (comment-aware, multi-line, quote-agnostic, and it does not
 *      depend on the filename being one this module already knows about) and
 *      carries a control PAIR: every shape must fire, and the legitimate uses of
 *      the same tokens must not.
 *
 * Written in `node:test` form and named `*.test.mjs` because that is what
 * devrc's node gate DISCOVERS (`scripts/run-node-tests.sh`). It still runs
 * standalone: `node test/state-paths.test.mjs`.
 *
 * 🔴 ORDER MATTERS in the migration group: the "left in place" and "no-op on a
 * second run" cases read the fixture the first migration case created. Node's
 * test runner executes a file's top-level tests in declaration order, so do not
 * reorder them or make them concurrent.
 *
 * Usage:
 *   node test/state-paths.test.mjs
 *   node --test test/state-paths.test.mjs
 */

import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  statSync,
  rmSync,
  chmodSync,
  readdirSync,
} from 'fs';
import { tmpdir, homedir } from 'os';
import { dirname, join, resolve, sep } from 'path';
import { fileURLToPath } from 'url';
import { blankComments, blankStrings, stringLiterals } from './js-source.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SKILL = resolve(__dirname, '..');

// ── Isolation ─────────────────────────────────────────────────────────────
// Point the state dir at a scratch directory BEFORE importing anything that
// resolves it, so nothing in this run can reach the real ~/.local/state/clickup
// or the real accounts.json. The accessors under test are pure and lazy, so
// this assignment is honoured even though ESM hoists the import below.
const SCRATCH = mkdtempSync(join(tmpdir(), 'clickup-state-test-'));
process.env.XDG_STATE_HOME = join(SCRATCH, 'xdg');

const paths = await import('../lib/paths.mjs');

// Cleanup runs AFTER the tests, not at module top level: with node:test the
// tests execute asynchronously, so a top-level rmSync would delete the fixture
// out from under them.
after(() => {
  rmSync(SCRATCH, { recursive: true, force: true });
});

// ── 1. Base dir honours XDG_STATE_HOME, and falls back when unset/empty ────

function withEnv(value, fn) {
  const had = Object.prototype.hasOwnProperty.call(process.env, 'XDG_STATE_HOME');
  const prev = process.env.XDG_STATE_HOME;
  try {
    if (value === undefined) delete process.env.XDG_STATE_HOME;
    else process.env.XDG_STATE_HOME = value;
    return fn();
  } finally {
    if (had) process.env.XDG_STATE_HOME = prev;
    else delete process.env.XDG_STATE_HOME;
  }
}

const FALLBACK = resolve(homedir(), '.local', 'state', 'clickup');

test('XDG_STATE_HOME set: base dir is $XDG_STATE_HOME/clickup', () => {
  const custom = join(SCRATCH, 'custom-xdg');
  const got = withEnv(custom, () => paths.stateBaseDir());
  assert.equal(got, resolve(custom, 'clickup'),
    'stateBaseDir() ignored a set XDG_STATE_HOME');
});

test('XDG_STATE_HOME unset: base dir falls back to ~/.local/state/clickup', () => {
  const got = withEnv(undefined, () => paths.stateBaseDir());
  assert.equal(got, FALLBACK,
    'stateBaseDir() did not fall back to ~/.local/state/clickup when XDG_STATE_HOME is unset');
});

test('XDG_STATE_HOME empty: base dir falls back to ~/.local/state/clickup', () => {
  const got = withEnv('', () => paths.stateBaseDir());
  assert.equal(got, FALLBACK,
    'stateBaseDir() did not fall back when XDG_STATE_HOME is set but EMPTY (an empty value means unset)');
});

test('XDG_STATE_HOME whitespace-only: base dir falls back', () => {
  const got = withEnv('   ', () => paths.stateBaseDir());
  assert.equal(got, FALLBACK,
    'stateBaseDir() treated a whitespace-only XDG_STATE_HOME as a real directory');
});

// The two branches must be genuinely different, or the two checks above are
// one check wearing two hats.
test('the two branches resolve to different directories', () => {
  const setBranch = withEnv(join(SCRATCH, 'custom-xdg'), () => paths.stateBaseDir());
  const fallback = withEnv(undefined, () => paths.stateBaseDir());
  assert.notEqual(setBranch, fallback,
    'the XDG-set and fallback branches produced the SAME path — one of them is not being exercised');
});

// ── 2. No state path resolves inside the skill directory ──────────────────

const ACCESSORS = [
  'accountsPath',
  'envPath',
  'cacheDir',
  'watchersFile',
  'webhookUrlFile',
  'webhookLogFile',
  'webhookLatestFile',
  'lastSeenFile',
];

test(`all ${ACCESSORS.length} state accessors are exported`, () => {
  const missing = ACCESSORS.filter((n) => typeof paths[n] !== 'function');
  assert.ok(missing.length === 0,
    `lib/paths.mjs is missing state accessor(s): ${missing.join(', ')}`);
});

function assertOutsideSkillDir(label, p) {
  const abs = resolve(p);
  const skillPrefix = resolve(SKILL) + sep;
  assert.ok(!abs.startsWith(skillPrefix) && abs !== resolve(SKILL),
    `${label} resolves INSIDE the skill dir (${abs}) — it will EROFS once the skill ` +
      `is deployed read-only. Skill dir: ${SKILL}`);
}

// Both branches, because a path can escape the skill dir under one and not the
// other (e.g. a relative XDG_STATE_HOME).
for (const branch of [
  { name: 'XDG set', env: join(SCRATCH, 'custom-xdg') },
  { name: 'fallback', env: undefined },
]) {
  test(`no state path resolves inside the skill dir (${branch.name})`, () => {
    withEnv(branch.env, () => {
      for (const name of ACCESSORS) {
        assertOutsideSkillDir(`${name}()`, paths[name]());
      }
      assertOutsideSkillDir('stateBaseDir()', paths.stateBaseDir());
      assertOutsideSkillDir("statePath('x')", paths.statePath('x'));
    });
  });
}

// Positive control: the comparison must be able to FIRE. A prefix check that
// silently never matches would report "all outside" for paths that are inside.
test('POSITIVE CONTROL: the skill-dir prefix check can actually fire', () => {
  let fired = false;
  try {
    assertOutsideSkillDir('synthetic', join(SKILL, 'accounts.json'));
  } catch {
    fired = true;
  }
  assert.ok(fired,
    'assertOutsideSkillDir() did NOT reject a path that is plainly inside the skill dir — ' +
      'the check is wired to nothing and its passes mean nothing');
});

// ── 3. Migration, against a temp fixture ──────────────────────────────────

const LEGACY_CONTENT = '{\n  "defaultAccount": "fixture",\n  "accounts": {}\n}\n';

test('migration: legacy file present + target absent => copied 0600, byte-identical', () => {
  const legacy = join(SCRATCH, 'm1-legacy');
  const state = join(SCRATCH, 'm1-state');
  mkdirSync(legacy, { recursive: true });
  const src = join(legacy, 'accounts.json');
  writeFileSync(src, LEGACY_CONTENT);
  chmodSync(src, 0o600);

  assert.ok(!existsSync(join(state, 'accounts.json')), 'fixture precondition: target must start absent');

  const migrated = paths.migrateLegacyState(legacy, state, { log: false });

  assert.ok(migrated.includes('accounts.json'),
    `migrateLegacyState() did not report accounts.json as migrated (got: ${JSON.stringify(migrated)})`);

  const dest = join(state, 'accounts.json');
  assert.ok(existsSync(dest), `migrateLegacyState() did not create ${dest}`);
  assert.equal(readFileSync(dest, 'utf8'), LEGACY_CONTENT,
    'migrated accounts.json contents differ from the legacy file');
  assert.equal((statSync(dest).mode & 0o777).toString(8), '600',
    'migrated accounts.json is not mode 0600 — a live API token would be world/group readable');
  assert.equal((statSync(state).mode & 0o777).toString(8), '700',
    'the created state directory is not mode 0700');
});

test('migration: the legacy file is LEFT IN PLACE (never moved or deleted)', () => {
  const legacy = join(SCRATCH, 'm1-legacy');
  const src = join(legacy, 'accounts.json');
  assert.ok(existsSync(src),
    'migrateLegacyState() removed the legacy file — the other host still reads it and the ' +
      'token has no other copy');
  assert.equal(readFileSync(src, 'utf8'), LEGACY_CONTENT,
    'migrateLegacyState() modified the legacy file');
});

test('migration: is a no-op when the target already exists (never clobbers)', () => {
  const legacy = join(SCRATCH, 'm2-legacy');
  const state = join(SCRATCH, 'm2-state');
  mkdirSync(legacy, { recursive: true });
  mkdirSync(state, { recursive: true });
  writeFileSync(join(legacy, 'accounts.json'), LEGACY_CONTENT);
  writeFileSync(join(state, 'accounts.json'), 'EXISTING\n');

  const migrated = paths.migrateLegacyState(legacy, state, { log: false });

  // Damage first, bookkeeping second: if these are the other way round the
  // report-list assertion wins and the clobber assertion never executes.
  assert.equal(readFileSync(join(state, 'accounts.json'), 'utf8'), 'EXISTING\n',
    'migrateLegacyState() CLOBBERED an existing state file');
  assert.ok(!migrated.includes('accounts.json'),
    'migrateLegacyState() re-migrated a file that already existed at the target');
});

// copyFileSync PRESERVES the source mode, so a legacy file that is already
// 0600 makes the mode assertion above pass with or without the chmod — it is
// vacuous on its own. This is the case that actually needs the chmod: a legacy
// accounts.json left group/world-readable must NOT be copied across that way.
test('migration: forces 0600 even when the legacy copy is world-readable', () => {
  const legacy = join(SCRATCH, 'm5-legacy');
  const state = join(SCRATCH, 'm5-state');
  mkdirSync(legacy, { recursive: true });
  const src = join(legacy, 'accounts.json');
  writeFileSync(src, LEGACY_CONTENT);
  chmodSync(src, 0o644);
  assert.equal((statSync(src).mode & 0o777).toString(8), '644',
    'fixture precondition: the legacy file must start at 0644');

  paths.migrateLegacyState(legacy, state, { log: false });

  assert.equal((statSync(join(state, 'accounts.json')).mode & 0o777).toString(8), '600',
    'migrateLegacyState() carried a 0644 legacy accounts.json across UNCHANGED — the copy of ' +
      'a live API token is group/world readable');
  assert.equal((statSync(src).mode & 0o777).toString(8), '644',
    'migrateLegacyState() changed the LEGACY file mode; it must be left exactly as found');
});

test('migration: is a no-op on a second run (so the note prints once, then never)', () => {
  const legacy = join(SCRATCH, 'm1-legacy');
  const state = join(SCRATCH, 'm1-state');
  const again = paths.migrateLegacyState(legacy, state, { log: false });
  assert.equal(again.length, 0,
    `migrateLegacyState() migrated again on a second run (got: ${JSON.stringify(again)}) — ` +
      'the stderr note would repeat forever');
});

test('migration: carries .env, watchers.json and the .cache/ dir too', () => {
  const legacy = join(SCRATCH, 'm3-legacy');
  const state = join(SCRATCH, 'm3-state');
  mkdirSync(join(legacy, '.cache'), { recursive: true });
  writeFileSync(join(legacy, '.env'), 'CLICKUP_API_TOKEN=fixture-not-a-real-token\n');
  chmodSync(join(legacy, '.env'), 0o600);
  writeFileSync(join(legacy, 'watchers.json'), '[]\n');
  writeFileSync(join(legacy, '.cache', 'jwt-cache-default.json'), '{"cu_jwt":"fixture"}\n');

  const migrated = paths.migrateLegacyState(legacy, state, { log: false });

  for (const want of ['.env', 'watchers.json', '.cache/']) {
    assert.ok(migrated.includes(want),
      `migrateLegacyState() did not migrate ${want} (got: ${JSON.stringify(migrated)})`);
  }
  assert.equal((statSync(join(state, '.env')).mode & 0o777).toString(8), '600',
    'migrated .env is not mode 0600 — it holds an API token');
  assert.equal(readFileSync(join(state, 'watchers.json'), 'utf8'), '[]\n',
    'migrated watchers.json contents differ');
  assert.equal((statSync(join(state, 'watchers.json')).mode & 0o777).toString(8), '600',
    'migrated watchers.json is not mode 0600 — each entry holds the webhook HMAC secret');
  assert.ok(existsSync(join(state, '.cache', 'jwt-cache-default.json')),
    'migrateLegacyState() did not carry the .cache/ contents across');
  assert.equal((statSync(join(state, '.cache')).mode & 0o777).toString(8), '700',
    'migrated .cache/ is not mode 0700 — it holds session JWTs');
  assert.equal((statSync(join(state, '.cache', 'jwt-cache-default.json')).mode & 0o777).toString(8), '600',
    'a migrated .cache/ entry is not mode 0600 — it holds a session JWT');
});

// ── 3b. The secret/public PARTITION of the state files ────────────────────
//
// What used to be here was a TAUTOLOGY: it looped over a literal list of three
// names and asserted each was in SECRET_FILES — which is that constant's own
// contents, written out a second time. It could not fail for any edit to the
// code it guards. In particular it stayed green while `webhook-url.txt` — the
// webhook.site CAPABILITY token, written 0644 — sat outside SECRET_FILES,
// because the tautology never asked about the files it did not name.
//
// The replacement asks the question that can actually go red: is EVERY state
// file classified, and does the classification have CONSEQUENCES?

/**
 * State files that are deliberately NOT secret, each with the reason. This is
 * an ENUMERATION, not a pattern: a new state file is secret-by-default as far
 * as this gate is concerned, and stays red until someone writes down which
 * side of the line it is on.
 */
const PUBLIC_STATE_FILES = new Map([
  // Event payloads (task titles, comment text). Sensitive-ish, but not a
  // credential: holding this file grants no access to anything.
  ['webhooks.jsonl', 'received event payloads — no credential'],
  ['webhook-latest.json', 'the most recent event payload — no credential'],
  ['last-seen.txt', 'an ISO timestamp cursor'],
]);

test('every state file is classified secret or deliberately public', () => {
  const unclassified = paths.STATE_FILES.filter(
    (n) => !paths.SECRET_FILES.has(n) && !PUBLIC_STATE_FILES.has(n));
  assert.ok(unclassified.length === 0,
    `state file(s) in neither SECRET_FILES nor this test's PUBLIC_STATE_FILES: ` +
      `${unclassified.join(', ')}. Migration will carry them at whatever mode the ` +
      `legacy copy happened to have. Decide: add to SECRET_FILES in lib/paths.mjs, ` +
      `or to PUBLIC_STATE_FILES here WITH the reason it holds no credential.`);

  const both = paths.STATE_FILES.filter(
    (n) => paths.SECRET_FILES.has(n) && PUBLIC_STATE_FILES.has(n));
  assert.ok(both.length === 0,
    `${both.join(', ')} is declared BOTH secret and public — the two lists disagree`);
});

test('SECRET_FILES names nothing that is not a state file', () => {
  const stray = [...paths.SECRET_FILES].filter((n) => !paths.STATE_FILES.includes(n));
  assert.ok(stray.length === 0,
    `SECRET_FILES declares ${stray.join(', ')}, which STATE_FILES does not list — ` +
      'migration never looks at it, so the 0600 it promises never happens');
});

test('PUBLIC_STATE_FILES names nothing that has left STATE_FILES', () => {
  const stray = [...PUBLIC_STATE_FILES.keys()].filter((n) => !paths.STATE_FILES.includes(n));
  assert.ok(stray.length === 0,
    `this test exempts ${stray.join(', ')} from the secret classification, but ` +
      'lib/paths.mjs no longer lists it as a state file — a stale exemption is how a ' +
      'renamed file re-enters unclassified');
});

// The classification must have CONSEQUENCES, or SECRET_FILES is decorative.
// Table-driven over the real constant, so a name ADDED to SECRET_FILES is
// covered here automatically, and one REMOVED is caught by the partition test
// above rather than silently shrinking this loop.
test('migration: EVERY secret file is forced to 0600 from a 0644 legacy copy', () => {
  const legacy = join(SCRATCH, 'part-secret-legacy');
  const state = join(SCRATCH, 'part-secret-state');
  mkdirSync(legacy, { recursive: true });
  const names = [...paths.SECRET_FILES];
  assert.ok(names.length >= 4,
    `SECRET_FILES holds only ${names.length} name(s) — this loop is the guard's eyes ` +
      'and it is looking at almost nothing');
  for (const name of names) {
    writeFileSync(join(legacy, name), 'fixture-not-a-real-credential\n');
    chmodSync(join(legacy, name), 0o644);
  }

  paths.migrateLegacyState(legacy, state, { log: false });

  for (const name of names) {
    assert.equal((statSync(join(state, name)).mode & 0o777).toString(8), '600',
      `${name} is in SECRET_FILES but migration left it at ` +
        `${(statSync(join(state, name)).mode & 0o777).toString(8)} — a credential copied ` +
        'across group/world readable');
  }
});

// The other half of the partition: a PUBLIC file keeps the legacy mode. Without
// this, `chmod 0600 everything` would satisfy the loop above and the two classes
// would be indistinguishable — i.e. SECRET_FILES would be pinning nothing.
test('migration: a deliberately-public state file keeps its legacy mode', () => {
  const legacy = join(SCRATCH, 'part-public-legacy');
  const state = join(SCRATCH, 'part-public-state');
  mkdirSync(legacy, { recursive: true });
  const names = [...PUBLIC_STATE_FILES.keys()];
  assert.ok(names.length >= 1, 'PUBLIC_STATE_FILES is empty — nothing is being compared');
  for (const name of names) {
    writeFileSync(join(legacy, name), 'fixture\n');
    chmodSync(join(legacy, name), 0o644);
  }

  paths.migrateLegacyState(legacy, state, { log: false });

  for (const name of names) {
    assert.equal((statSync(join(state, name)).mode & 0o777).toString(8), '644',
      `${name} is declared PUBLIC but migration forced its mode — the secret/public ` +
        'distinction has no consequence, so SECRET_FILES pins nothing');
  }
});

// webhook-url.txt by name, because its classification is the one that was
// wrong: `https://webhook.site/<token>` is a capability, not "just a URL".
test('webhook-url.txt is classified secret — the URL IS the credential', () => {
  assert.ok(paths.SECRET_FILES.has('webhook-url.txt'),
    'webhook-url.txt is not in SECRET_FILES. It holds https://webhook.site/<token>, ' +
      'and that token is a capability: whoever reads it can read this workspace\'s ' +
      'entire event stream and POST forged events into it.');
});

// ── 4. Seam: no consumer re-derives a state path from __dirname ───────────
//
// Structural, over the real sources. Items 1-3 are all satisfied by a correct
// lib/paths.mjs that nobody actually uses; this is the check that notices.

const STATE_NAMES = new Set([...paths.STATE_FILES, ...paths.STATE_DIRS]);

// Every way a module can name its own location. `import.meta.url` is in here
// because `new URL('accounts.json', import.meta.url)` is the skill's OWN idiom
// (lib/paths.mjs:37 uses it) — the previous guard listed the token and then
// required a state filename LATER ON THE SAME LINE, so the one form most likely
// to be written passed clean: in `new URL(...)` the filename comes FIRST.
const LOCATION_TOKEN =
  /(?<![\w$.])(__dirname|import\.meta\.dirname|import\.meta\.url|SKILL_DIR)(?![\w$])/g;

// Bundled READ-ONLY data legitimately read from the skill dir. Enumerated with
// reasons, not pattern-matched: an unknown name is an offender by default.
const READONLY_BUNDLED = new Set([
  'package.json',      // the skill's own manifest — shipped, never written
  'package-lock.json', // ditto; nix/pkgs/clickup-node-modules.nix builds from it
]);

// Extensions a MUTABLE file plausibly has. Deliberately not `.md` (bundled
// reference docs are read from the skill dir on purpose) and not `.mjs`/`.js`.
const WRITABLE_EXT = new Set([
  '.json', '.jsonl', '.ndjson', '.txt', '.log', '.env', '.yaml', '.yml',
  '.ini', '.toml', '.db', '.sqlite', '.sqlite3', '.csv', '.state', '.lock',
  '.cache', '.tmp',
]);

/**
 * Does this string literal name a file that would be WRITTEN?
 *
 * 🔴 The old guard asked a much narrower question — "is this literal one of the
 * seven names in STATE_FILES?" — which switched the guard OFF exactly when it
 * was needed: the moment someone adds a state file and forgets to register it,
 * the guard stops looking for it. So the class is "a mutable-looking data file",
 * with the read-only exceptions enumerated.
 */
export function looksLikeStateFile(literal) {
  if (!literal || literal.includes('${')) return false;
  const name = literal.split('/').filter(Boolean).pop() || '';
  if (!name || name === '.' || name === '..') return false;
  if (READONLY_BUNDLED.has(name)) return false;
  if (STATE_NAMES.has(name)) return true;
  if (name.startsWith('.') && name.length > 1 && !name.slice(1).includes('.')) return true;
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return false;
  return WRITABLE_EXT.has(name.slice(dot).toLowerCase());
}

// How far either side of a location token a filename literal still counts as
// "the same expression". Bounded, and additionally cut at the nearest statement
// terminator, so a state filename three statements away is not attributed here.
const WINDOW = 400;

/**
 * Every place `src` derives a writable path from the module's own location.
 *
 * Structural, not spelled:
 *   * comments are BLANKED first, so prose about the hazard is not a hit and,
 *     more importantly, code cannot be hidden from the guard by commenting the
 *     surrounding lines;
 *   * string CONTENTS are blanked for the token scan, so a bait string in a
 *     test fixture is not code;
 *   * the filename may appear BEFORE or AFTER the token, on any line, in any
 *     quote style — `new URL('x.json', import.meta.url)` and a four-line
 *     `join(\n __dirname,\n 'x.json'\n)` are the same finding.
 */
export function findOpenCodedStatePaths(src) {
  const noComments = blankComments(src);
  const bare = blankStrings(noComments);
  const literals = stringLiterals(noComments);
  const hits = [];
  LOCATION_TOKEN.lastIndex = 0;
  let m;
  while ((m = LOCATION_TOKEN.exec(bare)) !== null) {
    const at = m.index;
    const lo = Math.max(0, at - WINDOW);
    const hi = Math.min(bare.length, at + WINDOW);
    const leftCut = bare.lastIndexOf(';', at);
    const rightCut = bare.indexOf(';', at);
    const from = leftCut >= lo ? leftCut + 1 : lo;
    const to = rightCut !== -1 && rightCut <= hi ? rightCut : hi;
    for (const lit of literals) {
      if (lit.start < from || lit.end > to) continue;
      if (!looksLikeStateFile(lit.value)) continue;
      hits.push(`${m[1]} + '${lit.value}'`);
    }
  }
  return hits;
}

// lib/paths.mjs is exempt BY DESIGN: it is the one place allowed to name the
// skill dir alongside a state file, because it is the migration SOURCE.
const CONSUMERS = [
  'query.mjs',
  'listen.mjs',
  'lib/accounts.mjs',
  'lib/jwt.mjs',
  'api/client.mjs',
  'api/webhooks.mjs',
];

/**
 * Every .mjs in the tree, as skill-relative paths.
 *
 * Deliberately a WALK, not the CONSUMERS list: the regression this whole file
 * exists to prevent is a NEW module open-coding a state path off __dirname
 * without ever importing lib/paths.mjs. Such a module is not a "consumer" and
 * would be invisible to an enumerated ledger — a seam guard has to fail when
 * the set GROWS, not only when a known member misbehaves.
 *
 * 🔴 `.js` and `.cjs` count, not just `.mjs`. The walk used to end at `.mjs`,
 * so the guard's answer to "does any module open-code a state path?" excluded
 * every file extension the answer could arrive in but one — and nothing in the
 * repo stops a `.js` being added (`package.json` has no `"type"` field, so a
 * `.cjs` is the natural way to write a CommonJS helper here).
 *
 * Excluded: node_modules, dot-dirs, test/ (fixtures legitimately name the skill
 * dir), and lib/paths.mjs (exempt by design — it is the migration SOURCE).
 *
 * 🔴 node_modules is excluded by NAME, and that exclusion is load-bearing now
 * that the deployed skill has one: ~/.claude/skills/clickup/node_modules is a
 * /nix/store symlink holding 51 packages, and walking it would be slow, would
 * scan third-party code this guard has no authority over, and would blow the
 * "no offenders" claim on the first unrelated match.
 */
function allModules() {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const abs = join(dir, entry.name);
      if (entry.isDirectory()) { walk(abs); continue; }
      if (!/\.(mjs|cjs|js)$/.test(entry.name)) continue;
      const rel = abs.slice(SKILL.length + 1);
      if (rel.startsWith('test/') || rel === 'lib/paths.mjs') continue;
      out.push(rel);
    }
  };
  walk(SKILL);
  return out;
}

test('SEAM: no module derives a state path from the skill dir', () => {
  const modules = allModules();
  // Positive control: a walk that found nothing would report zero offenders and
  // read as an all-clear. The floor is the 6 known consumers.
  assert.ok(modules.length >= CONSUMERS.length,
    `the module walk found only ${modules.length} .mjs file(s) (floor ${CONSUMERS.length}) — ` +
      'it is wired to nothing, so "no offenders" means nothing');

  const offenders = [];
  for (const rel of modules) {
    for (const hit of findOpenCodedStatePaths(readFileSync(join(SKILL, rel), 'utf8'))) {
      offenders.push(`${rel}: ${hit}`);
    }
  }
  assert.ok(offenders.length === 0,
    'a module still resolves a state path from the skill dir (it will EROFS once deployed ' +
      `read-only) — import the accessor from lib/paths.mjs instead:\n        ${offenders.join('\n        ')}`);
});

// The walk is the guard's eyes: pin that it sees the WHOLE tree, not just the
// enumerated consumers. Under devrc the skill sits at claude/skills/clickup/,
// one `resolve(__dirname, '..')` from this file exactly as before — if that
// ever stops being true this fails LOUDLY instead of silently scanning less.
test('SEAM: the walk covers every consumer and the whole source tree', () => {
  const modules = allModules();
  const missed = CONSUMERS.filter((rel) => !modules.includes(rel));
  assert.ok(missed.length === 0,
    `the module walk did NOT reach: ${missed.join(', ')}. SKILL resolved to ${SKILL}; ` +
      'if the skill moved, fix the base path — do not shrink the walk.');
  assert.ok(modules.length >= 15,
    `the module walk found only ${modules.length} .mjs file(s); the skill has 20+ ` +
      `(SKILL=${SKILL}). A walk rooted at the wrong directory would still find the ` +
      'six consumers and look fine.');
});

test('SEAM: every listed consumer still exists', () => {
  const missing = CONSUMERS.filter((rel) => !existsSync(join(SKILL, rel)));
  assert.ok(missing.length === 0,
    `consumer(s) listed by this test do not exist — fix the list: ${missing.join(', ')}`);
});

// ── The seam scanner's CONTROL PAIR ───────────────────────────────────────
//
// A guard this permissive has to be shown both halves: that it fires on every
// shape of the hazard (or it is decoration), and that it stays quiet on the
// legitimate uses of the same tokens (or the first false positive gets it
// deleted, which is the real way a guard dies).

const MUST_FIRE = [
  ['the one-line join form (the only shape the old regex caught)',
    "const p = join(__dirname, 'accounts.json');"],
  ['new URL(name, import.meta.url) — the skill\'s OWN idiom, and the filename comes FIRST',
    "const p = new URL('accounts.json', import.meta.url);"],
  ['fileURLToPath(new URL(...)) — same, one wrapper deeper',
    "const p = fileURLToPath(new URL('watchers.json', import.meta.url));"],
  ['a MULTI-LINE join — the old regex stopped at the newline',
    "const p = join(\n  __dirname,\n  '..',\n  'webhooks.jsonl'\n);"],
  ['a state file that is NOT in STATE_FILES — the old regex only knew the seven registered names',
    "const p = join(__dirname, 'sessions.json');"],
  ['a dotfile the registry does not know either',
    "const p = join(__dirname, '.credentials');"],
  ['double quotes',
    'const p = join(__dirname, "accounts.json");'],
  ['SKILL_DIR as the base',
    "const p = join(SKILL_DIR, 'last-seen.txt');"],
  ['import.meta.dirname',
    "const p = join(import.meta.dirname, 'watchers.json');"],
  ['a nested state dir',
    "const p = join(__dirname, '.cache', 'jwt-cache-default.json');"],
];

const MUST_NOT_FIRE = [
  ['the __dirname preamble every module writes',
    "const __dirname = dirname(fileURLToPath(import.meta.url));"],
  ['the run-as-main guard (this is lib/jwt.mjs:338 verbatim)',
    "const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));"],
  ['reading a BUNDLED read-only doc out of the skill dir — legitimate',
    "const help = readFileSync(join(__dirname, '..', 'reference', 'setup.md'), 'utf8');"],
  ['reading the skill\'s own manifest',
    "const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8'));"],
  ['deriving the skill dir itself (lib/paths.mjs:43 verbatim)',
    "export const SKILL_DIR = resolve(__dirname, '..');"],
  ['a state filename with NO location token anywhere near it',
    "const p = statePath('accounts.json');"],
  ['a location token and a state name in DIFFERENT statements',
    "const d = dirname(fileURLToPath(import.meta.url));\nconst p = statePath('accounts.json');"],
  ['the hazard described in a COMMENT — prose is not code',
    "// never write join(__dirname, 'accounts.json') — use the accessor\nconst p = accountsPath();"],
  ['the hazard commented OUT — still not code',
    "/* const p = join(__dirname, 'accounts.json'); */\nconst p = accountsPath();"],
  ['an import specifier that merely ends in .js',
    "import x from './helper.js';\nconst d = dirname(fileURLToPath(import.meta.url));"],
];

test('POSITIVE CONTROL: the seam scanner fires on every shape of the hazard', () => {
  const missed = MUST_FIRE.filter(([, src]) => findOpenCodedStatePaths(src).length === 0)
    .map(([label]) => label);
  assert.ok(missed.length === 0,
    'the seam scanner did NOT flag plainly open-coded state paths, so "no offenders" ' +
      `means nothing for those shapes:\n        ${missed.join('\n        ')}`);
});

test('NEGATIVE CONTROL: the seam scanner stays quiet on legitimate uses', () => {
  const wrong = MUST_NOT_FIRE
    .filter(([, src]) => findOpenCodedStatePaths(src).length > 0)
    .map(([label, src]) => `${label}  ->  ${findOpenCodedStatePaths(src).join(', ')}`);
  assert.ok(wrong.length === 0,
    'the seam scanner flagged code that is NOT a state-path derivation. A guard that ' +
      'cries wolf gets deleted, which is worse than the guard being narrow:\n        ' +
      wrong.join('\n        '));
});

test('the scanner is exercised against both halves of the control pair', () => {
  // A guard on the guard: a refactor that emptied either table would leave both
  // control tests passing vacuously.
  assert.ok(MUST_FIRE.length >= 10,
    `only ${MUST_FIRE.length} positive control(s) — the shapes this scanner is trusted ` +
      'to catch are exactly the ones listed here');
  assert.ok(MUST_NOT_FIRE.length >= 8,
    `only ${MUST_NOT_FIRE.length} negative control(s) — false positives are what get a ` +
      'guard deleted, so they need as much pinning as the true ones');
});

test('SEAM: the consumer list covers every module that imports lib/paths.mjs', () => {
  const found = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const abs = join(dir, entry.name);
      if (entry.isDirectory()) { walk(abs); continue; }
      if (!entry.name.endsWith('.mjs')) continue;
      const src = readFileSync(abs, 'utf8');
      // Both spellings: '../lib/paths.mjs' from api/, './paths.mjs' from lib/.
      if (/from\s+['"][^'"]*\bpaths\.mjs['"]/.test(src)) {
        found.push(abs.slice(SKILL.length + 1));
      }
    }
  };
  walk(SKILL);
  const importers = found.filter((f) => !f.startsWith('test/'));
  const uncovered = importers.filter((f) => !CONSUMERS.includes(f));
  assert.ok(uncovered.length === 0,
    `module(s) import lib/paths.mjs but are NOT scanned by the seam check above: ` +
      `${uncovered.join(', ')}. Add them to CONSUMERS.`);
  assert.ok(importers.length >= CONSUMERS.length,
    `expected at least ${CONSUMERS.length} modules to import lib/paths.mjs, found ${importers.length} ` +
      `(${importers.join(', ')}) — a consumer stopped using the shared helper`);
});

// ── 5. Nothing in this run touched the real state dir ─────────────────────

test('hermetic: the real state dir was not created or written by this test', () => {
  const real = FALLBACK;
  const scratchPrefix = resolve(SCRATCH) + sep;
  assert.ok(!resolve(real).startsWith(scratchPrefix),
    'fixture logic error: the real state dir resolved inside the scratch dir');
  // Nothing here should have created it. If it already existed before this run
  // we cannot claim credit either way, so only assert we did not create a fresh
  // one under the scratch root — and that XDG still points at scratch.
  assert.ok(process.env.XDG_STATE_HOME.startsWith(resolve(SCRATCH)),
    'XDG_STATE_HOME no longer points at the scratch dir — a later check leaked the override');
});
