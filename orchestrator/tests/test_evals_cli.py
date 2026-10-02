import json

import pytest

from double_diamond import evals
from double_diamond.ask import AutoAsker
from double_diamond.cli import main
from double_diamond.config import Settings
from double_diamond.models import PairJudgement, PairScores
from double_diamond.pipeline import Pipeline, RunOptions
from double_diamond.store import Store
from fakes import MockLLM, item, brief_draft, run, triage
from test_pipeline import contract_out


def scores(n):
    return PairScores(fit=n, surprises_avoided=n, clarity=n, scope_discipline=n)


def eval_handlers():
    """Pipeline arm answers 'PIPELINE-OUT'; baseline answers 'BASELINE-OUT'. The judge always prefers
    whichever response contains PIPELINE-OUT, wherever it is placed."""

    def judge(user):
        first, second = user.split("### first")[1].split("### second")
        if "PIPELINE-OUT" in first:
            return PairJudgement(first=scores(5), second=scores(2), preferred="first", notes="n1")
        return PairJudgement(first=scores(2), second=scores(5), preferred="second", notes="n2")

    def triage_by_prompt(user):
        small = "small" in user
        return triage(ambiguous=not small, costly=not small)

    return {
        "Triage": triage_by_prompt,
        "BriefDraft": brief_draft([item("lang")]),
        "ContractOutput": lambda user: contract_out(goal="PIPELINE-OUT"),
        "text:direct": "PIPELINE-OUT (direct)",
        "text:baseline": "BASELINE-OUT",
        "PairJudgement": judge,
    }


PROMPTS = [
    evals.EvalPrompt("big", "do a big risky thing", "pipeline", "ambiguous"),
    evals.EvalPrompt("tiny", "a small tweak", "skip", "clear"),
    evals.EvalPrompt("misc", "whatever", "either"),
]


def make(llm, tmp_path):
    return lambda: Pipeline(llm, AutoAsker(), Store(tmp_path / "state"), Settings(), RunOptions(web=False, seed=1))


def test_blind_eval_end_to_end(tmp_path):
    llm = MockLLM(eval_handlers())
    result = run(evals.run_eval(PROMPTS, llm, make(llm, tmp_path), tmp_path / "out", judge="llm", seed=5))
    s = result["summary"]
    assert s["triage_accuracy"] == 1.0  # big -> pipeline, tiny -> skipped
    assert s["pipeline_win_share"] == 1.0  # the judge was run in both orders and agreed regardless of position
    assert s["pipeline_scores"]["fit"] > s["baseline_scores"]["fit"]
    assert (tmp_path / "out" / "report.md").read_text(encoding="utf-8").startswith("# Eval report")

    # blind files never name the arm, and the key maps back correctly
    key = json.loads((tmp_path / "out" / "key.json").read_text(encoding="utf-8"))
    for pid, mapping in key.items():
        blind = (tmp_path / "out" / "blind" / f"{pid}.md").read_text(encoding="utf-8")
        assert "pipeline" not in blind.lower().replace("pipeline-out", "")
        a_text = blind.split("## Response A")[1].split("## Response B")[0]
        assert ("PIPELINE-OUT" in a_text) == mapping.startswith("A=pipeline")


def test_judge_runs_in_both_orders_to_cancel_position_bias(tmp_path):
    llm = MockLLM(eval_handlers())
    run(evals.run_eval(PROMPTS[:1], llm, make(llm, tmp_path), tmp_path / "out", judge="llm", seed=1))
    judge_calls = llm.calls_for("PairJudgement")
    assert len(judge_calls) == 2
    firsts = [c.user.split("### first")[1].split("### second")[0] for c in judge_calls]
    assert ("PIPELINE-OUT" in firsts[0]) != ("PIPELINE-OUT" in firsts[1])


def test_human_mode_writes_template_and_reveal_unblinds(tmp_path):
    llm = MockLLM(eval_handlers())
    run(evals.run_eval(PROMPTS[:2], llm, make(llm, tmp_path), tmp_path / "out", judge="human", seed=2))
    assert not llm.calls_for("PairJudgement")
    template = json.loads((tmp_path / "out" / "scores_template.json").read_text(encoding="utf-8"))
    assert set(template) == {"big", "tiny"}

    key = json.loads((tmp_path / "out" / "key.json").read_text(encoding="utf-8"))
    pipeline_letter = "A" if key["big"].startswith("A=pipeline") else "B"
    other = "B" if pipeline_letter == "A" else "A"
    scores_file = tmp_path / "scores.json"
    scores_file.write_text(json.dumps({"big": {"preferred": pipeline_letter}, "tiny": {"preferred": "tie"}}), encoding="utf-8")
    res = evals.reveal(tmp_path / "out", scores_file)
    assert res["pipeline_wins"] == 1 and res["ties"] == 1 and res["pipeline_win_share"] == 0.75
    scores_file.write_text(json.dumps({"big": {"preferred": other}}), encoding="utf-8")
    assert evals.reveal(tmp_path / "out", scores_file)["baseline_wins"] == 1


