/**
 * Minimal JavaScript source scanning, shared by the two STRUCTURAL guards in
 * this directory (the state-path seam walk and the help-coverage extractor).
 *
 * WHY THIS EXISTS
 * ---------------
 * Both guards were SPELLED rather than structural, and both failed in the same
 * way: they described one way of writing the thing they were looking for, so
 * the hazard in any other shape walked straight past.
 *
 *   * the seam guard matched `__dirname` + a state filename on ONE LINE. The
 *     skill's own idiom — `new URL('accounts.json', import.meta.url)` — passed
 *     clean, as did every multi-line `join(\n  __dirname,\n  'x.json'\n)`.
 *   * the help extractor matched `^ {6}case 'x':` — six spaces, single quotes.
 *     `case "x":` and an eight-space `case` were dispatchable, undocumented,
 *     and green.
 *
 * Neither can be fixed by a longer regex over raw text, because both need to
 * know what is CODE and what is a comment or a string. So: one small scanner,
 * used by both, with its own controls in js-source.test.mjs — which for one
 * round was a claim about a file that did not exist, while both guards rested
 * on this module's behaviour. `blankStrings` blanking `${…}` (below) is exactly
 * the kind of thing direct controls catch and a downstream guard does not: it
 * showed up as a state-path scanner that was silent on a template literal.
 *
 * It is not a parser. It tracks exactly what these guards need — string,
 * template, regex and comment context — and nothing else. Known limits, all
 * exercised in js-source.test.mjs:
 *
 *   * a nested quote INSIDE a `${…}` substitution (`` `${x['a`b']}` ``) ends the
 *     template early — the literal scan does not recurse;
 *   * `blankComments` does not know about JSX or about a regex whose preceding
 *     token is a keyword (`return /x/`), because neither occurs here.
 */

const REGEX_PREFIX = new Set([
  '(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '-', '*',
  '%', '~', '^', '<', '>', '\n',
]);

/**
 * Replace every comment with spaces, preserving byte offsets and line
 * structure so an index into the result still points at the same place.
 *
 * String and template contents are left ALONE — `'https://x'` is not a comment,
 * and a guard that thinks it is reports defects that are not there.
 */
export function blankComments(src) {
  const out = Array.from(src);
  let i = 0;
  let prev = '\n'; // last significant code character
  const n = src.length;
  const blank = (from, to) => {
    for (let k = from; k < to && k < n; k++) if (out[k] !== '\n') out[k] = ' ';
  };
  while (i < n) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') {
      let j = i;
      while (j < n && src[j] !== '\n') j++;
      blank(i, j);
      i = j;
      continue;
    }
    if (c === '/' && src[i + 1] === '*') {
      let j = i + 2;
      while (j < n && !(src[j] === '*' && src[j + 1] === '/')) j++;
      blank(i, Math.min(j + 2, n));
      i = j + 2;
      continue;
    }
    if (c === '/' && REGEX_PREFIX.has(prev)) {
      // A regex literal. Skip it wholesale: `/[.]/g` and friends contain
      // characters that would otherwise be read as quotes or comments.
      let j = i + 1;
      let inClass = false;
      while (j < n) {
        const d = src[j];
        if (d === '\\') { j += 2; continue; }
        if (d === '[') inClass = true;
        else if (d === ']') inClass = false;
        else if (d === '/' && !inClass) break;
        else if (d === '\n') break;
        j++;
      }
      i = j + 1;
      prev = '/';
      continue;
    }
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1;
      while (j < n) {
        if (src[j] === '\\') { j += 2; continue; }
        if (src[j] === c) break;
        j++;
      }
      i = j + 1;
      prev = c;
      continue;
    }
    if (!/\s/.test(c)) prev = c;
    else if (c === '\n') prev = '\n';
    i++;
  }
  return out.join('');
}

/**
 * Every string literal in `code`, as {value, start, end}.
 * Template literals with `${}` are reported by their raw text — good enough:
 * these guards only care whether a *literal filename* appears.
 */
export function stringLiterals(code) {
  const out = [];
  let i = 0;
  const n = code.length;
  while (i < n) {
    const c = code[i];
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1;
      while (j < n) {
        if (code[j] === '\\') { j += 2; continue; }
        if (code[j] === c) break;
        j++;
      }
      out.push({ value: code.slice(i + 1, j), start: i, end: Math.min(j + 1, n) });
      i = j + 1;
      continue;
    }
    i++;
  }
  return out;
}

