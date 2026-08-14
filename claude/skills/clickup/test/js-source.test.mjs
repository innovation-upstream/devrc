#!/usr/bin/env node

/**
 * DIRECT controls for test/js-source.mjs (hermetic — pure string functions).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * js-source.mjs said, in its header, "with its own controls in
 * js-source.test.mjs" — and that file did not exist. Meanwhile it is the
 * SUBSTRATE under both structural guards in this directory: the state-path seam
 * scanner (comment blanking, string blanking, brace/argument depth counting, and
 * every literal it reads) and the help-coverage extractor (its switch-block
 * bounds and its case labels). A defect here is a silent hole in both, attributed
 * to neither.
 *
 * It was not hypothetical. `blankStrings` blanked the CONTENTS of a template
 * literal including its `${ … }` substitutions, so
 * `` writeFileSync(`${__dirname}/accounts.json`, d) `` — a real write idiom in
 * this skill — reached the seam scanner as a token-free string and passed
 * clean. A guard downstream cannot see that; a direct control can, and does
 * below.
 *
 * Usage:
 *   node --test test/js-source.test.mjs
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  blankComments,
  blankStrings,
  stringLiterals,
  substitutionRanges,
  balancedBlockAfter,
  topLevelCaseLabels,
} from './js-source.mjs';

/** Code units that are half of a surrogate pair with no partner. */
function loneSurrogates(s) {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) { i++; continue; }
      n++;
    } else if (c >= 0xdc00 && c <= 0xdfff) n++;
  }
  return n;
}

/**
 * Every one of these functions promises OFFSET PRESERVATION — an index into the
 * result points at the same character it did in the input. Both guards rely on
 * it (they scan `bare` and then read `literals` computed from another copy), and
 * nothing else in the suite would notice it breaking.
 *
 * 🔴 LENGTH IS IN UTF-16 CODE UNITS, and that is the whole point. This assertion
 * was correct and shipped for the whole life of the module while the module was
 * broken, because it was never fed a single non-ASCII character: `Array.from`
 * splits by CODE POINT, so one emoji made the output one unit short and every
 * offset after it wrong. Every fixture below that carries an astral character is
 * here to keep that from being possible again — do not "simplify" them back to
 * ASCII.
 */
function assertSameShape(before, after, what) {
  assert.equal(after.length, before.length, `${what}: length changed — offsets no longer line up`);
  const lines = (s) => s.split('\n').length;
  assert.equal(lines(after), lines(before), `${what}: line count changed`);
  for (let i = 0; i < before.length; i++) {
    if (before[i] === '\n') {
      assert.equal(after[i], '\n', `${what}: newline at ${i} was overwritten`);
    }
  }
  // Blanking must take a surrogate PAIR or neither half: half of one leaves a
  // lone surrogate, which is a different character from anything in the input.
  assert.equal(loneSurrogates(after), loneSurrogates(before),
    `${what}: a surrogate pair was half-blanked, leaving a lone surrogate`);
}

// ── blankComments ─────────────────────────────────────────────────────────

