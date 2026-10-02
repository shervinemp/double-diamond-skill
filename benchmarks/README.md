# Benchmarks

The question these answer: does the skill produce better outcomes than a plain request, enough to justify its extra cost and latency? Without a blind comparison there is no way to tell whether it helps or just feels thorough.

Everything here runs inside Claude Code on your normal login. No separate setup.

## Protocol (blind A/B)

1. Pick prompts from [prompts.md](prompts.md), plus 5 to 10 real past requests of your own. Mix task types.
2. For each prompt, run it twice in fresh sessions:
   - **A:** the prompt alone.
   - **B:** the skill followed by the prompt (`/double-diamond <prompt>`, or `/double-diamond:double-diamond <prompt>` if installed as a plugin). Answer any questions as you naturally would; say "go" where you would not care.
3. Record per run: wall-clock time, rough cost, and the number of questions asked.
4. Strip the framing so a reviewer cannot tell A from B, shuffle, and score each output 1 to 5 on:
   - **Fit:** does it address what was actually needed?
   - **Surprises avoided:** did it anticipate things you would otherwise have had to come back for, including the obvious requirements (price, quality, legitimacy)?
   - **Clarity:** could you act on it immediately?
   - **Scope discipline:** did it stay within what was asked?
5. Afterwards, note which of B's defaulted assumptions you would have changed. That is the **override rate**, the main signal for tuning the regret heuristics.

## What good looks like

- B beats A clearly on the ambiguous, high-stakes prompts.
- B **skips itself** (triage) on the clear, small prompts instead of adding overhead.
- Override rate is low. If you flip more than about a third of the defaults, the low-regret bucket is too generous.
- Questions are mostly ones you were glad to be asked. If you said "go" to most of them, the ask bucket is too wide.
- Recommendation prompts compare real prices, look beyond one platform's ratings, check the business exists, flag privacy, and say what was and was not verified.
