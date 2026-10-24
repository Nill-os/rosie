---
name: "coding-guardrails"
description: "Use when writing, fixing, reviewing or refactoring code: keep changes minimal and on-request, check that APIs exist, define a runnable success check, verify before saying done, and stop after two failed attempts."
---

# Coding guardrails

Distilled from several saved posts about stopping AI coding agents from over-engineering, ignoring instructions, faking completion and hallucinating APIs, plus the useful parts of andrej-karpathy-skills (MIT, forrestchang).

## Before writing code
- Restate the task in one sentence. If two readings are plausible and a wrong guess is costly, ask one question; otherwise pick the obvious reading and say which one you took.
- Read the files you will touch, plus their callers, before editing.
- Check that every library function, CLI flag or config key you plan to use exists in the installed version. Look at the package source, `--help` output or docs. Never guess an API.
- Turn the request into a check you can run (a failing test or a repro) before changing code. For multi-step work, list each step with how you will verify it.

## While writing code
- Change only what the request needs. No drive-by refactors, renames, formatting sweeps or new abstractions unless asked.
- Prefer the simplest working approach. No new dependencies unless clearly needed, and say why when you add one.
- Don't touch files the user didn't mention unless the change can't work without it. If you must, list them.
- Keep existing style, naming and structure.
- Remove imports, variables or functions your change made unused. Mention older dead code; don't delete it.
- If the result is much longer than the problem needs, simplify it before handing it over.

## Before saying it's done
- Run it: build, tests, or a minimal reproduction of the fix. Quote the key result lines (the pass/fail summary), not the whole log.
- If something could not be verified (no hardware attached, no credentials, no test suite), say exactly what was not checked.
- Never mark a task complete while a step failed or was skipped. Report it as partial, with what remains.
- Summarize what changed as a short file list with a one-line reason each.

## When stuck
- After two failed attempts at the same fix, stop and explain what you tried, what you observed and your best hypothesis, instead of trying random changes.