describe('blankComments', () => {
  test('a line comment is blanked, the code around it is not', () => {
    const src = "const a = 1; // writeFileSync(join(__dirname, 'x.json'), d)\nconst b = 2;";
    const out = blankComments(src);
    assertSameShape(src, out, 'blankComments');
    assert.ok(out.includes('const a = 1;'), 'the code before the comment was destroyed');
    assert.ok(out.includes('const b = 2;'), 'the code after the newline was destroyed');
    assert.ok(!/writeFileSync/.test(out),
      'a commented-out call survived. The seam guard would report prose as a defect — and, ' +
        'worse, a real call could be hidden from it by commenting the lines around it.');
  });

  test('a block comment is blanked across lines, preserving the newlines', () => {
    const src = 'const a = 1;\n/* writeFileSync(p, d);\n   more prose */\nconst b = 2;';
    const out = blankComments(src);
    assertSameShape(src, out, 'blankComments');
    assert.ok(!/writeFileSync/.test(out), 'a block-commented call survived');
    assert.ok(out.includes('const b = 2;'));
  });

  test('🔴 a comment MARKER INSIDE A STRING is not a comment', () => {
    // The control that matters in the other direction: over-blanking silently
    // deletes real code from every guard downstream.
    const src = "const u = 'https://example.com/token';\nconst p = accountsPath();";
    const out = blankComments(src);
    assert.ok(out.includes('example.com/token'),
      'the // inside a URL string was treated as a comment, so everything after it on the ' +
        'line was erased — including, in a real module, the call being guarded');
    assert.ok(out.includes('accountsPath()'));
  });

  test('a regex literal containing a quote does not open a string', () => {
    const src = "const re = /['\"]/g;\nwriteFileSync(p, d);";
    const out = blankComments(src);
    assert.ok(out.includes('writeFileSync(p, d);'),
      'a quote inside a regex literal was read as opening a string, which swallows the rest ' +
        'of the file');
  });

  test('a division is not mistaken for a regex', () => {
    const src = 'const half = total / 2;\nconst other = count / 4;\nwriteFileSync(p, d);';
    const out = blankComments(src);
    assert.ok(out.includes('writeFileSync(p, d);'),
      'two divisions were read as one regex literal, erasing the code between them');
  });

  test('an escaped quote does not end a string early', () => {
    const src = "const s = 'it\\'s fine'; writeFileSync(p, d);";
    const out = blankComments(src);
    assert.ok(out.includes('writeFileSync(p, d);'));
  });
});

// ── astral characters: the code-point / code-unit desync ──────────────────
//
// 🔴 THE FIXTURES BELOW ARE THE ONES THAT WERE MISSING, AND THAT IS THE FINDING.
// Every assertion this file already made was the right assertion; none of them
// was ever handed a character outside the BMP, so `Array.from(src)` — a split by
// CODE POINT, indexed with UTF-16 CODE-UNIT offsets — sat here green:
//
//     blankComments("// 🔴 a note\nconst x = 1;")   25 in -> 24 out
//
// `🔴` opens nearly every source header in this repo. Measured over the tree as
// it stood when the webhook listener still existed, 4 of the 26 walked modules
// desynced, and a genuine `writeFileSync(join(__dirname,'accounts.json'), d)`
// planted inside `listen.mjs` was INVISIBLE to the seam scanner. Two markers in
// one file is enough — see the seam-level regression in state-paths.test.mjs.

const RED = '\u{1F534}'; // U+1F534, astral: TWO code units, ONE code point
const CLEF = '\u{1D11E}'; // U+1D11E, astral and NOT an emoji

