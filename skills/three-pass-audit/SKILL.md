---
name: three-pass-audit
description: "Audit loop that signs off only after three clean passes in a row: an approved risk list, a failure test per risk, each fix gated by its own test and diff review, focused passes, and a final full-scope pass (hard cap of 8). Use for a full audit or a ready-to-order or ready-to-ship check of hardware, firmware or code, or /three-pass-audit."
---

# Three-pass audit

The user's rule: **audit, fix, re-audit, and only call it good after 3 consecutive clean passes.** A pass with a verified
real finding resets the count to 0. (On one PCB this caught a backwards microSD socket in pass 4 and a USB-C plug blocked by
the board edge in pass 5, after two "clean" passes. That is why the rule exists.)

The 2026-10-25 rework keeps that rule and makes it converge. What went wrong before: fresh reviewers were told to "find
anything" in the whole area every pass, so a big surface (a launcher's error paths) yielded new old problems forever; fixes
went straight into the next full pass, so their regressions showed up a whole pass later; and hardening ideas were filed as
SHOULD-FIX and reset the streak.

Claude Code runs this with its Agent tool. Agent Zero: one `call_subordinate` (`reset: true`) per reviewer. Rosie: offer to
hand the audit to Agent Zero or Claude Code instead of running it yourself.

## 1. Set up (once, before any reviewer)

1. Run the project's own checks (tests, audit script, DRC, build). They must pass first.
2. **Risk list.** Write the concrete ways this product can fail the user, per area: one line each, as a failure scenario
   ("an update leaves her with no working Rosie", "a second launcher rolls back a good update", "private data reaches the
   public repo", "the USB-C plug can't seat"). Mark each risk with how it gets checked: **scripted** (a test or tool can
   prove it) or **review** (needs judgment). Show the list to the user and get it approved. The list is the scope.
3. **A failure test per scripted risk.** Before pass 1, write tests that inject the failure: a crash at each step of an
   update, a second instance, a slow start, a locked file, a planted private string, a misspelled marker, a DRC rule, a
   connector clearance measured from the footprint. Risks covered by a passing failure test are re-checked by running
   the test, not by a reviewer reading code. A cheap model (`model: "sonnet"`) writes them; you check that each test fails
   when the protection is removed.
4. **Run where it really runs.** Platform-specific risks (Windows paths, a phone, a board) need a real run: a CI job on
   that platform, the target device, or a bring-up checklist. Ask the user before touching their or someone else's device.
   Check that the platform run really includes those tests: a test file left out of a public export or skipped by a marker
   never runs there. Never trust a "covered on CI" comment or a reviewer's claim; find the run or the job config that
   proves it. (2026-10-25: the Windows junction and lock tests were excluded from the public copy, so Windows CI never ran
   them, while one reviewer said it did.)
5. Write the **brief** in the scratch/job dir (not the repo). Every reviewer reads it first:
   - the product, files, tools and how to run the checks; local datasheets
   - the approved risk list, with which failure test covers which risk
   - **ACCEPTED items**: known trade-offs, numbered, each with its reason. Reviewers must not re-report them without new
     proof.
   - **Changes since the last pass** (updated every pass)
   - the severity bar and report format below
   - "READ-ONLY: never modify the repo; scratch files only under <dir>; no git writes; nice 19, at most 4 test workers."

## Severity bar (strict)

- **BLOCKER**: it won't work, can't be built or ordered as is, damages something, or leaks private data or a secret.
- **SHOULD-FIX**: a concrete failure scenario that is **plausible on the real target** (her PC, this board, this
  printer), with evidence: a reproduction, a failing test, a measured number, a datasheet line. "Could be hardened", "a
  rarer variant might slip through", "another pattern to add" are NOT SHOULD-FIX.
- **NOTE**: hardening, polish, rare edge cases, ideas. NOTEs go on the **later list** and never reset the streak.

Every finding: severity, exact location (file:line, ref, net, coordinates), evidence, the failure scenario in one
sentence, and a concrete fix. Then one comma-separated "checked and OK" line. Under ~400 words.

## 2. Passes

