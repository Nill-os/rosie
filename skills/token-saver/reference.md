# Token-saving research notes (2026-10-14) — read only when tuning the setup

[DOC]=official docs, [MEAS]=third-party measurement, [SELF]=project's own claim.

## Where tokens go
- Every request re-sends the full prefix (system+tools, CLAUDE.md/memory, conversation). Cache = exact prefix match. [DOC] https://code.claude.com/docs/en/prompt-caching
- Cache read 0.05-0.1× input price; writes 1.25× (5m) / 2× (1h). Subscription main thread = 1h TTL; subagents/compaction = 5m. Idle past TTL → next turn pays full price. [DOC] https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Cache busters: /model switch, fast mode, adding/removing non-deferred MCP servers, compaction, CC upgrade. Safe: file edits, permission/output-style change, skills, subagents, /rewind. [DOC]
- Tool outputs ≈ 84% of an agent turn. [MEAS] https://arxiv.org/abs/2508.21433
- Diagnose: `/context`, `/usage` (cache hit % line). [DOC] https://code.claude.com/docs/en/costs

## Filtering tool output (biggest lever)
- Built-in caps: Bash ~30K chars inline (BASH_MAX_OUTPUT_LENGTH), Read 25K tokens, MCP 25K tokens. [DOC] https://code.claude.com/docs/en/tools-reference
- PreToolUse hook `updatedInput` can rewrite e.g. test commands to `… 2>&1 | grep -A5 -E '(FAIL|ERROR)' | head -100`; PostToolUse `updatedToolOutput` replaces what Claude sees. Docs warn: stripping error detail makes Claude proceed on false assumptions. [DOC] https://code.claude.com/docs/en/hooks
- RTK (Rust Token Killer) hook: claims 60-90% less command output. [SELF] https://github.com/rtk-ai/rtk
- Observation masking matches LLM summarization at ~50% cost cut. [MEAS] arXiv 2508.21433. AgentDiet: 40-60% fewer input tokens, equal performance. [MEAS] arXiv 2509.23586

## Local-LLM offload (what lm does)
- Prior art: houtini-lm (MCP, claims 86-95% fewer Claude tokens, warns local models invent details on truncated input) [SELF] https://github.com/houtini-ai/houtini-lm ; local-ai (qwen3-coder MCP) [SELF] https://github.com/pawarvaibhav/local-ai
- Risks: small-model summaries hallucinate (~5% on Vectara HHEM for 8B-class); zero-shot filters miss ~¼ of relevant lines (Squeez, arXiv 2604.04979). Hence lm verifies every excerpt against the source and counts deterministically.
- Our own test (qwen3.5:9b, RTX 4070 8GB): ~1800 tok/s prompt, ~39 tok/s gen. Both qwen3.5:9b and gemma4:12b MISCOUNTED (17/18 vs true 34) and, given 16K tokens, ignored the first half of the input → chunks capped at ~6K tokens + squash pre-pass. gemma4:12b ~2× slower, no better.
- LLMLingua-style token-dropping compression: not useful here (breaks code/paths, would bust the cache). [MEAS] arXiv 2403.12968

## Context-size habits
- /clear between tasks (free); /compact is a big request — cheap only with a warm cache; /rewind to abandon a path. [DOC]
- CLAUDE.md <200 lines; @imports don't save tokens; `.claude/rules/*.md` with `paths:` load lazily; HTML block comments are stripped. [DOC] https://code.claude.com/docs/en/memory
- Skills: only description loads until used; `disable-model-invocation: true` = zero cost until /name; loaded bodies persist (re-attached ≤5K tok after compaction) → keep bodies short. [DOC] https://code.claude.com/docs/en/skills
- MCP tool search on by default (names only); CLIs (gh, etc.) cheaper than MCP; disable unused servers in /mcp. [DOC] https://code.claude.com/docs/en/mcp
- Subagents: firewall for the parent (e.g. read 6.1K, returned 420), but each rebuilds its own cache; multi-agent ≈15× chat tokens. Explore/Plan skip CLAUDE.md; forks reuse parent cache. [DOC] https://code.claude.com/docs/en/sub-agents , https://www.anthropic.com/engineering/multi-agent-research-system
- Output: thinking is billed as output — lower /effort for easy work; `/output-style concise`. "Caveman" terse speech: only ~8.5% fewer output tokens in a JetBrains A/B (82 tasks) — one brevity line gets most of it. [MEAS] https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/
