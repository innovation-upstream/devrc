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
 * This pins four things:
 *   1. BOTH base-dir branches — XDG_STATE_HOME set, and unset/empty. A test
 *      that only exercises the set case is blind to the fallback, which is the
 *      branch that actually runs on these hosts (XDG_STATE_HOME is empty here).
 *   2. No state path resolves inside the skill dir — compared as resolved
 *      absolute path prefixes, not as a filename string match, so renaming a
 *      file cannot make the check silently pass.
 *   3. Legacy migration, against a TEMP fixture: legacy present + target
 *      absent => copied, byte-identical, mode 0600, legacy left in place.
 *   4. The SEAM: no consumer re-derives a state path from __dirname. Item 1-3
 *      all pass while a consumer quietly keeps its own copy of the old path,
 *      which is exactly the bug this refactor exists to remove.
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

// Every file the skill writes that carries a credential must be declared
// secret, or migration silently carries a 0644 copy across.
test('every credential-bearing state file is declared secret', () => {
  for (const name of ['accounts.json', '.env', 'watchers.json']) {
    assert.ok(paths.SECRET_FILES.has(name),
      `${name} holds a credential but is NOT in SECRET_FILES — it will be migrated at ` +
        `whatever mode the legacy copy happened to have`);
  }
});

test('migration: refuses when legacy and state are the same directory', () => {
  const same = join(SCRATCH, 'm4');
  mkdirSync(same, { recursive: true });
  writeFileSync(join(same, 'accounts.json'), LEGACY_CONTENT);
  const migrated = paths.migrateLegacyState(same, same, { log: false });
  assert.equal(migrated.length, 0,
    'migrateLegacyState() tried to migrate a directory onto itself');
});

// ── 4. Seam: no consumer re-derives a state path from __dirname ───────────
//
// Structural, over the real sources. Items 1-3 are all satisfied by a correct
// lib/paths.mjs that nobody actually uses; this is the check that notices.

const STATE_NAMES = [...paths.STATE_FILES, ...paths.STATE_DIRS];
const NAME_ALT = STATE_NAMES.map((n) => n.replace(/[.]/g, '\\.')).join('|');
// A skill-dir-derived base on the same expression as a state file name.
const OPEN_CODED = new RegExp(
  `(__dirname|import\\.meta\\.dirname|import\\.meta\\.url|SKILL_DIR)[^\\n;]*['"\`](${NAME_ALT})['"\`]`
);

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
      if (!entry.name.endsWith('.mjs')) continue;
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
    const m = OPEN_CODED.exec(readFileSync(join(SKILL, rel), 'utf8'));
    if (m) offenders.push(`${rel}: ${m[0].trim()}`);
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

test('POSITIVE CONTROL: the seam scanner can actually match', () => {
  const bait = "const p = join(__dirname, 'accounts.json');";
  assert.ok(OPEN_CODED.test(bait),
    'the open-coded-path regex did not match a plainly open-coded path — it is wired to ' +
      'nothing, so "no offenders" means nothing');
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
