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
 * `` writeFileSync(`${__dirname}/accounts.json`, d) `` — the idiom listen.mjs
 * itself writes — reached the seam scanner as a token-free string and passed
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

/**
 * Every one of these functions promises OFFSET PRESERVATION — an index into the
 * result points at the same character it did in the input. Both guards rely on
 * it (they scan `bare` and then read `literals` computed from another copy), and
 * nothing else in the suite would notice it breaking.
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
    const src = "const u = 'https://webhook.site/token';\nconst p = accountsPath();";
    const out = blankComments(src);
    assert.ok(out.includes('webhook.site/token'),
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
      'the substitution was blanked, so a location token written the way listen.mjs writes it ' +
        'is invisible to any guard scanning this text — which is exactly how the seam scanner ' +
        'stayed silent on `writeFileSync(`${__dirname}/accounts.json`, d)`');
    assert.ok(!/accounts\.json/.test(out),
      'the literal TEXT around the substitution must still be blanked — it is not code');
  });

  test('keepSubstitutions leaves braces BALANCED', () => {
    const src = 'writeFileSync(`${join(a, {x: 1})}/f.json`, d);';
    const out = blankStrings(src, { keepSubstitutions: true });
    let depth = 0;
    for (const c of out) {
      if (c === '{') depth++;
      else if (c === '}') depth--;
      assert.ok(depth >= 0, 'brace depth went negative — argument spans would be truncated');
    }
    assert.equal(depth, 0,
      'keeping substitutions unbalanced the braces, which moves every span computed from ' +
        'this text');
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