**Pass 1 (full scope):** 3-4 reviewers in parallel, fresh agents, one per area, each with its own scratch subdir
(`p1_<area>/`). Each checks the risk list for its area and looks for risks the list missed. A missed risk that meets the
SHOULD-FIX bar is added to the risk list (with a failure test when it can be scripted).

**Passes 2..N (focused):** reviewers get only the diff since the last pass plus the risk list. They check that the fixes
work, that nothing nearby regressed, and that every risk still holds (by running the failure tests). One reviewer per area
that changed; skip areas with no change and say so.

**Final pass (full scope):** the last pass of a sign-off is always a fresh full-scope pass like pass 1. The streak only
completes when that final full pass is clean.

Domains, as examples. Code: correctness and error paths; security and privacy; tests, build and release. PCB:
electrical and firmware; layout, physical and mechanical (every connector's mating direction against its fab drawing);
manufacturing files (outputs regenerated and byte-compared, BOM vs CPL vs board, rotations).

Models: reviewers and fix agents run on `model: "sonnet"`. Keep the main model for the risk list, verification, the
final pass's triage and sign-off. Reviewers read big files through `lm outline` / `lm ask` first.

## 3. Verify, then fix (the fix gate)

1. **Verify every BLOCKER and SHOULD-FIX yourself** with the cited lines (`sed -n`, `grep -n`) or one targeted command.
   Apply the severity bar: downgrade hardening ideas to NOTE and say so. Rejected findings go to ACCEPTED with the
   evidence.
2. **Fix gate.** A fix enters the next pass only when it has:
   - a test that **fails on the old code and passes on the new**. Run it both ways by restoring only the product file
     (`git show HEAD:<file> > <file>`), never `git stash`, which also hides the new test. Check pytest's own exit code,
     never a pipe's, and never chain a commit after `pytest | tail` with `&&`;
   - a **diff review**: one small reviewer (`model: "sonnet"`) that sees only that fix's diff and asks "what does this
     break?": every new exception path, `None` values, platform differences (e.g. `RuntimeError` vs `OSError` across Python
     versions, Windows file locks), callers of changed functions, and exits that pass through (`finally` also runs on
     `SystemExit`: a cleanup that restarts something must use `except Exception`, not `finally`);
   - the project's checks green again.
   A regression the diff review catches is fixed before the pass; it does not count against the streak.
3. Turn every real finding into a scripted check, so it can't silently come back.
4. Commit fixes in a dev copy or branch; deploy only per the project's own rules (slots, staging, probation).

## 4. Count and stop

- Clean pass (no verified BLOCKER or SHOULD-FIX) -> streak + 1. Verified BLOCKER or SHOULD-FIX -> streak = 0.
- Sign off at **3 consecutive clean passes, the last one full scope**. Report: the pass table (result, fixes, commits), the
  failure tests and their state, the ACCEPTED trade-offs, and the later list.
- **Hard cap: 8 passes (or ~6 hours).** Then stop launching reviewers, don't deploy or ship, and ask the user how to
  proceed with the pass table and the open findings.
- **Stop and ask early** if a fix's regression gets past the fix gate into a pass, or if two passes in a row find new old
  problems in the same area: that area needs a better failure test or a narrower risk list, not more reviewers.
- **Scope is the approved risk list.** Out-of-scope findings go on the later list.
- Never let an audit block other work the user asked for.
- If budget runs out mid-loop: stop, report the streak and what is fixed and committed, and say "say continue to resume
  from pass N". Never present an unfinished loop as signed off.

## Record of runs

- 2026-10-25, Rosie Windows/OTA/export/skill sync re-audit. Old method: 3 passes, streak 0, two of its own fixes
  regressed. New method: 15-risk list, failure tests (they found 2 real launcher bugs before any reviewer did), every
  mutation caught; passes: full clean, focused clean, final full clean = signed off. The fix gate caught one regression
  (`finally` on `SystemExit`) before it reached a pass.

## Save tokens (always)

- Rosie reads, Claude judges: `lm outline FILE`, then Read only the range; `lm sum` / `lm ask` for logs and long diffs.
- Failure tests replace re-reading: a risk with a passing test needs no reviewer time.
- Focused passes read the diff, not the whole area.
- Rerun the full test suite only when code changed since its last green run; otherwise reuse the result and say so.
- One-line update to the user per pass (result and streak); the full table only at the end.
