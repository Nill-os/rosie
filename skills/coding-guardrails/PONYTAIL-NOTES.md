# Ponytail notes (not part of SKILL.md)

Source: https://github.com/DietrichGebert/ponytail (skills/ponytail/SKILL.md), commit 09c434a, MIT licence. Not installed; only these rules are extracted.

Rules not already in SKILL.md:
1. Order of options for a change: skip it, reuse an existing helper or pattern, use the standard library or platform feature, use an installed dependency, write one line, then write the minimum code.
2. A vague request ("build me X") gets the smallest version that does the core job; name the features you skipped in one line.
3. Before editing, list every place the change must reach (callers, tests, fixtures, config, exports) and what it could break for users (data destroyed or exposed).
4. Be lazy about the solution, never about the change: finish every caller, test and fixture your change breaks.
5. Bug fix: grep every caller of the function first, then fix the root cause once in the shared code.
6. Code you move or merge keeps its error handling and validation.
7. Between options of equal size, take the one that is correct on edge cases; a one-liner that needs decoding is not short.
8. New non-trivial logic (branch, loop, parser, money, security, new script) leaves one small test or assert-based self-check; trivial changes need none.
9. A shortcut with a known limit gets a one-line comment: `shortcut: <the limit>, <when to upgrade>`.
10. End each reply with one or two lines: what you skipped or did not check, and any risk the user must know. Never cut validation at trust boundaries, data-loss error handling, security, accessibility or hardware calibration.

Benchmark method (their own claim): a real Claude Code session on a real public repo with an over-build trap, comparing lines added and correctness/safety against a bare model and a bare "one-liner" prompt. They report 60-94% less code on trap features, no change on already-minimal code, and 100% safe; the older single-shot numbers are labelled as overstated. Treat these as the author's figures.
