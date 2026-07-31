// Source hygiene for the extension: every file must diff and review as TEXT.
//
// This exists because route_core.js -- the 11 KB module holding the suggest()
// ladder and the cached-fallback matcher -- shipped with three literal NUL
// bytes used as a Map key separator. git classified it as binary, so
// `gh pr diff` printed "Binary files differ" and the module was merged without
// anyone being able to read the diff. The fix is backslash-u escapes;
// this test is what stops it coming back.
//
// Run: nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const EXT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "extension");
const SOURCES = readdirSync(EXT_DIR).filter((f) => f.endsWith(".js"));

test("there is something to check", () => {
  assert.ok(SOURCES.length >= 6, `found only ${SOURCES.length} sources`);
});

test("no extension source contains a NUL or any other C0 control byte", () => {
  for (const name of SOURCES) {
    const raw = readFileSync(join(EXT_DIR, name));
    for (let i = 0; i < raw.length; i += 1) {
      const b = raw[i];
      const ok = b >= 0x20 || b === 0x09 || b === 0x0a || b === 0x0d;
      assert.ok(ok, `${name}: control byte 0x${b.toString(16)} at offset ${i}`);
      assert.notEqual(b, 0x7f, `${name}: DEL at offset ${i}`);
    }
  }
});

test("every extension source is plain ASCII", () => {
  // Not cosmetic: the same convention is what keeps hostile-looking literals
  // (bidi overrides, zero-width joiners) impossible to hide in this code.
  for (const name of SOURCES) {
    const raw = readFileSync(join(EXT_DIR, name));
    for (let i = 0; i < raw.length; i += 1) {
      assert.ok(raw[i] < 0x80,
        `${name}: non-ASCII byte 0x${raw[i].toString(16)} at offset ${i}`);
    }
  }
});
