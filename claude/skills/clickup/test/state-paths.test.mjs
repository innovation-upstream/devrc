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
 *      green while a capability token, written 0644, sat outside SECRET_FILES.
 *   4. The SEAM: no module WRITES to a path it derived from its own location.
 *      Items 1-3 all pass while a consumer quietly keeps its own copy of the old
 *      path, which is exactly the bug this refactor exists to remove. The
 *      scanner is STRUCTURAL (comment-aware, multi-line, quote-agnostic, blind
 *      to filenames, and it follows a path bound to a variable and written a
 *      statement later) and carries a control PAIR: every shape must fire, and
 *      the legitimate uses of the same tokens must not.
 *
 *      🔴 It asks about a WRITE, not about a filename. The previous version
 *      never made that distinction, so four plain `readFileSync` calls on files
 *      SHIPPED with the skill were reported as defects under a message
 *      ("it will EROFS once deployed read-only") that is false for a read. The
 *      escape hatch was an exemption list of two filenames, and the negative
 *      controls were calibrated to that list rather than to the class.
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

// INVARIANT GUARD, not regression coverage — labelled as one deliberately.
//
// #438 deleted a `resolve(legacyDir) === resolve(stateDir)` clause from
// migrateLegacyState() after showing it was DEAD (with src and dest the same
// path, the existsSync(dest) skip fires for every name that exists and the
// !existsSync(src) skip for every name that does not), and deleted the test
// with it. The clause was dead; the BEHAVIOUR it described is not, and it went
// unasserted. This pins the behaviour without reinstating the clause: a future
// rewrite that reorders those two skips would silently start copying a file
// onto itself.
test('migration: migrating a directory ONTO ITSELF is a no-op', () => {
  const dir = join(SCRATCH, 'same-dir');
  mkdirSync(join(dir, '.cache'), { recursive: true });
  writeFileSync(join(dir, 'accounts.json'), LEGACY_CONTENT);
  chmodSync(join(dir, 'accounts.json'), 0o600);
  writeFileSync(join(dir, '.cache', 'jwt-cache-default.json'), '{"cu_jwt":"fixture"}\n');
  const before = readdirSync(dir).sort();

  const migrated = paths.migrateLegacyState(dir, dir, { log: false });

  assert.deepEqual(migrated, [],
    `migrateLegacyState(d, d) reported ${JSON.stringify(migrated)} as migrated — it copied ` +
      'files onto themselves and told the user it had moved their state');
  assert.equal(readFileSync(join(dir, 'accounts.json'), 'utf8'), LEGACY_CONTENT,
    'accounts.json was damaged by a same-directory migration — it holds the only copy of a ' +
      'live API token');
  assert.equal((statSync(join(dir, 'accounts.json')).mode & 0o777).toString(8), '600');
  assert.equal(readFileSync(join(dir, '.cache', 'jwt-cache-default.json'), 'utf8'),
    '{"cu_jwt":"fixture"}\n', 'the .cache contents were damaged');
  assert.deepEqual(readdirSync(dir).sort(), before,
    'a same-directory migration changed the directory listing');
});

