/**
 * Where the ClickUp skill keeps its MUTABLE state.
 *
 * The skill directory is CODE. It is deployed read-only (home-manager
 * `recursive = true` puts it behind /nix/store symlinks), so anything written
 * relative to `__dirname` fails with EROFS. It is also the wrong place for a
 * live `pk_` API token, which should not sit next to source.
 *
 * So every mutable path resolves under:
 *
 *   $XDG_STATE_HOME/clickup      when XDG_STATE_HOME is set and non-empty
 *   ~/.local/state/clickup       otherwise
 *
 * This module is the ONLY place that computation lives. Import an accessor;
 * do not re-derive a state path from `__dirname` anywhere else — the seam
 * check in test/state-paths.test.mjs fails the build if you do.
 *
 * The accessors are PURE: they resolve a path and touch nothing. Anything that
 * actually reads or writes state must call `ensureStateDir()` first — that is
 * what creates the directory (0700) and performs the one-time migration of a
 * pre-existing skill-directory state file.
 */

import {
  existsSync,
  mkdirSync,
  copyFileSync,
  cpSync,
  chmodSync,
  statSync,
  readdirSync,
} from 'fs';
import { homedir } from 'os';
import { dirname, join, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * The skill's own install directory — code and bundled read-only docs.
 * Treat as READ-ONLY. It is the migration SOURCE, never a write target.
 */
export const SKILL_DIR = resolve(__dirname, '..');

/**
 * Every state file the skill owns, as a flat name under the state dir.
 * Keep in sync with the accessors below; the seam test reads this list.
 */
export const STATE_FILES = [
  'accounts.json',
  '.env',
  'watchers.json',
  'webhook-url.txt',
  'webhooks.jsonl',
  'webhook-latest.json',
  'last-seen.txt',
];

/** State subdirectories (migrated wholesale). */
export const STATE_DIRS = ['.cache'];

/**
 * Names that hold credentials. These are forced to 0600 on migration
 * regardless of what the legacy copy's mode happened to be.
 *
 * watchers.json is on the list because each entry carries the webhook's
 * `secret` — the HMAC key ClickUp signs deliveries with.
 */
export const SECRET_FILES = new Set(['accounts.json', '.env', 'watchers.json']);

/** Files inside .cache hold JWTs, so the whole directory is secret. */
const SECRET_DIRS = new Set(['.cache']);

// ── Base directory ────────────────────────────────────────────────────────

/**
 * Resolve the state base directory. PURE — creates nothing.
 *
 * XDG_STATE_HOME wins when it is set to a non-empty value; an unset OR empty
 * value falls back to ~/.local/state, per the XDG base-directory spec (an
 * empty variable is explicitly "unset" there). Both branches are pinned by
 * test/state-paths.test.mjs — dropping either one is a silent single-host bug.
 */
export function stateBaseDir() {
  const xdg = process.env.XDG_STATE_HOME;
  const base = xdg && xdg.trim() !== '' ? xdg : join(homedir(), '.local', 'state');
  return resolve(base, 'clickup');
}

/** Resolve a path inside the state dir. PURE — creates nothing. */
export function statePath(...parts) {
  return join(stateBaseDir(), ...parts);
}

// ── Named accessors (pure) ────────────────────────────────────────────────

export const accountsPath = () => statePath('accounts.json');
export const envPath = () => statePath('.env');
export const cacheDir = () => statePath('.cache');
export const watchersFile = () => statePath('watchers.json');
export const webhookUrlFile = () => statePath('webhook-url.txt');
export const webhookLogFile = () => statePath('webhooks.jsonl');
export const webhookLatestFile = () => statePath('webhook-latest.json');
export const lastSeenFile = () => statePath('last-seen.txt');

// ── Migration ─────────────────────────────────────────────────────────────

/**
 * Copy pre-existing state out of a legacy directory (the skill dir) into the
 * state dir, ONCE, for each item that does not already exist at the target.
 *
 * The legacy copies are deliberately LEFT IN PLACE: another host may still be
 * running the old code that reads them, and a delete here is unrecoverable —
 * `accounts.json` holds the only copy of a live API token.
 *
 * @param {string} legacyDir  directory to migrate FROM
 * @param {string} stateDir   directory to migrate TO
 * @param {{log?: boolean}} [opts]
 * @returns {string[]} names actually migrated (empty on a no-op run)
 */
export function migrateLegacyState(legacyDir, stateDir, opts = {}) {
  const { log = true } = opts;
  const migrated = [];
  if (!legacyDir || resolve(legacyDir) === resolve(stateDir)) return migrated;
  if (!existsSync(legacyDir)) return migrated;

  for (const name of STATE_FILES) {
    const src = join(legacyDir, name);
    const dest = join(stateDir, name);
    if (existsSync(dest) || !existsSync(src)) continue;
    if (!statSync(src).isFile()) continue;
    mkdirSync(stateDir, { recursive: true });
    try { chmodSync(stateDir, 0o700); } catch { /* best effort */ }
    copyFileSync(src, dest);
    const mode = SECRET_FILES.has(name) ? 0o600 : statSync(src).mode & 0o777;
    try { chmodSync(dest, mode); } catch { /* Windows may not support */ }
    migrated.push(name);
  }

  for (const name of STATE_DIRS) {
    const src = join(legacyDir, name);
    const dest = join(stateDir, name);
    if (existsSync(dest) || !existsSync(src)) continue;
    if (!statSync(src).isDirectory()) continue;
    mkdirSync(stateDir, { recursive: true });
    try { chmodSync(stateDir, 0o700); } catch { /* best effort */ }
    cpSync(src, dest, { recursive: true });
    if (SECRET_DIRS.has(name)) {
      try { chmodSync(dest, 0o700); } catch { /* best effort */ }
      for (const entry of readdirSync(dest, { withFileTypes: true })) {
        if (!entry.isFile()) continue;
        try { chmodSync(join(dest, entry.name), 0o600); } catch { /* best effort */ }
      }
    }
    migrated.push(name + '/');
  }

  if (migrated.length && log) {
    // Names only — NEVER the contents. accounts.json holds a live token.
    console.error(
      `[clickup] migrated state (${migrated.join(', ')}) from ${legacyDir} to ${stateDir}; ` +
        'the legacy copies were left in place.'
    );
  }
  return migrated;
}

// ── Lazy initialisation ───────────────────────────────────────────────────

/** Dirs already prepared in THIS process, keyed by resolved path. */
const _prepared = new Set();

/**
 * Create the state dir (0700) and run the one-time legacy migration.
 * Idempotent, and cheap after the first call per resolved directory.
 * Call this before any state read or write.
 *
 * @returns {string} the state base dir
 */
export function ensureStateDir() {
  const dir = stateBaseDir();
  if (_prepared.has(dir)) return dir;
  mkdirSync(dir, { recursive: true });
  try { chmodSync(dir, 0o700); } catch { /* best effort */ }
  migrateLegacyState(SKILL_DIR, dir);
  _prepared.add(dir);
  return dir;
}
