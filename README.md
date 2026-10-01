# double-diamond-skill

An expand-then-contract request handler for Claude Code, based on the Double Diamond idea: surface what the request leaves unsaid, decide it cheaply where possible, compare real alternatives where the choice matters, then deliver one plan.

## What's here

| Path | Role |
|---|---|
| `.claude/skills/double-diamond/SKILL.md` | The pipeline playbook. Explicit trigger only (`/double-diamond <request>`). Runs in the live session so Phase 1 can be a real conversation. |
| `.claude/agents/dd-candidate.md` | Read-only subagent. Develops one approach under an assigned lens. Spawned in parallel, so each gets a fresh context. |
| `.claude/agents/dd-judge.md` | Read-only subagent on a stronger model. Scores anonymized, shuffled candidates and proposes compatible grafts. |
| `snippets/CLAUDE.disposition.md` | The always-on slice (ask vs. default). Paste into a CLAUDE.md if you want it everywhere. |
| `evals/prompts.md` | Blind A/B protocol and a starter prompt set. |

## Pipeline

```
Triage ─► Phase 1: Expand & ground ─► Phase 2: Diverge & compare ─► Phase 3: Contract ─► (execute)
 gate      discover / default / ask       lenses → candidates → judge     scrub + pre-mortem    re-expand on
           assumption ledger + Brief      (only if hard to reverse)        one canonical plan    broken assumption
```

Design choices, and where they depart from the original Gemini write-up:

- **Four buckets, not two.** Discoverable / low-regret / ask / irrelevant. Look before asking.
- **A silent baseline covers the obvious.** Price, quality, legitimacy, time, privacy, recourse, and the task's own rules are applied to every request without being asked. They surface only on a conflict with something the user said, or a real concern.
- **Recommendation tasks get their own path.** For picking a vendor, service, or place, the pipeline gathers options from several independent sources, vets them adversarially (does it exist, do the reviews hold up, is the payment safe), and grades the evidence instead of trusting star ratings.
- **Questions are rare by design.** A question must pass three tests (it changes the plan, it is a real toss-up, a wrong guess is costly) plus an obviousness check. The target is 0 or 1 per request. Everything else becomes a stated assumption, which the user can flip at the plan checkpoint.
- **Lenses come from the forks**, not from fixed MVP/Enterprise/Bleeding-edge archetypes.
- **Phase 2 is conditional.** Phases 1 and 3 carry most of the value; the tournament runs only when the choice is hard to reverse and has several viable options.
- **Judging is anonymized and shuffled,** uses a rubric weighted from the user's own constraints, and runs on a different model tier than the generators. Evidence (a spike, a command) beats opinion when the top two are close.
- **Crossover is gated.** Each graft must name the winner's failure mode it fixes and pass a compatibility check. No unnamed additions.
- **The assumption ledger outlives planning.** If execution contradicts an assumption, stop and re-open that one fork.
- **No auto-router.** Explicit trigger first; add routing once there is data.

## Install

Clone the repo, then copy the skill and the two agents into the project you want to use them in (`.claude/` there), or into `~/.claude/` to use them everywhere:

```bash
git clone https://github.com/shervinemp/double-diamond-skill.git
cd double-diamond-skill
mkdir -p ~/.claude/skills ~/.claude/agents
cp -r .claude/skills/double-diamond ~/.claude/skills/
cp .claude/agents/dd-*.md ~/.claude/agents/
```

Restart your Claude Code session so the skill and agents are picked up.

## Using it

```
/double-diamond migrate our auth from sessions to JWT
```

You can also just run Claude Code inside a clone of this repo; the skill and agents load from its `.claude/` folder.

The skill only runs when you invoke it. For the always-on slice (the ask-vs-default rule and the obvious-requirements baseline), paste `snippets/CLAUDE.disposition.md` into a CLAUDE.md.

Tuning knobs, all in plain text: the `model:` lines in the two agent files, the question and candidate caps in `SKILL.md`, and the regret heuristics in Phase 1 step 3.

## Evaluating it

See `evals/prompts.md`. Without a blind comparison there is no way to tell whether this helps or just feels thorough.