test('migration: carries .env and the .cache/ dir too', () => {
  const legacy = join(SCRATCH, 'm3-legacy');
  const state = join(SCRATCH, 'm3-state');
  mkdirSync(join(legacy, '.cache'), { recursive: true });
  writeFileSync(join(legacy, '.env'), 'CLICKUP_API_TOKEN=fixture-not-a-real-token\n');
  chmodSync(join(legacy, '.env'), 0o600);
  writeFileSync(join(legacy, '.cache', 'jwt-cache-default.json'), '{"cu_jwt":"fixture"}\n');

  const migrated = paths.migrateLegacyState(legacy, state, { log: false });

  for (const want of ['.env', '.cache/']) {
    assert.ok(migrated.includes(want),
      `migrateLegacyState() did not migrate ${want} (got: ${JSON.stringify(migrated)})`);
  }
  assert.equal(readFileSync(join(state, '.env'), 'utf8'),
    'CLICKUP_API_TOKEN=fixture-not-a-real-token\n', 'migrated .env contents differ');
  assert.equal((statSync(join(state, '.env')).mode & 0o777).toString(8), '600',
    'migrated .env is not mode 0600 — it holds an API token');
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
// code it guards. In particular it stayed green while a CAPABILITY token that
// the skill wrote 0644 sat outside SECRET_FILES, because the tautology never
// asked about the files it did not name.
//
// The replacement asks the question that can actually go red: is EVERY state
// file classified, and does the classification have CONSEQUENCES?

/**
 * State files that are deliberately NOT secret, each with the reason. This is
 * an ENUMERATION, not a pattern: a new state file is secret-by-default as far
 * as this gate is concerned, and stays red until someone writes down which
 * side of the line it is on.
 *
 * It is EMPTY today. Its three entries were the webhook listener's event log,
 * latest-event snapshot and timestamp cursor; removing the listener removed
 * them, and every state file that remains is a credential. The partition still
 * holds — STATE_FILES == SECRET_FILES ⊎ ∅ — and the classification test below
 * is still what goes red when a new state file arrives unclassified.
 */
const PUBLIC_STATE_FILES = new Map([]);

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
  assert.ok(names.length >= 2,
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

// The other half of the partition USED to be a behavioural test: a PUBLIC file
// keeps its legacy mode, so `chmod 0600 everything` could not satisfy the loop
// above and the two classes stayed distinguishable.
//
// 🔴 That test has no subject any more. PUBLIC_STATE_FILES is empty, so no input
// reaches the mode-PRESERVING branch of migrateLegacyState, and a loop over zero
// names is a test that passes by looking at nothing — the vacuous green this
// whole section was written to eliminate. It is deleted rather than left to run
// empty, and replaced by the tripwire below.
//
// This IS a real reduction in coverage: nothing now proves migration reads
// SECRET_FILES rather than hardcoding 0600. It is not restorable without a
// public state file to compare against, so the tripwire fails the moment one
// exists, which is the moment the behavioural test becomes writable again.
test('PUBLIC_STATE_FILES is empty — restore the behavioural mode test if that changes', () => {
  assert.equal(PUBLIC_STATE_FILES.size, 0,
    `PUBLIC_STATE_FILES now declares ${[...PUBLIC_STATE_FILES.keys()].join(', ')}. The ` +
      'mode-preserving branch of migrateLegacyState is reachable again, so restore the ' +
      "deleted 'a deliberately-public state file keeps its legacy mode' test (git log this " +
      'file) — otherwise nothing proves the secret/public split has any consequence and ' +
      'SECRET_FILES is decorative.');
});

// ── 4. Seam: no consumer re-derives a state path from __dirname ───────────
//
// Structural, over the real sources. Items 1-3 are all satisfied by a correct
// lib/paths.mjs that nobody actually uses; this is the check that notices.

// Every way a module can name its own location. `import.meta.url` is in here
// because `new URL('accounts.json', import.meta.url)` is the skill's OWN idiom
// (lib/paths.mjs:37 uses it) — the previous guard listed the token and then
// required a state filename LATER ON THE SAME LINE, so the one form most likely
// to be written passed clean: in `new URL(...)` the filename comes FIRST.
const LOCATION_TOKEN =
  /(?<![\w$.])(__dirname|import\.meta\.dirname|import\.meta\.url|SKILL_DIR)(?![\w$])/g;

/**
 * 🔴 THE SCANNER ASKS ABOUT A **WRITE**, NOT ABOUT A FILENAME.
 *
 * What was here before never distinguished a READ from a WRITE: any literal
 * with a mutable-looking extension near a location token was an offender, under
 * a message ("it will EROFS once deployed read-only") that is FALSE for a read.
 * The escape hatch was an enumerated `READONLY_BUNDLED` holding exactly
 * `package.json` and `package-lock.json`, and the negative-control table was
 * calibrated to THAT LIST rather than to the class: its two read-only cases
 * were the two names that happened to be exempt. A guard that cries wolf gets
 * deleted, and the exemption list would have had to grow by one entry per
 * bundled data file forever.
 *
 * 🔴 HOW MUCH OF THAT IS OBSERVED, PRECISELY. The shapes below are CONSTRUCTED
 * — they are what the old predicate does when a bundled read is written in one
 * of them, not defects it reported in this repo:
 *
 *     join(__dirname, '..', 'data', 'custom-fields.json')
 *     join(__dirname, 'schema.yaml')
 *     new URL('prompt.txt', import.meta.url)
 *     join(__dirname, '..', '.gitignore')
 *
 * None of those four files is read by any module in the skill; repo-wide they
 * occur only as fixture strings in this file. Run over the CURRENT 21 modules,
 * `origin/main`'s scanner reports **0 offenders — identical to this one**, with
 * both firing on a planted `writeFileSync(join(__dirname, 'sessions.json'), d)`
 * (so neither zero is a dead scanner). This rewrite therefore changes nothing
 * observable today: it is FORWARD-LOOKING hardening against the first bundled
 * data file somebody reads off `__dirname`, plus the indirect write shapes the
 * literal predicate structurally cannot see. Calling constructed examples
 * "verified false positives" is how a claim like that stops being checkable.
 *
 * The question it asks now is the one the message actually makes: does a path
 * derived from the module's own location end up in the TARGET position of a
 * call that WRITES? That needs no filename registry, no extension list and no
 * exemptions — a write into the skill dir is an EROFS whatever the file is
 * called, including `package.json`.
 */

/**
 * Calls that write, and WHICH argument is the path they write TO.
 *
 * The index matters: `cpSync(src, dest)` and `copyFileSync(src, dest)` READ
 * their first argument, and copying bundled data OUT of the skill dir is
 * legitimate. Flagging the whole argument list would reintroduce the false
 * positive one level down.
 */
const WRITE_TARGET_ARG = new Map([
  ['writeFileSync', [0]], ['writeFile', [0]],
  ['appendFileSync', [0]], ['appendFile', [0]],
  ['mkdirSync', [0]], ['mkdir', [0]],
  ['rmSync', [0]], ['rm', [0]],
  ['rmdirSync', [0]], ['rmdir', [0]],
  ['unlinkSync', [0]], ['unlink', [0]],
  ['truncateSync', [0]], ['truncate', [0]],
  ['chmodSync', [0]], ['chmod', [0]],
  ['chownSync', [0]], ['chown', [0]],
  ['utimesSync', [0]], ['utimes', [0]],
  ['mkdtempSync', [0]], ['mkdtemp', [0]],
  ['createWriteStream', [0]],
  // source, DESTINATION — only the destination is written.
  ['cpSync', [1]], ['cp', [1]],
  ['copyFileSync', [1]], ['copyFile', [1]],
  ['renameSync', [1]], ['rename', [1]],
  ['linkSync', [1]], ['link', [1]],
  ['symlinkSync', [1]], ['symlink', [1]],
]);

/**
 * `openSync`/`open` are read-OR-write depending on the flags, so they are
 * decided by the mode argument rather than by the name. A `'r'` open of a
 * bundled file is a read like any other; anything with `w`, `a` or `+` is a
 * write.
 *
 * 🔴 THE FLAG IS ARGUMENT 1 ONWARD, NEVER ARGUMENT 0. `WRITE_FLAG` used to be
 * tested against every literal in the whole call, and argument 0 is the PATH:
 * `openSync(join(__dirname, 'accounts.json'), 'r')` fired because the FILENAME
 * begins with an `a`, as did `webhooks.jsonl` (w) and `c++notes.txt` (+). The
 * shipped negative control used `schema.yaml` — an `s`, the one initial that
 * cannot trip it — so the table was calibrated AROUND the bug and stayed green.
 * The flags now come from the argument spans after the path only.
 *
 * A non-literal flags argument (`openSync(p, mode)`) has no literal to test and
 * so does not fire. That is a deliberate false negative: it is unreadable to a
 * scanner, and firing on it would flag every read too.
 */
const AMBIGUOUS_OPEN = new Set(['openSync', 'open']);
const WRITE_FLAG = /^[wa]|\+/;

const IDENTIFIER = /(?<![\w$.])([A-Za-z_$][\w$]*)/g;

/** The balanced `(...)` beginning at `open` (an index pointing at the paren). */
function argSpan(bare, open) {
  let depth = 0;
  for (let i = open; i < bare.length; i++) {
    const c = bare[i];
    if (c === '(' || c === '[' || c === '{') depth++;
    else if (c === ')' || c === ']' || c === '}') {
      depth--;
      if (depth === 0) return { start: open + 1, end: i };
    }
  }
  return null;
}

/** Split an argument list at TOP-LEVEL commas: [{start, end}, …]. */
function splitArgs(bare, span) {
  const out = [];
  let depth = 0;
  let from = span.start;
  for (let i = span.start; i < span.end; i++) {
    const c = bare[i];
    if (c === '(' || c === '[' || c === '{') depth++;
    else if (c === ')' || c === ']' || c === '}') depth--;
    else if (c === ',' && depth === 0) {
      out.push({ start: from, end: i });
      from = i + 1;
    }
  }
  out.push({ start: from, end: span.end });
  return out;
}

/** Does a location token occur inside [start, end)? */
function locationTokenIn(bare, start, end) {
  LOCATION_TOKEN.lastIndex = 0;
  let m;
  while ((m = LOCATION_TOKEN.exec(bare)) !== null) {
    if (m.index >= start && m.index < end) return m[1];
  }
  return null;
}

/**
 * Every place `src` derives a path from the module's own location and then
 * WRITES to it.
 *
 * Structural, not spelled:
 *   * comments are BLANKED first, so prose about the hazard is not a hit and,
 *     more importantly, code cannot be hidden from the guard by commenting the
 *     surrounding lines;
 *   * string CONTENTS are blanked for the token scan, so a bait string in a
 *     fixture is not code — but a template's `${ … }` is KEPT, because that is
 *     code (`` `${__dirname}/accounts.json` `` is a real idiom in this skill);
 *   * the WRITE SHAPE is irrelevant — `writeFileSync(new URL('x',
 *     import.meta.url))`, a four-line `join(\n __dirname,\n 'x'\n)`, a template
 *     literal, and a `const p = …` bound one statement earlier and written
 *     LATER are all the same finding;
 *   * no filename is required at all: `writeFileSync(join(__dirname, name))`
 *     with a computed `name` is the same EROFS, and the literal-based predicate
 *     could not see it.
 *
 * 🔴 WHAT IT DOES **NOT** SEE — measured, not assumed. Each of these is silent,
 * and none is a regression (the predicate this replaced saw none of them
 * either); they are the honest edge of "the shape is irrelevant":
 *
 *   * a TWO-HOP binding — `const a = join(__dirname,'x'); const b = a;` — the
 *     binding table is one level deep, by design;
 *   * an ALIASED import: `import { writeFileSync as wfs }` … `wfs(p, d)`. The
 *     call names are matched literally, so a rename escapes;
 *   * a path handed to a FUNCTION and written inside it — that needs dataflow,
 *     not a scanner.
 *
 * The guard's job is this repo's own modules, where the inline and one-hop
 * forms are what actually get written. Widen it when one of the above appears,
 * and add the case to MUST_FIRE in the same commit.
 */
export function findOpenCodedStatePaths(src) {
  const noComments = blankComments(src);
  // keepSubstitutions: `${__dirname}` is a location token in code, not text.
  const bare = blankStrings(noComments, { keepSubstitutions: true });
  const literals = stringLiterals(noComments);
  const hits = new Map(); // dedup by offset

  // The path a location token is BOUND to, if any: `const p = join(__dirname,
  // 'x')` writes the hazard into `p`, and the write happens on another line.
  // Nearest preceding declaration/assignment within the same statement.
  //
  // Deliberately NOT scope-aware — this is a scanner, not a parser. Binding
  // names are file-global here, so a `p` derived from __dirname in one function
  // and a DIFFERENT `p` written in another is reported. That over-approximates
  // in the safe direction (a false positive is a loud question about a name
  // collision; a false negative is the EROFS this file exists to prevent), and
  // the negative-control table is what keeps the over-approximation honest.
  const boundNames = new Map(); // name -> {token, literal}
  LOCATION_TOKEN.lastIndex = 0;
  let t;
  while ((t = LOCATION_TOKEN.exec(bare)) !== null) {
    const cut = bare.lastIndexOf(';', t.index);
    const stmtStart = cut === -1 ? 0 : cut + 1;
    const decl = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=|(?:^|\n)\s*([A-Za-z_$][\w$]*)\s*=[^=]/g;
    const head = bare.slice(stmtStart, t.index);
    let d;
    let name = null;
    while ((d = decl.exec(head)) !== null) name = d[1] || d[2];
    if (!name) continue;
    const stmtEnd = bare.indexOf(';', t.index);
    const lit = literals.find(
      (l) => l.start >= stmtStart && (stmtEnd === -1 || l.end <= stmtEnd) && l.start > t.index - 400);
    boundNames.set(name, { token: t[1], literal: lit ? lit.value : null });
  }

  const CALL = /(?<![\w$])([A-Za-z_$][\w$]*)\s*\(/g;
  let m;
  while ((m = CALL.exec(bare)) !== null) {
    const call = m[1];
    const isOpen = AMBIGUOUS_OPEN.has(call);
    if (!WRITE_TARGET_ARG.has(call) && !isOpen) continue;
    const span = argSpan(bare, m.index + m[0].length - 1);
    if (!span) continue;
    const args = splitArgs(bare, span);

    if (isOpen) {
      // Arguments 1+ only. Argument 0 is the path, and reading it as a flag
      // makes the BASENAME decide — see WRITE_FLAG.
      const flagArgs = args.slice(1);
      const flags = literals.filter(
        (l) => flagArgs.some((a) => l.start >= a.start && l.end <= a.end));
      if (!flags.some((l) => WRITE_FLAG.test(l.value))) continue;
    }
    const targets = isOpen ? [0] : WRITE_TARGET_ARG.get(call);

    for (const idx of targets) {
      const arg = args[idx];
      if (!arg) continue;

      // (a) the derivation is written INLINE in the target position.
      const token = locationTokenIn(bare, arg.start, arg.end);
      if (token) {
        const lit = literals
          .filter((l) => l.start >= arg.start && l.end <= arg.end)
          .map((l) => l.value)
          .filter((v) => v && v !== '..' && v !== '.')
          .pop();
        hits.set(arg.start, `${token} + '${lit ?? '<computed>'}'  ->  ${call}()`);
        continue;
      }

      // (b) the derivation was BOUND earlier and only the name appears here.
      IDENTIFIER.lastIndex = 0;
      let id;
      while ((id = IDENTIFIER.exec(bare.slice(arg.start, arg.end))) !== null) {
        const bound = boundNames.get(id[1]);
        if (!bound) continue;
        hits.set(arg.start, `${bound.token} + '${bound.literal ?? '<computed>'}' (via \`${id[1]}\`)  ->  ${call}()`);
        break;
      }
    }
  }
  return [...hits.values()];
}

// lib/paths.mjs is exempt BY DESIGN: it is the one place allowed to name the
// skill dir alongside a state file, because it is the migration SOURCE.
const CONSUMERS = [
  'query.mjs',
  'lib/accounts.mjs',
  'lib/jwt.mjs',
  'api/client.mjs',
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
const MODULE_EXT = /\.(mjs|cjs|js)$/;

function allModules(root = SKILL) {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const abs = join(dir, entry.name);
      if (entry.isDirectory()) { walk(abs); continue; }
      if (!MODULE_EXT.test(entry.name)) continue;
      const rel = abs.slice(root.length + 1);
      if (rel.startsWith('test/') || rel === 'lib/paths.mjs') continue;
      out.push(rel);
    }
  };
  walk(root);
  return out;
}

// The `.js`/`.cjs` widening was INERT: the skill has 31 .mjs and zero .js/.cjs,
// so narrowing the pattern back to `.mjs` cost nothing and nothing went red.
// A fixture tree makes the extension set an asserted property rather than a
// hope — and does it BEHAVIOURALLY, through the walk, not by matching the regex
// against a list of names.
test('SEAM: the module walk covers .mjs, .js AND .cjs — and only those', () => {
  const root = mkdtempSync(join(SCRATCH, 'walk-'));
  const put = (rel, body = '// fixture\n') => {
    const abs = join(root, rel);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, body);
  };
  put('a.mjs'); put('lib/b.js'); put('api/c.cjs');
  put('d.md'); put('e.json'); put('f.txt'); put('g.mjs.bak');
  put('test/should-be-skipped.mjs');
  put('node_modules/pkg/vendored.js');
  put('.hidden/ignored.mjs');

  const got = allModules(root).sort();
  assert.deepEqual(got, ['a.mjs', 'api/c.cjs', 'lib/b.js'],
    `the walk collected ${JSON.stringify(got)}. It must see every extension a module can ` +
      'arrive in — package.json has no "type" field, so a .cjs is the natural way to write ' +
      'a CommonJS helper here and a .js is legal too — and must keep skipping test/, ' +
      'node_modules and dot-dirs.');
});

test('SEAM: the walk finds a .cjs offender the .mjs-only walk would miss', () => {
  // Reachability for the widening: an offender that exists ONLY in a .cjs file.
  const root = mkdtempSync(join(SCRATCH, 'walk-cjs-'));
  writeFileSync(join(root, 'helper.cjs'),
    "const { writeFileSync } = require('fs');\nwriteFileSync(join(__dirname, 'sessions.json'), d);\n");
  const offenders = allModules(root).flatMap(
    (rel) => findOpenCodedStatePaths(readFileSync(join(root, rel), 'utf8')));
  assert.equal(offenders.length, 1,
    'a state write open-coded in a .cjs module was invisible to the walk, so the guard\'s ' +
      'answer to "does any module open-code a state path?" excludes every extension the ' +
      'answer could arrive in but one');
});

test('SEAM: no module derives a state path from the skill dir', () => {
  const modules = allModules();
  // Positive control: a walk that found nothing would report zero offenders and
  // read as an all-clear. The floor is the 4 known consumers.
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
    'a module WRITES to a path it derived from the skill dir. That is an EROFS once the ' +
      'skill is deployed read-only (home-manager puts it behind /nix/store symlinks) — ' +
      `import the accessor from lib/paths.mjs instead:\n        ${offenders.join('\n        ')}`);
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
      'four consumers and look fine.');
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
//
// 🔴 THE FILENAMES BELOW ARE ARBITRARY, AND DELIBERATELY SO. The scanner
// consults no registry — `watchers.json`, `webhooks.jsonl` and `last-seen.txt`
// are no longer state files (they belonged to the deleted webhook listener),
// and that is precisely the property being pinned: a write into the skill dir
// is an EROFS whatever the file is called. Do NOT "tidy" these to current state
// filenames. Several are load-bearing as SPELLINGS — `webhooks.jsonl` starts
// with `w` and `accounts.json` with `a`, the two initials that caught the
// openSync bug where the FLAG pattern was tested against the PATH argument.

const MUST_FIRE = [
  ['the one-line join form, written',
    "writeFileSync(join(__dirname, 'accounts.json'), data);"],
  ['new URL(name, import.meta.url) — the skill\'s OWN idiom, and the filename comes FIRST',
    "writeFileSync(new URL('accounts.json', import.meta.url), data);"],
  ['fileURLToPath(new URL(...)) — same, one wrapper deeper',
    "writeFileSync(fileURLToPath(new URL('watchers.json', import.meta.url)), data);"],
  ['a MULTI-LINE join — the old regex stopped at the newline',
    "appendFileSync(join(\n  __dirname,\n  '..',\n  'webhooks.jsonl'\n), line);"],
  ['a state file that is NOT in STATE_FILES — no registry is consulted any more',
    "writeFileSync(join(__dirname, 'sessions.json'), data);"],
  ['a dotfile the registry does not know either',
    "writeFileSync(join(__dirname, '.credentials'), token);"],
  ['double quotes',
    'writeFileSync(join(__dirname, "accounts.json"), data);'],
  ['SKILL_DIR as the base',
    "writeFileSync(join(SKILL_DIR, 'last-seen.txt'), ts);"],
  ['import.meta.dirname',
    "writeFileSync(join(import.meta.dirname, 'watchers.json'), data);"],
  ['a nested state dir, created rather than written',
    "mkdirSync(join(__dirname, '.cache'), { recursive: true });"],
  // ── shapes the literal-and-extension predicate could NOT see ──
  ['🔴 INDIRECT: bound one statement earlier, written later',
    "const p = join(__dirname, 'watchers.json');\nappendFileSync(p, line);"],
  ['🔴 INDIRECT through an accessor function',
    "const logFile = () => join(__dirname, 'webhooks.jsonl');\nappendFileSync(logFile(), line);"],
  ['🔴 a COMPUTED filename — no literal for a filename check to match',
    'writeFileSync(join(__dirname, name), data);'],
  ['🔴 WRITING the manifest back — the old READONLY_BUNDLED exemption made this invisible',
    "writeFileSync(join(__dirname, '..', 'package.json'), JSON.stringify(pkg));"],
  ['🔴 a .md file — the old WRITABLE_EXT list did not consider one writable',
    "writeFileSync(join(__dirname, '..', 'reference', 'generated.md'), body);"],
  ['🔴 the DESTINATION of a copy',
    "cpSync(tmpDir, join(__dirname, '.cache'), { recursive: true });"],
  ['🔴 chmod of a skill-dir path',
    "chmodSync(join(__dirname, 'accounts.json'), 0o600);"],
  ['🔴 openSync with a WRITE flag',
    "const fd = openSync(join(__dirname, 'webhooks.jsonl'), 'a');"],
  ['🔴 openSync WRITE of a name that could never be mistaken for a flag',
    "const fd = openSync(join(__dirname, 'schema.yaml'), 'w');"],
  ['🔴 openSync r+ — read-WRITE',
    "const fd = openSync(join(__dirname, 'schema.yaml'), 'r+');"],
  ['🔴 a TEMPLATE LITERAL path',
    'writeFileSync(`${__dirname}/accounts.json`, data);'],
  ['🔴 a template literal bound one statement earlier',
    'const p = `${__dirname}/watchers.json`;\nappendFileSync(p, line);'],
  ['🔴 a write stream',
    "const out = createWriteStream(join(__dirname, 'webhooks.jsonl'));"],
  ['🔴 deletion is a write too',
    "rmSync(join(__dirname, '.cache'), { recursive: true, force: true });"],
  // ── the emoji desync, pinned where it had its consequence ──
  //
  // 🔴 js-source.mjs split by CODE POINT and indexed by CODE UNIT, so every
  // astral character shifted the offsets after it by one. Two markers — fewer
  // than a normal header in this repo carries — moved the blanking of the middle
  // comment far enough to leave its apostrophe behind; the stray quote opened a
  // "string literal" that ran on through the write and swallowed it. Measured
  // against the tree that still had the webhook listener: 4 of 26 walked modules
  // desynced, and a genuine write planted in listen.mjs was invisible here.
  //
  // This entry FIRED red before the fix. Keep the apostrophe and both markers —
  // one marker alone shifts uniformly and strands nothing.
  ['🔴 TWO emoji markers and an apostrophe — the shape that swallowed a real write',
    "// \u{1F534} a marker, the idiom every header in this repo opens with\n" +
    "// the receiver doesn't care which host it runs on\n" +
    '// \u{1F534} a second marker\n' +
    "writeFileSync(join(__dirname, 'accounts.json'), data);"],
  ['🔴 an emoji inside the WRITTEN template path itself',
    'writeFileSync(`${__dirname}/\u{1F534}-accounts.json`, data);'],
];

// 🔴 CALIBRATED TO THE CLASS, NOT TO AN EXEMPTION LIST — and not to the bug
// either. The first four entries are CONSTRUCTED bundled reads, not defects
// anything reported in this repo (none of those four files is read by any
// module here; over the real tree both scanners find zero). They are here
// because each is a plain READ of a shipped file, and the message the old
// predicate printed for that shape ("it will EROFS once deployed read-only")
// was false. None of them is exempt by name any more — they are quiet because
// nothing writes.
//
// 🔴 The openSync read cases below are deliberately basenames that START with
// `w` and `a` or CONTAIN `+`. The single shipped control was `schema.yaml`,
// whose `s` is the one initial the flag pattern cannot match, so it passed while
// `openSync(join(__dirname,'accounts.json'),'r')` fired on the FILENAME. A
// control chosen to avoid the bug measures nothing.
const MUST_NOT_FIRE = [
  ['READ: bundled data next to the module',
    "const fields = JSON.parse(readFileSync(join(__dirname, '..', 'data', 'custom-fields.json'), 'utf8'));"],
  ['READ: a bundled schema with a "writable" extension',
    "const schema = readFileSync(join(__dirname, 'schema.yaml'), 'utf8');"],
  ['READ: the skill\'s own idiom, new URL(..., import.meta.url)',
    "const prompt = readFileSync(new URL('prompt.txt', import.meta.url), 'utf8');"],
  ['READ: a bundled dotfile',
    "const ignore = readFileSync(join(__dirname, '..', '.gitignore'), 'utf8');"],
  ['READ: the manifest (no longer exempt BY NAME — exempt because it is a read)',
    "const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8'));"],
  ['READ: a bundled reference doc',
    "const help = readFileSync(join(__dirname, '..', 'reference', 'setup.md'), 'utf8');"],
  ['READ: listing a bundled directory',
    "const docs = readdirSync(join(__dirname, '..', 'reference'));"],
  ['READ: copying bundled data OUT of the skill dir — the SOURCE argument',
    "cpSync(join(__dirname, '..', 'templates'), outDir, { recursive: true });"],
  ['READ: openSync with a read flag',
    "const fd = openSync(join(__dirname, 'schema.yaml'), 'r');"],
  ['READ: openSync of a file whose name STARTS WITH W (not a write flag)',
    "const fd = openSync(join(__dirname, 'webhooks.jsonl'), 'r');"],
  ['READ: openSync of a file whose name STARTS WITH A (not a write flag)',
    "const fd = openSync(join(__dirname, 'accounts.json'), 'r');"],
  ['READ: openSync of a file whose name CONTAINS + (not a write flag)',
    "const fd = openSync(join(__dirname, 'c++notes.txt'), 'r');"],
  ['READ: openSync with the flags omitted entirely (defaults to r)',
    "const fd = openSync(join(__dirname, 'webhooks.jsonl'));"],
  ['the __dirname preamble every module writes',
    "const __dirname = dirname(fileURLToPath(import.meta.url));"],
  ['the run-as-main guard (this is lib/jwt.mjs:338 verbatim)',
    "const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));"],
  ['deriving the skill dir itself (lib/paths.mjs:43 verbatim)',
    "export const SKILL_DIR = resolve(__dirname, '..');"],
  ['the CORRECT code: a write through the accessor, with a location token nearby',
    "const __dirname = dirname(fileURLToPath(import.meta.url));\nwriteFileSync(accountsPath(), data);"],
  ['a write to a path derived from the STATE dir, not the module',
    "writeFileSync(join(stateBaseDir(), 'accounts.json'), data);"],
  ['a scratch dir under tmpdir()',
    "const dir = mkdtempSync(join(tmpdir(), 'clickup-'));\nwriteFileSync(join(dir, 'accounts.json'), data);"],
  ['the hazard described in a COMMENT — prose is not code',
    "// never write writeFileSync(join(__dirname, 'accounts.json'), d) — use the accessor\nconst p = accountsPath();"],
  ['the hazard commented OUT — still not code',
    "/* writeFileSync(join(__dirname, 'accounts.json'), d); */\nconst p = accountsPath();"],
  ['an import specifier that merely ends in .js',
    "import x from './helper.js';\nconst d = dirname(fileURLToPath(import.meta.url));"],
  // The other direction of the emoji fix: blanking that is correct must not
  // start UNDER-blanking either, or the prose above becomes a reported defect.
  ['the hazard described in a comment that also carries emoji markers',
    "// \u{1F534} never write writeFileSync(join(__dirname, 'accounts.json'), d)\n" +
    "// \u{1F534} — use the accessor; the receiver doesn't care\nconst p = accountsPath();"],
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
  assert.ok(MUST_FIRE.length >= 24,
    `only ${MUST_FIRE.length} positive control(s) — the shapes this scanner is trusted ` +
      'to catch are exactly the ones listed here');
  assert.ok(MUST_NOT_FIRE.length >= 22,
    `only ${MUST_NOT_FIRE.length} negative control(s) — false positives are what get a ` +
      'guard deleted, so they need as much pinning as the true ones');
});

// ── openSync: the FLAGS decide, never the filename ────────────────────────
//
// Its own test rather than two more table rows, because the failure it pins is
// specific and was invisible: the flag pattern was tested against every literal
// in the call, so argument 0 — the PATH — voted. Both directions, over the SAME
// basenames, is the only arrangement that can tell "reads the flags" apart from
// "reads the name".

const OPEN_FLAG_CASES = [
  // basename,           flags,      must fire
  ['webhooks.jsonl',     "'r'",      false],  // w-initial name, READ
  ['accounts.json',      "'r'",      false],  // a-initial name, READ
  ['c++notes.txt',       "'r'",      false],  // + in the name, READ
  ['schema.yaml',        "'r'",      false],
  ['webhooks.jsonl',     "'a'",      true],
  ['accounts.json',      "'w'",      true],
  ['schema.yaml',        "'w'",      true],   // innocuous name, WRITE
  ['schema.yaml',        "'r+'",     true],   // read-write
  ['schema.yaml',        "'wx'",     true],
  ['webhooks.jsonl',     null,       false],  // flags omitted → defaults to 'r'
];

test('🔴 openSync is decided by its FLAGS argument, never by the filename', () => {
  // Both directions have to be present, or this passes by having only one.
  assert.ok(OPEN_FLAG_CASES.some(([, , fire]) => fire) && OPEN_FLAG_CASES.some(([, , fire]) => !fire),
    'the openSync table lost one of its two directions');
  assert.ok(OPEN_FLAG_CASES.filter(([name]) => /^[wa]|\+/.test(name)).length >= 3,
    'the read cases no longer include basenames that the flag pattern itself would match ' +
      '(w-initial, a-initial, +-containing) — that is the bug this test exists for');
  const wrong = [];
  for (const [name, flags, shouldFire] of OPEN_FLAG_CASES) {
    const src = `const fd = openSync(join(__dirname, '${name}')${flags ? `, ${flags}` : ''});`;
    const fired = findOpenCodedStatePaths(src).length > 0;
    if (fired !== shouldFire) {
      wrong.push(`openSync('${name}'${flags ? `, ${flags}` : ''}) ${fired ? 'FIRED' : 'was SILENT'}` +
        ` — expected ${shouldFire ? 'a hit' : 'silence'}`);
    }
  }
  assert.equal(wrong.length, 0,
    'the openSync branch is not reading the flags argument. If the PATH argument votes, the ' +
      'basename decides: a plain read of `accounts.json` reports an EROFS that will never ' +
      'happen (and the only shipped control used an `s`-initial name, which cannot trip ' +
      `it):\n        ${wrong.join('\n        ')}`);
});

// ── READ vs WRITE, stated as its own property and proved BOTH ways ────────
//
// The control tables above would still be satisfied by a scanner that fired on
// everything containing `writeFileSync` somewhere. These two say the distinction
// itself is what is being measured: the SAME path expression, in the same
// module, read and then written.

// CONSTRUCTED shapes — no module in this skill reads any of these four files.
// They are the bundled-read CLASS written four ways, not observed findings.
const READ_ONLY_SHAPES = [
  ["join(__dirname, '..', 'data', 'custom-fields.json')", 'readFileSync(EXPR, \'utf8\')'],
  ["join(__dirname, 'schema.yaml')", 'readFileSync(EXPR, \'utf8\')'],
  ["new URL('prompt.txt', import.meta.url)", 'readFileSync(EXPR, \'utf8\')'],
  ["join(__dirname, '..', '.gitignore')", 'readFileSync(EXPR, \'utf8\')'],
];

test('🔴 READ vs WRITE: the four bundled-read shapes are SILENT', () => {
  const wrong = [];
  for (const [expr, template] of READ_ONLY_SHAPES) {
    const src = `const x = ${template.replace('EXPR', expr)};`;
    const hits = findOpenCodedStatePaths(src);
    if (hits.length) wrong.push(`${expr}  ->  ${hits.join(', ')}`);
  }
  assert.equal(wrong.length, 0,
    'the seam scanner flagged a plain READ of a file shipped inside the skill dir. Reading ' +
      'bundled data off __dirname is legal on a read-only deploy, and the message the guard ' +
      'prints ("it will EROFS once deployed read-only") is FALSE for a read. A guard that ' +
      `cries wolf gets deleted:\n        ${wrong.join('\n        ')}`);
});

test('🔴 READ vs WRITE: the SAME expressions WRITTEN all fire', () => {
  // The other half, over the identical expressions: this is what proves the
  // scanner is measuring the write context and not just the four templates.
  const missed = [];
  for (const [expr] of READ_ONLY_SHAPES) {
    const src = `writeFileSync(${expr}, data);`;
    if (findOpenCodedStatePaths(src).length === 0) missed.push(expr);
  }
  assert.equal(missed.length, 0,
    'a genuine open-coded state WRITE off the module\'s own location did NOT fire — the ' +
      'read/write check was widened into a hole. These EROFS on a read-only deploy:\n        ' +
      missed.join('\n        '));
});

test('🔴 READ vs WRITE: a genuine write via EACH location idiom fires, direct and indirect', () => {
  const cases = [
    ['__dirname + join, inline',
      "writeFileSync(join(__dirname, 'sessions.json'), data);"],
    ['__dirname + join, bound then written',
      "const p = join(__dirname, 'sessions.json');\nwriteFileSync(p, data);"],
    ['new URL(..., import.meta.url), inline',
      "writeFileSync(new URL('sessions.json', import.meta.url), data);"],
    ['new URL(..., import.meta.url), bound then written',
      "const p = new URL('sessions.json', import.meta.url);\nwriteFileSync(p, data);"],
  ];
  const missed = cases.filter(([, src]) => findOpenCodedStatePaths(src).length === 0)
    .map(([label]) => label);
  assert.equal(missed.length, 0,
    'the scanner missed a genuine state WRITE derived from the module\'s own location — ' +
      `this is the property the whole file exists for:\n        ${missed.join('\n        ')}`);
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
