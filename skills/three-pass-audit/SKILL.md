---
name: three-pass-audit
description: "Audit loop that signs off only after three clean passes in a row (fresh parallel reviewers, verified findings, fixes between passes, hard cap of 8). Use for a full audit or a ready-to-order or ready-to-ship check of hardware, firmware or code, or /three-pass-audit."
---

# Three-pass audit

The user's rule: **audit, fix, re-audit - and only call it good after 3 consecutive clean passes.** A pass that finds
a real problem resets the count to 0, even if the passes before it were clean. (On one PCB this caught a backwards microSD
socket in pass 4 and a USB-C plug blocked by the board edge in pass 5, after two "clean" passes - which is exactly why the
rule exists.)

Claude Code runs this with its Agent tool. Agent Zero: run one `call_subordinate` (`reset: true`) per reviewer. Rosie: offer
to hand the audit to Agent Zero or Claude Code instead of running it yourself.

## 1. Set up (once)

1. Run the project's own checks first (audit script, DRC, tests, build). They must pass before a reviewer pass starts.
2. Write a **brief** file in the scratch/job dir (not the repo) that every reviewer reads first:
   - what the product is, the files and tools (paths, how to run the checks), local datasheets
   - conventions already established (e.g. JLC CPL bottom-side rotation = 180 - angle)
   - **ACCEPTED items**: known trade-offs, numbered, each with the reason. Reviewers must not re-report them unless they
     prove the item is wrong or worse than stated.
   - **Changes since the last review** (updated every pass) - reviewers look at these closely.
   - Severity definitions and the report format (below).
   - "READ-ONLY: never modify the repo; scratch files only under <dir>; no git writes."

Severities:
- **BLOCKER** - won't work, can't be built/assembled as ordered, or something gets damaged.
- **SHOULD-FIX** - a real functional, assembly or reliability risk worth changing before ordering/shipping.
- **NOTE** - minor, cosmetic, informational.
Every finding: severity, exact location (refs/nets/coordinates/file:line), evidence (measured numbers, datasheet
section, tool output), concrete fix. Plus a "checked and OK" list as one comma-separated line. Under ~400 words; no
restating the brief, no quoted file contents beyond the few lines that prove a finding.

## Save tokens (always)

The streak rule never changes: every pass is a real, full-scope review. Spend less per pass instead:
- **Rosie reads, Claude judges.** Reviewers and the verify step read big files through the local model first: `lm outline FILE`,
  then Read only the needed range; `lm sum`/`lm ask` for logs, test output and long diffs (token-saver skill). Never Read a
  whole file over ~300 lines just to look around.
- **3-4 reviewers per pass**, one Agent call each, split by domain so they don't overlap. Give each the brief's path, not the
  brief pasted into the prompt.
- **Cheaper model after pass 1.** Pass 1 runs on the default model. Later passes may run reviewers with `model: "sonnet"`; a
  reviewer whose domain had a BLOCKER fixed since the last pass stays on the default model.
- **Scripted checks only when something changed.** Rerun the test suite/DRC/build only if code or design changed since its
  last green run; otherwise reuse that result and say so in the pass table.
- **Verify cheaply.** Check a finding with `grep -n`/`sed -n` on the cited lines or one targeted command, not a full re-read.
- **Short updates.** One line to the user per pass (result + streak); the full table only at the end.

## 2. Each pass

Launch the reviewers **in parallel, as fresh agents** (no memory of earlier passes - independence is the point), one
per domain, each with its own scratch subdir (`p<N>_<domain>/`). For a PCB:
- **Electrical + firmware**: re-derive every IC's supply/ground/decoupling/straps/unused pins from the board's actual pad
  nets and datasheets; every firmware GPIO vs the board; regulator/charger equations; back-power paths; sequencing;
  interfaces/cables; power budget.
- **Layout + physical + mechanical**: DRC, copper connectivity (islands, stubs, duplicates, single-layer vias), fills
  current, vias in pads/paste, keep-outs, RF, high-current paths, decoupling distance, courtyards, edges, holes,
  silkscreen, board vs netlist - and **every connector / button's mating direction checked against its library fab
  drawing or datasheet** (which side the card/plug/cable/finger enters, clearance for the mating part, nothing in the
  insertion path, the PCB edge not blocking a plug overmold).
- **Manufacturing files (DFM)**: fabrication outputs regenerated and byte-compared, BOM vs CPL vs board, stock, CPL
  positions/rotations fitted independently against the assembler's own footprints, DFM limits, the user-facing order
  instructions and the copies in the user's order folder.
For code: e.g. correctness/logic, security, tests/build/release artifacts. Tell each reviewer to re-derive rather than
trust comments or earlier reports, and point at the riskiest recent changes.

## 3. Verify, then fix

- **Verify every BLOCKER / SHOULD-FIX yourself** before acting (re-measure, re-read the datasheet, reproduce). Reviewers
  are sometimes wrong (e.g. an "all LEDs backwards" claim was disproved by the assembler's own placement preview).
  If you reject or downgrade a finding, say so to the user with the evidence, and add it to the brief's ACCEPTED list
  so later passes don't re-raise it without new proof.
- A pass is **clean** only if, after verification, no BLOCKER or SHOULD-FIX remains. NOTEs don't fail a pass.
- Fix real findings (and cheap, safe NOTEs) between passes. After fixing: re-run the scripted checks, regenerate
  outputs, refresh any user-facing copies, commit (and push if that's the repo's workflow), and update the brief's
  "changes since the last review".
- Turn every real finding into a scripted check where possible (so it can never silently come back).
- Don't game the count: a downgrade needs evidence; if in doubt, it is not clean.

## 4. Count and stop

- Clean pass -> streak + 1. Any verified BLOCKER/SHOULD-FIX -> streak = 0.
- Stop and call it good only at **3 consecutive clean passes**. Then report: pass-by-pass table (result, what was
  fixed, commits), the final state of the scripted checks, accepted trade-offs that the user should know, and
  follow-ups (bring-up checks, case/firmware notes).
- Keep the user posted in one or two lines after each pass (result + streak).
- **Hard cap: 8 passes (or ~6 hours), whichever comes first.** If there still aren't 3 clean in a row, stop launching
  reviewers, don't deploy/ship, and ask the user how to proceed (narrow scope, accept items, or keep going) with the
  pass table and the remaining findings. Also stop and ask early if two passes in a row find regressions from your own
  earlier fixes. (On Oct 8 2026 an uncapped loop ran 29 passes, never signed off, burned the weekly usage limit and
  blocked the job the user actually wanted run.)
- **Scope is locked** to what was agreed at setup. Findings outside it go in a "later" list for the user; they don't
  get fixed in-loop and don't reset the streak.
- Never let an audit block other work the user asked for - start that work first unless it truly depends on the audit.
- If you run out of budget mid-loop: stop running reviewers, and tell the user the streak, what is fixed/committed,
  and "say continue to resume from pass N" - never present an unfinished loop as signed off.
