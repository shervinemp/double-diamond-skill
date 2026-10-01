# Evals

The question this answers: does `/double-diamond` produce better outcomes than a plain request, enough to justify the extra cost and latency?

## Protocol (blind A/B)

1. Pick prompts from the list below, plus 5 to 10 real past requests of your own. Mix task types.
2. For each prompt, run it twice in fresh sessions:
   - **A:** the prompt alone.
   - **B:** `/double-diamond` followed by the prompt. Answer any questions as you naturally would; say "go" where you would not care.
3. Record per run: wall-clock time, rough token or cost figure if available, and the number of questions asked.
4. Strip the framing so a reviewer cannot tell A from B, shuffle, and score each output 1 to 5 on:
   - **Fit:** does it address what was actually needed?
   - **Surprises avoided:** did it anticipate things you would have had to come back for?
   - **Clarity:** could you act on it immediately?
   - **Scope discipline:** did it stay within what was asked?
5. Afterwards, note which of B's defaulted assumptions you would have changed. That is the **override rate**, and it is the main signal for tuning the regret heuristics.

## What good looks like

- B beats A clearly on the ambiguous, high-stakes prompts.
- B **skips itself** (triage) on the clear, small prompts instead of adding overhead.
- Override rate is low. If you flip more than about a third of the defaults, the low-regret bucket is too generous.
- Questions asked are mostly ones you were glad to be asked. If you said "go" to most of them, the ask bucket is too wide.

## Starter prompts

Expected to run the pipeline (ambiguous and costly to get wrong):
1. "Set up CI/CD for this project."
2. "We need to add multi-tenancy to our app."
3. "Write a launch announcement for our new product." (give it a one-line product description)
4. "Help me decide whether to build or buy an internal search tool."
5. "Plan the migration from our monolith to services."

Expected to skip (clear and small), as a check that triage works:
6. "Rename the variable `x` to `count` in this file."
7. "How do I format a date in Python?"
8. "Fix the typo in the README heading."

Recommendation tasks (should use the vetting path, ask almost nothing, and never rely on star ratings alone):
- "Where can I print and bind my immigration forms near me?" Check: compares real prices, looks beyond one platform's ratings, checks the business exists, flags the privacy of the documents, recommends protected payment, states what was and wasn't verified.
- "Which moving company should I use for a cross-country move?"
- "Find me a contractor to redo my bathroom."

Edge cases:
9. A request that is already fully specified but large. The pipeline should skip asking and go straight to the plan.
10. A request where two options differ only on a values trade-off. The judge should flag "needs a user value call".