/**
 * The `${ … }` substitutions inside ONE template literal, as [start, end)
 * offsets into `code` covering the delimiters as well.
 *
 * Brace-matched, so `${join(a, {b: 1})}` is one range. Not quote-aware inside
 * the substitution — see the module header's known limits.
 */
export function substitutionRanges(code, lit) {
  if (code[lit.start] !== '`') return [];
  const out = [];
  for (let i = lit.start + 1; i < lit.end - 1; i++) {
    if (code[i] === '\\') { i += 1; continue; }
    if (code[i] !== '$' || code[i + 1] !== '{') continue;
    let depth = 0;
    let j = i + 1;
    for (; j < lit.end - 1; j++) {
      if (code[j] === '{') depth++;
      else if (code[j] === '}') { depth--; if (depth === 0) break; }
    }
    const end = Math.min(j + 1, lit.end - 1);
    out.push([i, end]);
    i = end - 1;
  }
  return out;
}

/**
 * Blank the CONTENTS of every string/template literal (quotes kept), preserving
 * offsets. Brace-depth counting needs this: a lone `'{'` in a string would
 * otherwise skew the depth and silently move where "top level" is.
 *
 * 🔴 `keepSubstitutions` leaves the CODE inside a template's `${ … }` intact.
 * The default blanks it, and that is a hole for any guard that looks for an
 * identifier: `` writeFileSync(`${__dirname}/accounts.json`, d) `` — a real
 * write idiom in this skill — became a token-free string and the state-path
 * scanner was silent on it. A substitution's braces are balanced by
 * construction, so keeping them does not disturb the depth counting this
 * function exists to protect; the literal TEXT around them is still blanked, so
 * a `{` or a comma in the text cannot skew it either.
 *
 * @param {string} code
 * @param {{keepSubstitutions?: boolean}} [opts]
 */
export function blankStrings(code, { keepSubstitutions = false } = {}) {
  const out = Array.from(code);
  for (const lit of stringLiterals(code)) {
    const keep = keepSubstitutions ? substitutionRanges(code, lit) : [];
    for (let k = lit.start + 1; k < lit.end - 1; k++) {
      if (out[k] === '\n') continue;
      if (keep.some(([s, e]) => k >= s && k < e)) continue;
      out[k] = ' ';
    }
  }
  return out.join('');
}

/**
 * The `{ … }` block that follows `code.indexOf(header)`, brace-matched.
 *
 * Used to bound `switch (command) { … }` so a case label can be recognised by
 * its POSITION in that switch rather than by its indentation.
 * @returns {{start: number, end: number}|null}
 */
export function balancedBlockAfter(code, header) {
  const at = code.indexOf(header);
  if (at === -1) return null;
  const bare = blankStrings(code);
  const open = bare.indexOf('{', at);
  if (open === -1) return null;
  let depth = 0;
  for (let i = open; i < bare.length; i++) {
    if (bare[i] === '{') depth++;
    else if (bare[i] === '}') {
      depth--;
      if (depth === 0) return { start: open, end: i + 1 };
    }
  }
  return null;
}

/**
 * `case '<name>':` / `case "<name>":` labels at the TOP level of a block.
 *
 * Depth-based, so it is immune to indentation AND to quote style, and — unlike
 * a bare `case` regex over the rest of the file — it does not sweep in the
 * labels of a NESTED switch, which would inflate the "dispatchable" set with
 * things no command dispatch reaches.
 */
export function topLevelCaseLabels(code, block) {
  const out = new Set();
  let depth = 0;
  const re = /case\s*(['"])([a-z][a-z0-9-]*)\1\s*:/g;
  const marks = [];
  let m;
  // 🔴 The label NAME has to be read from the raw text (blanking strings blanks
  // the name too), so the "is this code?" question is asked separately: a match
  // whose `case` keyword sits INSIDE a string literal is text, not a label.
  // Without this, `usage("case 'restore':")` in a help string invented a
  // dispatchable command and the coverage guard demanded docs for it.
  const lits = stringLiterals(code);
  const insideLiteral = (i) => lits.some((l) => i > l.start && i < l.end - 1);
  while ((m = re.exec(code.slice(block.start, block.end))) !== null) {
    const index = block.start + m.index;
    if (insideLiteral(index)) continue;
    marks.push({ index, name: m[2] });
  }
  const bare = blankStrings(code);
  let mi = 0;
  for (let i = block.start; i < block.end && mi < marks.length; i++) {
    while (mi < marks.length && marks[mi].index === i) {
      if (depth === 1) out.add(marks[mi].name);
      mi++;
    }
    if (bare[i] === '{') depth++;
    else if (bare[i] === '}') depth--;
  }
  return out;
}
