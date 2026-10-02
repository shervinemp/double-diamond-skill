"""Blind A/B evaluation: plain model call versus the pipeline.

Each prompt is run both ways. The two outputs are shuffled behind neutral
labels so a reviewer (a judge model, or you) cannot tell which is which. An
LLM judge scores each pair twice with the order swapped to cancel position bias.
Triage accuracy, tokens and wall-clock time are recorded alongside quality.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import prompts
from .llm import LLM
from .models import PairJudgement, PairScores
from .pipeline import Pipeline

CRITERIA = ("fit", "surprises_avoided", "clarity", "scope_discipline")


@dataclass
class EvalPrompt:
    id: str
    prompt: str
    expect: str  # "pipeline", "skip" or "either"
    kind: str = ""


@dataclass
class Arm:
    output: str
    seconds: float
    tokens: int
    skipped: bool | None = None


def load_prompts(path: str | Path) -> list[EvalPrompt]:
    out: list[EvalPrompt] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
            out.append(EvalPrompt(d["id"], d["prompt"], d.get("expect", "either"), d.get("kind", "")))
        except (ValueError, KeyError) as e:
            raise ValueError(f"{path}:{n}: bad eval line ({e})") from e
    return out


def _tokens(llm: LLM) -> int:
    t = llm.usage.total()
    return t["input_tokens"] + t["output_tokens"]


async def run_baseline(llm: LLM, prompt: str) -> Arm:
    start, before = time.perf_counter(), _tokens(llm)
    text = await llm.text("baseline", prompts.BASELINE, prompt)
    return Arm(text, time.perf_counter() - start, _tokens(llm) - before)


async def run_pipeline_arm(llm: LLM, make_pipeline: Callable[[], Pipeline], prompt: str) -> Arm:
    start, before = time.perf_counter(), _tokens(llm)
    result = await make_pipeline().run(prompt)
    return Arm(result.markdown, time.perf_counter() - start, _tokens(llm) - before, result.skipped)


def _scores_mean(s: PairScores) -> float:
    return (s.fit + s.surprises_avoided + s.clarity + s.scope_discipline) / 4.0


async def judge_pair(llm: LLM, request: str, first: str, second: str) -> PairJudgement:
    user = f"Request:\n{request}\n\n### first\n{first}\n\n### second\n{second}"
    return await llm.structured("eval_judge", prompts.EVAL_JUDGE, user, PairJudgement)


async def run_eval(
    items: list[EvalPrompt],
    llm: LLM,
    make_pipeline: Callable[[], Pipeline],
    out_dir: str | Path,
    *,
    judge: str = "llm",
    seed: int = 0,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    out = Path(out_dir)
    (out / "blind").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    key: dict[str, str] = {}

    for item in items:
        log(f"{item.id}: baseline")
        base = await run_baseline(llm, item.prompt)
        log(f"{item.id}: pipeline")
        pipe = await run_pipeline_arm(llm, make_pipeline, item.prompt)

        pipeline_first = rng.random() < 0.5
        a, b = (pipe, base) if pipeline_first else (base, pipe)
        key[item.id] = "A=pipeline,B=baseline" if pipeline_first else "A=baseline,B=pipeline"
        (out / "blind" / f"{item.id}.md").write_text(
            f"# {item.id}\n\n## Request\n{item.prompt}\n\n## Response A\n{a.output}\n\n## Response B\n{b.output}\n",
            encoding="utf-8",
        )

        triage_ok = None
        if item.expect in ("pipeline", "skip") and pipe.skipped is not None:
            triage_ok = (item.expect == "skip") == pipe.skipped

        row: dict[str, Any] = {
            "id": item.id,
            "kind": item.kind,
            "expect": item.expect,
            "pipeline_skipped": pipe.skipped,
            "triage_correct": triage_ok,
            "pipeline": {"seconds": round(pipe.seconds, 1), "tokens": pipe.tokens},
            "baseline": {"seconds": round(base.seconds, 1), "tokens": base.tokens},
        }
        if judge == "llm":
            log(f"{item.id}: judging (both orders)")
            j1 = await judge_pair(llm, item.prompt, pipe.output, base.output)
            j2 = await judge_pair(llm, item.prompt, base.output, pipe.output)
            row["scores"] = {
                "pipeline": {c: (getattr(j1.first, c) + getattr(j2.second, c)) / 2 for c in CRITERIA},
                "baseline": {c: (getattr(j1.second, c) + getattr(j2.first, c)) / 2 for c in CRITERIA},
            }
            wins = 0.0
            wins += {"first": 1.0, "tie": 0.5}.get(j1.preferred.strip().lower(), 0.0)
            wins += {"second": 1.0, "tie": 0.5}.get(j2.preferred.strip().lower(), 0.0)
            row["pipeline_win_share"] = wins / 2
            row["notes"] = [j1.notes, j2.notes]
        rows.append(row)

    (out / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    summary = summarize(rows)
    result = {"rows": rows, "summary": summary, "judge": judge, "seed": seed}
    (out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_report(result), encoding="utf-8")
    if judge == "human":
        template = {i.id: {"preferred": ""} for i in items}
        (out / "scores_template.json").write_text(json.dumps(template, indent=2), encoding="utf-8")
    return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    judged = [r for r in rows if "scores" in r]
    triage = [r["triage_correct"] for r in rows if r.get("triage_correct") is not None]
    ran = [r for r in rows if r.get("pipeline_skipped") is False]
    summary: dict[str, Any] = {
        "prompts": len(rows),
        "triage_accuracy": mean([1.0 if t else 0.0 for t in triage]),
        "pipeline_runs": len(ran),
        "avg_tokens_pipeline": mean([r["pipeline"]["tokens"] for r in rows]),
        "avg_tokens_baseline": mean([r["baseline"]["tokens"] for r in rows]),
        "avg_seconds_pipeline": mean([r["pipeline"]["seconds"] for r in rows]),
        "avg_seconds_baseline": mean([r["baseline"]["seconds"] for r in rows]),
    }
    if judged:
        summary["pipeline_win_share"] = mean([r["pipeline_win_share"] for r in judged])
        for system in ("pipeline", "baseline"):
            summary[f"{system}_scores"] = {
                c: mean([r["scores"][system][c] for r in judged]) for c in CRITERIA
            }
        ambiguous = [r for r in judged if r["expect"] == "pipeline"]
        if ambiguous:
            summary["pipeline_win_share_on_expected_pipeline_prompts"] = mean(
                [r["pipeline_win_share"] for r in ambiguous]
            )
    return summary


def render_report(result: dict[str, Any]) -> str:
    s = result["summary"]
    lines = ["# Eval report", "", f"Judge: {result['judge']} · seed {result['seed']} · prompts {s['prompts']}", ""]
    lines += [
        "| Metric | Value |",
        "|---|---|",
        f"| Triage accuracy (where an expectation was set) | {s.get('triage_accuracy')} |",
        f"| Pipeline win share (0.5 = tie) | {s.get('pipeline_win_share', 'n/a')} |",
        f"| Win share on prompts expected to need the pipeline | {s.get('pipeline_win_share_on_expected_pipeline_prompts', 'n/a')} |",
        f"| Avg tokens, pipeline vs baseline | {s['avg_tokens_pipeline']} vs {s['avg_tokens_baseline']} |",
        f"| Avg seconds, pipeline vs baseline | {s['avg_seconds_pipeline']} vs {s['avg_seconds_baseline']} |",
        "",
    ]
    if "pipeline_scores" in s:
        lines += ["| Criterion | Pipeline | Baseline |", "|---|---|---|"]
        for c in CRITERIA:
            lines.append(f"| {c} | {s['pipeline_scores'][c]} | {s['baseline_scores'][c]} |")
        lines.append("")
    lines += ["## Per prompt", "", "| Prompt | Expected | Pipeline ran | Triage ok | Win share |", "|---|---|---|---|---|"]
    for r in result["rows"]:
        ran = "no" if r["pipeline_skipped"] else "yes"
        lines.append(
            f"| {r['id']} | {r['expect']} | {ran} | {r['triage_correct']} | {r.get('pipeline_win_share', 'n/a')} |"
        )
    return "\n".join(lines) + "\n"


def reveal(out_dir: str | Path, scores_path: str | Path) -> dict[str, Any]:
    """Unblind human scores: {id: {"preferred": "A"|"B"|"tie"}} against key.json."""
    key = json.loads((Path(out_dir) / "key.json").read_text(encoding="utf-8"))
    scores = json.loads(Path(scores_path).read_text(encoding="utf-8"))
    pipeline_wins = baseline_wins = ties = 0
    detail: dict[str, str] = {}
    for pid, mapping in key.items():
        choice = str(scores.get(pid, {}).get("preferred", "")).strip().upper()
        if choice not in ("A", "B", "TIE"):
            continue
        if choice == "TIE":
            ties += 1
            detail[pid] = "tie"
            continue
        winner = "pipeline" if f"{choice}=pipeline" in mapping else "baseline"
        detail[pid] = winner
        pipeline_wins += winner == "pipeline"
        baseline_wins += winner == "baseline"
    decided = pipeline_wins + baseline_wins + ties
    share = (pipeline_wins + 0.5 * ties) / decided if decided else None
    return {
        "pipeline_wins": pipeline_wins,
        "baseline_wins": baseline_wins,
        "ties": ties,
        "pipeline_win_share": round(share, 3) if share is not None else None,
        "detail": detail,
    }
