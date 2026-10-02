# Benchmarks

The question these answer: does the pipeline produce better outcomes than a plain request, enough to justify its extra cost and latency? Without a blind comparison there is no way to tell whether it helps or just feels thorough.

There are two ways to run it.

## Automated (headless orchestrator)

```bash
double-diamond eval run benchmarks/prompts.jsonl --out results/first-run
```

For each prompt it runs a plain model call and the full pipeline (non-interactive, defaults accepted), shuffles the two outputs behind neutral labels, and has a judge model score each pair twice with the order swapped to cancel position bias. Scores cover **fit**, **surprises avoided**, **clarity** and **scope discipline**. It also reports triage accuracy against each prompt's `expect`, tokens, and wall-clock time.

Output in `--out`: `report.md`, `results.json`, `blind/<id>.md` (the unlabeled pairs), `key.json` (which letter was which). Eval runs use their own state directory, so they never touch your real preferences.

To judge by hand instead, use `--judge human`, read the files in `blind/`, record your pick for each in `scores_template.json` (`A`, `B` or `tie`), then unblind:

```bash
double-diamond eval reveal results/first-run results/first-run/my_scores.json
```

The automated judge is a model and has its own biases. Treat its numbers as a screen and spot-check with `--judge human` on a few prompts.

## Manual (the skill, in Claude Code)

1. Pick prompts from `prompts.jsonl` plus 5 to 10 real past requests of your own.
2. For each, run it twice in fresh sessions: **A** the prompt alone, **B** the skill followed by the prompt. Answer any questions as you naturally would; say "go" where you would not care.
3. Record per run: wall-clock time, rough cost, number of questions asked.
4. Strip the framing so a reviewer cannot tell A from B, shuffle, and score each 1 to 5 on fit, surprises avoided, clarity and scope discipline.
5. Note which defaulted assumptions you would have changed. That is the **override rate**, the main signal for tuning the regret heuristics.

## What good looks like

- The pipeline beats the baseline clearly on the ambiguous, high-stakes prompts.
- It **skips itself** on the clear, small prompts instead of adding overhead.
- Override rate is low. If you flip more than about a third of the defaults, the low-regret bucket is too generous.
- Questions are mostly ones you were glad to be asked. If you said "go" to most, the ask bucket is too wide. `double-diamond stats` reports both rates from real use.
- Recommendation prompts compare real prices, look beyond one platform's ratings, check the business exists, flag privacy, and say what was and was not verified.

## Prompt file format

JSON Lines, one object per line: `id`, `prompt`, `expect` (`pipeline`, `skip` or `either`), `kind`. Lines starting with `#` are comments.
