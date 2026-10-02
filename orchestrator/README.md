# double-diamond (headless orchestrator)

The same expand-then-contract pipeline as the Claude Code skill, as a Python program. Use it when there is no human in the loop, when you want enforced steps and measurable cost, or to run evals.

The control flow is code; models only fill narrow structured transforms. The rules that make the design work are enforced in plain code (`policy.py`) so they hold whatever a model returns:

- **Triage** needs both ambiguity and cost-of-being-wrong (money or sensitive documents count as costly).
- **Question policy.** An "ask" item must pass all three tests (changes the plan, a genuine toss-up, costly if wrong) and not be obvious; the model's own flags are re-checked, failing items are demoted to defaults, and at most three questions survive.
- **Standing preferences.** The last two explicit answers to the same topic become a default. Accepting a default is never counted as a preference.
- **Anonymized, shuffled candidates**, generated in parallel on a cheaper model, judged once on a different model. Weighted totals, disqualification, margin and confidence are computed in code, not trusted from the judge.
- **Universal baseline** criteria (cost and value, quality, legitimacy, time, safety/privacy/recourse, the task's own rules) are always in the rubric.
- **Graft gate.** A graft must come from a loser, be compatible, and name the specific weakness it fixes.
- **Scrubbed contraction.** The contractor never sees raw candidates, scores, or the judge's reasoning, and the assumption ledger and non-goals are attached by code so they cannot be dropped.
- **Recommendation path.** Real options are researched and vetted with web search; an option backed only by platform ratings cannot be the top pick while better-evidenced options exist.

## Install

```bash
pip install -e "./orchestrator[dev]"
```

Python 3.10 or newer. Authentication is whatever the Anthropic SDK resolves: `ANTHROPIC_API_KEY`, or a profile from `ant auth login`.

## Use

```bash
double-diamond run "Set up CI/CD for this project" --workdir . 
double-diamond run --yes --no-web "Plan the migration from sessions to JWT"   # never ask, no web
double-diamond run "Where can I print my immigration forms in Austin?"       # recommendation path
double-diamond show <run-id> --audit        # the deliberation, on request
double-diamond reopen <run-id> --item <assumption-id> --evidence "the repo is Go"
double-diamond stats                        # questions, go rate, override rate, standing preferences
double-diamond forget <topic-key>
double-diamond eval run ../benchmarks/prompts.jsonl --out results/first-run
```

`--workdir` lets discovery read files under that directory with sandboxed, read-only tools (path escapes, symlink escapes, binaries and large files are refused). Everything a tool or web page returns is treated as data, never instructions.

## Configuration

| Variable | Effect |
|---|---|
| `DD_MODEL` | Use one model for every role. |
| `DD_MODEL_<ROLE>` | Per role: `TRIAGE`, `DIRECT`, `EXPAND`, `RESEARCH`, `LENSES`, `CANDIDATE`, `RUBRIC`, `JUDGE`, `CONTRACT`, `REOPEN`, `BASELINE`, `EVAL_JUDGE`. |
| `DD_EFFORT_<ROLE>` | `low`, `medium`, `high`, `xhigh`, `max`. |
| `DD_FALLBACKS` | `0` disables server-side refusal fallbacks (on by default for models that support them). |
| `DD_STATE_DIR` | Where preferences, events and runs live (default `~/.double-diamond`). |

Defaults: Claude Opus 5.5 for reasoning-heavy roles; Claude Sonnet 5.5 for the cheap triage gate and the parallel candidate generators. The judge runs on a different model than the candidates so it does not grade its own work. Effort is set explicitly per role because defaults differ by model.

## State

```
~/.double-diamond/
  preferences.json   topic -> history of explicit answers
  events.jsonl       questions, outcomes, flips (feeds `stats`)
  runs/<id>.json     brief, audit trail, plan, usage for each run
```

## Tests

```bash
python -m pytest orchestrator/tests -q
```

The suite uses a scripted fake LLM, so every rule and the whole control flow are tested without an API key. `tests/test_llm_requests.py` additionally drives the real Anthropic SDK against a fake HTTP transport and checks the exact request bodies: models, effort, structured-output format, the refusal-fallback beta, tool definitions, and the `pause_turn` and tool-use loops.

**What is not tested:** live calls to the API. No run here has used a real key, so prompt quality, real model behavior, and cost per run are unmeasured. Run the benchmark (`double-diamond eval run`) on your own prompts before relying on it.