describe('astral characters', () => {
  test('🔴 an emoji in a LINE comment does not shift every later offset', () => {
    const src = `// ${RED} a note\nwriteFileSync(join(__dirname, 'accounts.json'), d);`;
    const out = blankComments(src);
    assertSameShape(src, out, 'blankComments');
    assert.ok(!/a note/.test(out), 'the comment was not blanked');
    assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'),
      'the code after the comment moved. Every guard downstream indexes this text with ' +
        'offsets taken from the ORIGINAL source, so a shift here silently points them at ' +
        'the wrong characters — that is how a real write became invisible to the seam scanner');
  });

  test('🔴 an emoji in a BLOCK comment does not shift every later offset', () => {
    const src = `/* ${RED} prose\n   ${RED} more */\nwriteFileSync(p, d);`;
    const out = blankComments(src);
    assertSameShape(src, out, 'blankComments');
    assert.ok(!/prose/.test(out) && !/more/.test(out), 'the block comment was not blanked');
    assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'));
  });

  test('🔴 an emoji in a STRING leaves the code right after it intact', () => {
    // The shape the seam scanner meets in real modules: a label with a marker in
    // it, and a genuine state write on the very next statement.
    const src = `const label = '${RED} blocked'; writeFileSync(join(__dirname, 'accounts.json'), d);`;
    const noComments = blankComments(src);
    assertSameShape(src, noComments, 'blankComments');
    const bare = blankStrings(noComments, { keepSubstitutions: true });
    assertSameShape(src, bare, 'blankStrings');
    assert.ok(bare.includes("writeFileSync(join(__dirname, '"),
      'the write following an emoji-bearing string was mangled, so the call the guard ' +
        'exists to find is no longer spelled like a call');
    assert.equal(bare.indexOf('__dirname'), src.indexOf('__dirname'),
      'the location token moved — the argument-span arithmetic reads the wrong characters');
  });

  test('an emoji in a DOUBLE-quoted string is blanked, offsets kept', () => {
    const src = `const s = "${RED}"; writeFileSync(p, d);`;
    const out = blankStrings(src);
    assertSameShape(src, out, 'blankStrings');
    assert.ok(!out.includes(RED), 'the emoji survived inside a blanked string');
    assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'));
  });

  test('an emoji in a TEMPLATE literal is blanked, offsets kept, both keepSubstitutions ways', () => {
    const src = `const p = \`${RED}/\${__dirname}/accounts.json\`; writeFileSync(p, d);`;
    for (const keep of [false, true]) {
      const out = blankStrings(src, { keepSubstitutions: keep });
      assertSameShape(src, out, `blankStrings(keepSubstitutions:${keep})`);
      assert.ok(!out.includes(RED), `the emoji survived (keepSubstitutions:${keep})`);
      assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'),
        `the code after the template moved (keepSubstitutions:${keep})`);
    }
    assert.equal(blankStrings(src, { keepSubstitutions: true }).indexOf('__dirname'),
      src.indexOf('__dirname'),
      'the substitution was kept but at the wrong offset, which is worse than dropping it');
  });

  test('an emoji ADJACENT to a comment or string delimiter', () => {
    // No separating space: the delimiter and the astral pair touch, which is
    // where an off-by-one in the blanking bounds shows up.
    for (const src of [`//${RED}\nwriteFileSync(p, d);`, `const s = '${RED}';writeFileSync(p, d);`]) {
      const out = blankStrings(blankComments(src));
      assertSameShape(src, out, 'blankComments+blankStrings');
      assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'), src);
    }
  });

  test('a non-BMP character that is NOT an emoji desyncs identically', () => {
    // The hazard is the astral PLANE, not emoji. `𝄞` is the control that keeps
    // a future "strip emoji" workaround from reading as a fix.
    const src = `// ${CLEF} a clef\nwriteFileSync(join(__dirname, 'accounts.json'), d);`;
    const out = blankComments(src);
    assertSameShape(src, out, 'blankComments');
    assert.equal(out.indexOf('writeFileSync'), src.indexOf('writeFileSync'));
  });

  test('🔴 TWO markers and an apostrophe — the shape that STRANDED A QUOTE', () => {
    // The compound case, and the one with a live consequence. With the offsets
    // shifted two units, the blanking of the middle comment stopped short of its
    // apostrophe; the stray quote then opened a "string literal" that swallowed
    // everything up to the next quote — the write's own filename — so the write
    // was gone. Pinned end-to-end in state-paths.test.mjs's MUST_FIRE too.
    const src = [
      `// ${RED} first marker`,
      "// the receiver doesn't care",
      `// ${RED} second marker`,
      "writeFileSync(join(__dirname, 'accounts.json'), d);",
    ].join('\n');
    const noComments = blankComments(src);
    assertSameShape(src, noComments, 'blankComments');
    assert.ok(!/receiver/.test(noComments), 'the middle comment was not blanked');
    const lits = stringLiterals(noComments);
    assert.deepEqual(lits.map((l) => l.value), ['accounts.json'],
      `the literal scan reported ${JSON.stringify(lits.map((l) => l.value))}. A quote left ` +
        'behind by shifted comment-blanking opens a literal that runs on through live code, ' +
        'and every call inside it is invisible to both structural guards');
  });

  test('an UNTERMINATED literal ending in an astral character keeps the pair whole', () => {
    // The one place a blanking bound can land BETWEEN two surrogates: the stop
    // is clamped to the end of the input rather than sitting on a closing quote.
    const src = `const s = 'never closed ${RED}`;
    const out = blankStrings(src);
    assertSameShape(src, out, 'blankStrings');
    assert.equal(loneSurrogates(out), 0,
      'half of the surrogate pair was blanked and half was not, so the output holds a ' +
        'character that appears nowhere in the input');
  });

  test('a BMP character at the end of a literal does not pull in the closing quote', () => {
    // The threshold is exactly "does this character occupy TWO code units".
    // U+FFFF is the largest BMP code point — one unit — so a `>=` in that test
    // blanks the closing quote, and every literal after it is bracketed wrong.
    const src = "const s = 'x￿'; writeFileSync(p, d);";
    const out = blankStrings(src);
    assertSameShape(src, out, 'blankStrings');
    const q = src.lastIndexOf("'");
    assert.equal(out[q], "'",
      `the closing quote at ${q} was blanked: ${JSON.stringify(out)}`);
  });

  test('an emoji does not move a switch block or its case labels', () => {
    const src = [
      `// ${RED} dispatch`,
      'switch (cmd) {',
      `  case 'one': log('${RED}'); break;`,
      "  case 'two': break;",
      '}',
    ].join('\n');
    const code = blankComments(src);
    assertSameShape(src, code, 'blankComments');
    const b = balancedBlockAfter(code, 'switch (cmd)');
    assert.equal(code[b.start], '{');
    assert.equal(code[b.end - 1], '}');
    assert.deepEqual([...topLevelCaseLabels(code, b)].sort(), ['one', 'two'],
      'a case label was lost once an emoji was present — the help-coverage guard then ' +
        'stops asking for docs on a dispatchable command');
  });
});

