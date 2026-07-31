// Loader for the SHARED hostile-input table.
//
// tests/conftest.py loads the same JSON with the same expansion rules, so
// safety.py and extension/sanitize.js are asserted against ONE table. The two
// used to be hand-copied literal lists in the two test files; differential
// fuzzing then found 991 inputs the implementations disagreed on that neither
// list covered.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

function expand(value) {
  if (Array.isArray(value)) return value.map(expand);
  if (value && typeof value === "object" && "repeat" in value) {
    return String(value.prefix ?? "")
      + String(value.repeat).repeat(Number(value.count));
  }
  return value;
}

export function loadNameCases() {
  const raw = JSON.parse(
    readFileSync(join(HERE, "fixtures", "name_cases.json"), "utf8"));
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    if (key.startsWith("_")) continue;
    out[key] = expand(value);
  }
  return out;
}
