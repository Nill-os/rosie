---
name: token-saver
description: Keep bulk text out of Claude's context. Use BEFORE reading anything big — logs, journalctl/dmesg, build or test output, long files (>~300 lines / >20KB), dumps, scraped pages, long diffs — before spawning a subagent, and when asked to save tokens or work leaner. Offloads reading to the local Ollama model via `lm` (sum/ask/squash/draft) with source-verified excerpts, and hands Claude-level jobs that don't need the top model to Haiku or Sonnet subagents.
---

# Token saver

Every token that enters context is re-sent on every later turn. Tool output is ~80% of agent tokens. So: **filter before it enters context**, cheapest tool first.

## Ladder (stop at the first rung that answers the question)
1. **Know what you need** → targeted `grep -n PATTERN | head`, `grep -c`, `tail -n 50`, `Read` with offset/limit, quiet flags (`-q`, `--silent`, `pytest -q`). Redirect noisy commands to a file (`cmd > $SCRATCH/out.txt 2>&1; tail -n 30 …`), then grep it.
2. **Big source file** → `lm outline FILE` — no model: functions/classes/headings with line numbers (3,747-line C file → 64 lines). Then Read only that range.
3. **Repetitive text (logs, test spam)** → `lm squash FILE` — no model, instant: masks timestamps/ids/numbers, collapses repeats to `xN Lfirst line` (600-line log → 138 lines).
4. **Don't know what to look for** → `lm sum FILE [--focus "auth errors"]` — distinct points, each `Lfirst xN: meaning | "verbatim excerpt"`. Counts and line numbers are computed by the script, not the model.
5. **Specific question about a big input** → `lm ask "QUESTION" FILE` — answer + line citations, each marked `ok`/`UNVERIFIED`.
6. **Boilerplate prose** (commit msg, README section, docstrings from a diff) → `cmd | lm draft "INSTRUCTION" -`. Saves Claude output tokens (the expensive kind). Review it; rewrite if off.
7. **Whole writing jobs** (only where the `orchestrate` skill and `delegate` are installed; otherwise skip this rung) (a test file, a script, a doc page, a review of one file, a rewrite) → `delegate rosie JOB "INSTRUCTIONS + acceptance criteria" FILE...`, check it, `delegate again JOB "what to fix"` until it passes. Multi-step jobs that need code run or web research → `delegate a0`. See the `orchestrate` skill; when orchestrator mode is ON (`delegate status`), this is the default way to work.

8. **Claude-level work that doesn't need the top model** → a subagent on a cheaper Claude model. See "Cheaper Claude models."

All take `-` for stdin. Run one `lm` at a time (one model fits on the GPU), prefix `nice -n 19`. ~12K chars/chunk ≈ 11 s each (`sum`/`ask` replies are capped so the 9B stays fast).

**Rosie = this model.** `lm` uses Rosie's chosen model and context window (Settings > AI model and Context window, saved in her `data/config.json`) and her 30-minute keep-alive, so both share one loaded copy. A different `LM_MODEL` or `LM_CTX` loads a second copy and evicts hers: use one only while she is idle.

**Not installed?** If `lm` isn't on your PATH, run `python3 <this skill folder>/scripts/lm.py` (Windows: `py`, or Rosie's own `%LOCALAPPDATA%\Rosie\slots\A\.venv\Scripts\python.exe` (or `slots\B` after an update); leave out `nice -n 19`). Set `ROSIE_HOME` if Rosie's folder isn't found. If that fails or exits 2 (Ollama down), use rung 1 only. The hooks section applies only where `~/.claude/settings.json` runs `scripts/hooks.py`. Claude Code only: Rosie and Agent Zero don't follow this skill.

## Cheaper Claude models
When a job is too hard or too risky for Rosie and Agent Zero (a 9B model) but doesn't need the main model's judgment, give it to a subagent on Haiku or Sonnet. Use the Agent tool with `model: "haiku"` or `model: "sonnet"`. A cheaper model uses less of the usage limit per token. The main thread keeps the plan, the hard parts, and the final check.

- **Haiku:** searching and sweeping many files, collecting facts with `file:line` references, running a test suite and reporting only the failures, mechanical edits with an exact spec (rename across files, apply one pattern), format conversions, and pulling data out of files.
- **Sonnet:** well-specified code changes in a few files, writing tests for known behavior, bug fixes with a clear repro, first-pass code review of one change, and docs or scripts with clear acceptance criteria.
- **Keep on the main model:** planning and design, unclear debugging, security, anything across many files that needs judgment, the final check, and anything the user acts on directly.