// ── stringLiterals ────────────────────────────────────────────────────────

describe('stringLiterals', () => {
  test('reports value, start and end for each quote style', () => {
    const src = `const a = 'one'; const b = "two"; const c = \`three\`;`;
    const got = stringLiterals(src);
    assert.deepEqual(got.map((l) => l.value), ['one', 'two', 'three']);
    for (const lit of got) {
      assert.equal(src.slice(lit.start + 1, lit.end - 1), lit.value,
        'start/end do not bracket the reported value — every offset-based filter downstream ' +
          '(which argument a literal sits in, for one) is then reading the wrong span');
    }
  });

  test('an escaped quote is part of the value, not its end', () => {
    const got = stringLiterals("const s = 'a\\'b';");
    assert.equal(got.length, 1);
    assert.equal(got[0].value, "a\\'b");
  });

  test('start/end still bracket the value when an emoji precedes the literal', () => {
    // INVARIANT GUARD, not regression coverage: this was already green before the
    // code-point fix, because stringLiterals never left code-unit space. It is
    // here because the fix hands it non-ASCII input for the first time, and every
    // offset-based filter downstream slices with these numbers.
    const src = `const s = '${RED} blocked'; const t = 'accounts.json';`;
    const got = stringLiterals(src);
    assert.deepEqual(got.map((l) => l.value), [`${RED} blocked`, 'accounts.json']);
    for (const lit of got) {
      assert.equal(src.slice(lit.start + 1, lit.end - 1), lit.value,
        'start/end stopped bracketing the reported value once a non-BMP character was present');
    }
  });

  test('an unterminated literal does not run past the end of the input', () => {
    const src = "const s = 'never closed";
    const got = stringLiterals(src);
    assert.equal(got.length, 1);
    assert.ok(got[0].end <= src.length, 'end ran past the input length');
  });
});

// ── blankStrings, and the ${…} hole it had ────────────────────────────────