def test_reveal_attributes_wins_correctly_in_both_slots(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "key.json").write_text(
        json.dumps({"p1": "A=pipeline,B=baseline", "p2": "A=baseline,B=pipeline", "p3": "A=baseline,B=pipeline", "p4": "A=pipeline,B=baseline"}),
        encoding="utf-8",
    )
    scores = tmp_path / "s.json"
    # p1: A wins -> pipeline.  p2: A wins -> baseline.  p3: B wins -> pipeline.  p4: B wins -> baseline.
    scores.write_text(json.dumps({p: {"preferred": c} for p, c in {"p1": "A", "p2": "A", "p3": "B", "p4": "B"}.items()}), encoding="utf-8")
    res = evals.reveal(out, scores)
    assert res["detail"] == {"p1": "pipeline", "p2": "baseline", "p3": "pipeline", "p4": "baseline"}
    assert res["pipeline_win_share"] == 0.5


def test_wrong_triage_is_reported(tmp_path):
    handlers = eval_handlers()
    handlers["Triage"] = triage(ambiguous=False, costly=False)  # skips everything
    llm = MockLLM(handlers)
    result = run(evals.run_eval(PROMPTS[:1], llm, make(llm, tmp_path), tmp_path / "out", judge="human"))
    assert result["summary"]["triage_accuracy"] == 0.0


def test_load_prompts(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text('# comment\n{"id":"a","prompt":"p","expect":"skip"}\n\n{"id":"b","prompt":"q"}\n', encoding="utf-8")
    items = evals.load_prompts(f)
    assert [(i.id, i.expect) for i in items] == [("a", "skip"), ("b", "either")]
    f.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="bad eval line"):
        evals.load_prompts(f)


# ---- CLI ---------------------------------------------------------------------------------


def cli(args, tmp_path, llm=None, capsys=None):
    lines = []
    code = main(["--state-dir", str(tmp_path / "s"), *args], llm_factory=(lambda s: llm) if llm else None, asker=AutoAsker(), out=lines.append)
    return code, "\n".join(lines)


def test_cli_run_prints_the_plan_and_run_id(tmp_path):
    handlers = eval_handlers()
    handlers["Triage"] = triage()
    llm = MockLLM(handlers)
    code, out = cli(["run", "--yes", "--no-web", "do", "something", "big"], tmp_path, llm)
    assert code == 0 and "## Goal and done-when" in out and "(run " in out


def test_cli_run_json_and_skip(tmp_path):
    llm = MockLLM(eval_handlers())
    code, out = cli(["run", "--yes", "--json", "a", "small", "tweak"], tmp_path, llm)
    data = json.loads(out)
    assert code == 0 and data["skipped"] is True and data["plan"] is None


def test_cli_show_runs_stats_forget(tmp_path):
    handlers = eval_handlers()
    handlers["Triage"] = triage()
    llm = MockLLM(handlers)
    cli(["run", "--yes", "--no-web", "x"], tmp_path, llm)
    store = Store(tmp_path / "s")
    run_id = store.list_runs()[0]
    code, out = cli(["show", run_id, "--audit"], tmp_path)
    assert code == 0 and "## Goal and done-when" in out
    assert cli(["runs"], tmp_path)[1] == run_id
    assert json.loads(cli(["stats"], tmp_path)[1])["runs"] == 1
    store.record_answer("k", "v")
    store.record_answer("k", "v")
    assert cli(["forget", "k"], tmp_path)[1] == "forgotten"


def test_state_dir_works_before_or_after_the_subcommand(tmp_path):
    Store(tmp_path / "a").save_run("r1", {"skipped": True, "answer": "hi"})
    for argv in (["--state-dir", str(tmp_path / "a"), "runs"], ["runs", "--state-dir", str(tmp_path / "a")]):
        lines = []
        assert main(argv, out=lines.append) == 0 and lines == ["r1"]


def test_cli_unknown_run_is_a_clean_error(tmp_path):
    code, out = cli(["show", "nope"], tmp_path)
    assert code == 2 and "no such run" in out


def test_cli_authentication_failure_gives_a_hint(tmp_path):
    from double_diamond.llm import LLMError

    class Broken:
        usage = None

        async def structured(self, *a, **k):
            raise LLMError("triage: 401 authentication_error: invalid x-api-key")

    code, out = cli(["run", "--yes", "x"], tmp_path, Broken())
    assert code == 2 and "ANTHROPIC_API_KEY" in out


def test_cli_refusal_is_reported(tmp_path):
    from double_diamond.llm import RefusedError

    class Refuses:
        usage = None

        async def structured(self, *a, **k):
            raise RefusedError("cyber", "declined")

    code, out = cli(["run", "--yes", "x"], tmp_path, Refuses())
    assert code == 2 and "declined" in out