How to hand off:
1. Write a self-contained prompt, because the subagent can't see this conversation. Include the goal, the exact files, the constraints, numbered acceptance criteria, and the output format. For example: "Reply in under 15 lines: what changed, `file:line`, and the test result."
2. Tell the subagent to follow the token-saver rules (`lm`, grep, offset reads) and not to paste whole files back.
3. If you have several independent jobs, start them in one message so they run in parallel. Run no more than about 8 at a time.
4. Check the result like any agent output: read the diff, run the tests, and check the cited lines. If it fails, send it back once with the specific fix (SendMessage to the same agent keeps its context). If it fails again, do the job yourself.

When it costs more than it saves:
- Each subagent starts with an empty cache and loads its own system prompt. A job that reads or writes little costs more in a subagent. Hand off only when the job reads far more than it returns (about 10 times or more), or when it is long.
- A `fork` always runs on the main model and ignores `model`. Use a fork to reuse the cache, not to save on the model price.
- Don't switch the main session with `/model` to save tokens, because that busts the prompt cache. Use subagents instead.
- For scripts and hooks outside a session, `claude -p --model haiku "PROMPT"` runs one headless turn on Haiku.

## Automatic hooks (where ~/.claude/settings.json runs scripts/hooks.py)
- **Bash output over 6,000 chars** is saved to `~/.cache/token-saver/out/`. If it is log/build/test-like (repetitive, or `journalctl`, `pytest`, `make`, `npm`, `pio`, `esptool`, and so on) and its unique lines fit in ~48K chars, Rosie summarizes it (`lm sum`, ~10-45 s) and you get her verified points plus the last 15 lines. Otherwise you get a squashed or head+tail view. Grep the saved file for detail. Add `#raw` at the END of a command for unfiltered output (`#` starts a shell comment). Failed commands (non-zero exit) are never filtered.
- **Read of a text file over 300 lines / 20 KB without offset/limit** is blocked. The block message includes Rosie's summary for `.log`/`.txt`/`.out` files, or an `lm outline` for code. Pass offset/limit (offset=1 limit=N for the whole file when truly needed).
- **Every prompt** gets a one-line Rosie-first reminder with her up/down status.
- `touch ~/.claude/skills/token-saver/rosie-off` stops the hooks from calling the model (for example, while Rosie is busy with voice); squash and outline still run. `rm` the file to resume.
- **Quiet flags** are added to `pytest`, `make`, `cargo`, `pip install`, `npm install`, `git clone` unless already set or `#raw`.
- If `~/.claude/agents/Explore.md` sets `model: haiku`, Explore subagents run on Haiku. Status line shows context size, cache hit rate, and today's savings.

## Trust rules (the local model is a 9B — it skims and miscounts)
- Only act on **verified** output. `UNVERIFIED` lines/answers = discard or check yourself.
- `NOT FOUND` from `lm ask` is weak evidence — confirm with grep if it matters.
- `xN` counts that exact excerpt only; for a category total, `grep -c` a broader pattern.
- Before editing code or making a claim to the user from an `lm` result, `sed -n 'L-2,L+2p' FILE` the cited line. Never feed `lm` text to Edit as old_string.
- Don't use `lm` under ~200 lines — overhead + latency beats the savings; just Read/grep.
- Exit code 2 = Ollama down → fall back to rung 1, don't retry in a loop.

## Session habits (from research — see reference.md for sources)
- Don't re-Read a file already in context unless it changed. Prefer Edit over Write; don't echo code back in replies.
- Subagents only when they'd read far more than they return (≥~10× ); small lookups cost more in a subagent. Explore/haiku agents are cheapest; a fork reuses the cache. Pick the model by the job ("Cheaper Claude models").
- Don't switch model mid-session (busts the prompt cache). Suggest `/clear` between unrelated tasks and `/compact Focus on …` at natural breaks.
- Write replies in the Google developer documentation style (`communication.md` in this folder): answer first, short plain sentences, no filler or recap.

`lm stats` = running total of tokens kept out of context and how many calls Rosie answered (ledger.jsonl). The status line shows today's count. Tuning: `LM_THINK=1` (better reasoning, slower); `LM_MODEL=<bigger model>` only while Rosie is idle (see above).