describe('blankStrings', () => {
  test('string contents are blanked, quotes and offsets kept', () => {
    const src = "const s = 'writeFileSync';\nconst t = 2;";
    const out = blankStrings(src);
    assertSameShape(src, out, 'blankStrings');
    assert.ok(!/writeFileSync/.test(out), 'a bait string survived and reads as code');
    assert.ok(out.includes("'") && out.includes('const t = 2;'));
  });

  test('a brace inside a string cannot skew depth counting', () => {
    const src = "const s = '{';\nconst t = '}';";
    const out = blankStrings(src);
    assert.ok(!out.includes('{') && !out.includes('}'),
      'braces inside strings survived, so every depth count over this text is off — that is ' +
        'how "top level" silently moves');
  });

  test('🔴 by DEFAULT a template substitution is blanked with the text', () => {
    const src = 'const p = `${__dirname}/accounts.json`;';
    assert.ok(!/__dirname/.test(blankStrings(src)),
      'the default changed. balancedBlockAfter/topLevelCaseLabels rely on the whole literal ' +
        'being inert; only the seam scanner asks for substitutions back');
  });

  test('🔴 keepSubstitutions preserves the CODE inside ${ … }', () => {
    const src = 'const p = `${__dirname}/accounts.json`;';
    const out = blankStrings(src, { keepSubstitutions: true });
    assertSameShape(src, out, 'blankStrings(keepSubstitutions)');
    assert.ok(/\$\{__dirname\}/.test(out),
      'the substitution was blanked, so a location token written in template-literal form ' +
        'is invisible to any guard scanning this text — which is exactly how the seam scanner ' +
        'stayed silent on `writeFileSync(`${__dirname}/accounts.json`, d)`');
    assert.ok(!/accounts\.json/.test(out),
      'the literal TEXT around the substitution must still be blanked — it is not code');
  });

  // 🔴 THIS GUARD USED TO BE CALIBRATED AROUND THE BUG. It ran ONE fixture,
  // `${join(a, {x: 1})}` — a nested OBJECT, whose braces really are balanced by
  // construction and therefore the one shape that cannot expose the defect. The
  // module's comment claimed "a substitution's braces are balanced by
  // construction"; two shapes made that false, each with delta +1, and both were
  // silent here. A control chosen to avoid the hazard measures nothing.
  const braceDelta = (out) => {
    let depth = 0;
    let negative = false;
    for (const c of out) {
      if (c === '{') depth++;
      else if (c === '}') { depth--; if (depth < 0) negative = true; }
    }
    return { depth, negative };
  };

  for (const [label, src] of [
    ['a nested OBJECT — balanced by construction, the shape that could never fail',
      'writeFileSync(`${join(a, {x: 1})}/f.json`, d);'],
    ["a brace that is TEXT in a nested string: `${f('{')}`",
      "writeFileSync(`${f('{')}/f.json`, d);"],
    // INVARIANT GUARD: this one's delta was 0 before the fix too — but only by
    // luck. The range stopped at the TEXT brace, so a text `}` was kept and the
    // real one blanked; the count balanced while every offset between them was
    // attributed to the wrong side. The range itself is pinned under
    // substitutionRanges, where it WAS red.
    ["a CLOSING brace that is TEXT in a nested string: `${f('}')}`",
      "writeFileSync(`${f('}')}/f.json`, d);"],
    ['a nested BACKTICK, which ends the outer literal early: `${`inner`}`',
      'writeFileSync(`${`inner`}/f.json`, d);'],
    ['a double-quoted brace inside the substitution',
      'writeFileSync(`${f("{")}/f.json`, d);'],
  ]) {
    test(`keepSubstitutions leaves braces BALANCED — ${label}`, () => {
      const out = blankStrings(src, { keepSubstitutions: true });
      assertSameShape(src, out, 'blankStrings(keepSubstitutions)');
      const { depth, negative } = braceDelta(out);
      assert.ok(!negative,
        `brace depth went NEGATIVE on ${JSON.stringify(src)} -> ${JSON.stringify(out)}; ` +
          'argument spans computed from this text are truncated at the wrong character');
      assert.equal(depth, 0,
        `keeping substitutions left brace delta ${depth} on ${JSON.stringify(src)} -> ` +
          `${JSON.stringify(out)}. Unbalanced braces move every span computed downstream, ` +
          'which is the one thing blanking exists to prevent');
    });
  }

  test('🔴 an unbalanceable substitution is BLANKED rather than kept skewed', () => {
    // The safe direction when the brace match cannot be trusted: lose the
    // identifier (a false negative the seam scanner already tolerates for this
    // known limit) rather than carry a stray brace into everyone's depth count.
    const out = blankStrings('const p = `${`inner`}`;', { keepSubstitutions: true });
    assert.ok(!out.includes('${'),
      `a substitution whose braces do not balance was kept anyway: ${JSON.stringify(out)}`);
  });

  test('🔴 a brace in the template TEXT right after a substitution is not counted into it', () => {
    // The range's END bound: reading one character past `}` pulls a brace that
    // belongs to the literal TEXT into the substitution's own balance check, and
    // the substitution is then dropped as "unbalanced" — silently reinstating the
    // `${__dirname}` blindness this option exists to remove.
    const src = 'writeFileSync(`${__dirname}{brace}/accounts.json`, d);';
    const out = blankStrings(src, { keepSubstitutions: true });
    assertSameShape(src, out, 'blankStrings(keepSubstitutions)');
    assert.ok(/\$\{__dirname\}/.test(out),
      `the substitution was dropped: ${JSON.stringify(out)}`);
    assert.ok(!out.includes('{brace}'), 'the braces in the template TEXT were not blanked');
  });

  test('🔴 a brace inside a nested string is blanked, the identifier around it is NOT', () => {
    // Both halves of the point: the depth count must not see the text brace, and
    // the seam scanner must still see the call it is looking for.
    const out = blankStrings("writeFileSync(`${join(__dirname, '{')}/f.json`, d);",
      { keepSubstitutions: true });
    assert.ok(/\$\{join\(__dirname, '\s*'\)\}/.test(out),
      `expected the brace TEXT blanked and the call kept, got ${JSON.stringify(out)}`);
  });

  test('keepSubstitutions does not un-blank a comma in the literal TEXT', () => {
    // A comma in template text at argument depth 0 would split one argument in
    // two and shift every index after it.
    const src = 'writeFileSync(`a,b/${x}`, d);';
    const out = blankStrings(src, { keepSubstitutions: true });
    const inner = out.slice(out.indexOf('`'), out.lastIndexOf('`'));
    assert.ok(!inner.includes(','), 'a comma in template TEXT survived and would split args');
  });

  test('a template with no substitution is blanked either way', () => {
    const src = 'const p = `accounts.json`;';
    assert.ok(!/accounts/.test(blankStrings(src, { keepSubstitutions: true })));
  });
});

describe('substitutionRanges', () => {
  const rangesOf = (src) => {
    const lit = stringLiterals(src)[0];
    return substitutionRanges(src, lit).map(([s, e]) => src.slice(s, e));
  };

  test('finds each substitution, delimiters included', () => {
    assert.deepEqual(rangesOf('const p = `${a}/x/${b}`;'), ['${a}', '${b}']);
  });

  test('brace-matches a nested object literal', () => {
    assert.deepEqual(rangesOf('const p = `${f({x: 1})}`;'), ['${f({x: 1})}']);
  });

  test('🔴 the brace match SKIPS a string nested in the substitution', () => {
    // A brace inside quotes is TEXT. Matching on it ended the range one character
    // into the string, so the REAL closing brace was blanked as literal text
    // while a text brace was kept — balanced only by luck, and every offset in
    // between attributed to the wrong side.
    assert.deepEqual(rangesOf("const p = `${f('}')}`;"), ["${f('}')}"]);
    assert.deepEqual(rangesOf("const p = `${f('{')}`;"), ["${f('{')}"]);
    assert.deepEqual(rangesOf('const p = `${f("}")}`;'), ['${f("}")}']);
  });

  test('ADJACENT substitutions are two ranges, not one', () => {
    // INVARIANT GUARD, not regression coverage: green before this change too.
    // The only fixture separating substitutions used to be `/x/`, so the
    // end-of-range arithmetic was never exercised with nothing between them —
    // untested, not broken.
    assert.deepEqual(rangesOf('const p = `${a}${b}`;'), ['${a}', '${b}']);
  });

  test('a plain string has no substitutions', () => {
    assert.deepEqual(rangesOf("const p = '${a}';"), [],
      "'${a}' is text in a single-quoted string, not a substitution");
  });

  test('a bare $ or { is not a substitution', () => {
    assert.deepEqual(rangesOf('const p = `$ {a} cost`;'), []);
  });
});

// ── balancedBlockAfter / topLevelCaseLabels ───────────────────────────────

describe('balancedBlockAfter', () => {
  test('returns the braces of the block following the header', () => {
    const src = 'switch (cmd) {\n  case 1: break;\n}\nconst after = 1;';
    const b = balancedBlockAfter(src, 'switch (cmd)');
    assert.equal(src[b.start], '{');
    assert.equal(src[b.end - 1], '}');
    assert.ok(src.slice(b.start, b.end).includes('case 1'));
    assert.ok(!src.slice(b.start, b.end).includes('const after'),
      'the block ran past its closing brace');
  });

  test('a brace inside a nested block does not close it early', () => {
    const src = 'switch (cmd) {\n  case 1: { const x = 1; }\n  case 2: break;\n}';
    const b = balancedBlockAfter(src, 'switch (cmd)');
    assert.ok(src.slice(b.start, b.end).includes('case 2'),
      'the block closed at the nested brace, so half the switch is unread');
  });

  test('a brace inside a STRING does not close it early', () => {
    const src = "switch (cmd) {\n  case 1: log('}'); break;\n  case 2: break;\n}";
    const b = balancedBlockAfter(src, 'switch (cmd)');
    assert.ok(src.slice(b.start, b.end).includes('case 2'),
      "a '}' inside a string ended the block — the extractor then sees fewer commands than " +
        'exist, and an undocumented one is invisible');
  });

  test('a header that is not present returns null rather than throwing', () => {
    assert.equal(balancedBlockAfter('const a = 1;', 'switch (cmd)'), null);
  });
});

describe('topLevelCaseLabels', () => {
  const labels = (src) => {
    const b = balancedBlockAfter(src, 'switch (cmd)');
    return [...topLevelCaseLabels(src, b)].sort();
  };

  test('quote style and indentation are irrelevant', () => {
    const src = [
      'switch (cmd) {',
      "  case 'one': break;",
      '        case "two": break;',
      "case 'three': break;",
      '}',
    ].join('\n');
    assert.deepEqual(labels(src), ['one', 'three', 'two'],
      'a case label was missed on quote style or indentation — those were the two spellings ' +
        'that made dispatchable commands invisible to the help guard');
  });

  test('🔴 a NESTED switch\'s labels are not swept in', () => {
    const src = [
      'switch (cmd) {',
      "  case 'outer':",
      '    switch (sub) {',
      "      case 'inner': break;",
      '    }',
      '    break;',
      '}',
    ].join('\n');
    assert.deepEqual(labels(src), ['outer'],
      'a nested switch\'s labels were counted as dispatchable commands, inflating the set ' +
        'with things no command dispatch reaches');
  });

  test('a case label written inside a STRING is not a label', () => {
    const src = "switch (cmd) {\n  case 'real': help(\"case 'fake':\"); break;\n}";
    assert.deepEqual(labels(src), ['real']);
  });

  test('a label immediately AFTER a string literal is still a label', () => {
    // The other side of the same check: "inside a literal" has to end at the
    // closing quote. A window even a few characters too wide silently drops
    // real labels, and the coverage guard then stops asking about them.
    const src = "switch (cmd) {\n  case 'a': f('x');case 'b': break;\n}";
    assert.deepEqual(labels(src), ['a', 'b'],
      'a case label a few characters after a string literal was swallowed by the ' +
        '"is this inside a string?" test — that is an undocumented command going unnoticed');
  });
});

// ── KNOWN LIMITS, pinned so they stay known ───────────────────────────────
//
// These assert what the scanner does WRONG. They are here because the module
// header names both, and a header that names a limit no test demonstrates is
// the same kind of claim this whole round is about. If a future change fixes
// one, this file goes red — update it, do not delete it.

describe('known limits', () => {
  test('LIMIT: a quote inside ${ … } ends the template literal early', () => {
    const src = "const p = `${x['a`b']}`;";
    const lits = stringLiterals(src);
    assert.equal(lits[0].value, "${x['a",
      'the literal scan started recursing into substitutions. That is an improvement — ' +
        'update this test and the module header rather than removing them.');
  });

  test('LIMIT: a regex after a KEYWORD is read as division, so a later comment survives', () => {
    // REGEX_PREFIX holds punctuation only, so `return /'/` leaves an unmatched
    // quote open and the comment after it is never blanked. For the seam
    // scanner that means a commented-out call would read as live code — a false
    // positive, not a miss, which is the safe direction. No module here writes
    // a regex directly after a keyword.
    const src = "return /'/;\n// writeFileSync(join(__dirname, 'x.json'), d)\nconst t = 1;";
    assert.ok(/writeFileSync/.test(blankComments(src)),
      'the keyword-regex case now blanks correctly — good; update this test and the module ' +
        'header, which both describe it as a limit');
    // And the shape that DOES work, so this is a limit and not a general break:
    assert.ok(!/writeFileSync/.test(blankComments(
      "const re = /'/;\n// writeFileSync(join(__dirname, 'x.json'), d)\nconst t = 1;")),
    'an ASSIGNED regex is in REGEX_PREFIX and must still be handled — if this fails the ' +
      'limit is not a limit, it is a break');
  });
});
