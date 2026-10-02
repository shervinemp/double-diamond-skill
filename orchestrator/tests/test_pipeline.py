import json

import pytest

from double_diamond import policy
from double_diamond.ask import YOU_DECIDE, AutoAsker, ScriptedAsker
from double_diamond.config import Settings
from double_diamond.models import (
    Bucket,
    ContractOutput,
    Criterion,
    EvidenceGrade,
    Graft,
    Option,
    ReopenDecision,
    ReopenVerdict,
    Resolution,
    Resolutions,
    Risk,
    RubricDraft,
    Shortlist,
    TaskType,
)
from double_diamond.pipeline import Pipeline, RunOptions
from double_diamond.store import Store
from fakes import (
    MockLLM,
    ask_item,
    brief_draft,
    candidate,
    item,
    labels_to_markers,
    lens_set,
    run,
    triage,
    verdict,
)

MARKERS = ["MARK_ALPHA", "MARK_BETA", "MARK_GAMMA"]
LENSES = ["fastest first result", "lowest upkeep", "least disruption"]


def contract_out(**kw):
    return ContractOutput(
        goal_and_done=kw.get("goal", "Ship it; done when tests pass."),
        steps=kw.get("steps", ["Do step one", "Do step two"]),
        trade_offs=kw.get("trade_offs", ["Rejected X: costs more"]),
        risks=[Risk(risk="Data loss", mitigation="Back up first")],
        open_items=kw.get("open_items", []),
    )


def rubric_draft():
    return RubricDraft(criteria=[Criterion(name="fit_to_goal", weight=60, description="d"), Criterion(name="speed", weight=40, description="d")])


def full_handlers(items, **draft_kw):
    """Handlers for a run that goes through every phase; ALPHA wins."""
    state = {}

    def candidate_for(user):
        for lens, marker in zip(LENSES, MARKERS):
            if f"Lens: {lens}" in user:
                return candidate(marker)
        raise AssertionError("unknown lens in candidate prompt")

    def judge(user):
        mapping = labels_to_markers(user, MARKERS)
        rubric = policy.build_rubric(rubric_draft().criteria)
        totals = {lab: {"MARK_ALPHA": 5, "MARK_BETA": 3, "MARK_GAMMA": 2}[m] for lab, m in mapping.items()}
        alpha = next(l for l, m in mapping.items() if m == "MARK_ALPHA")
        beta = next(l for l, m in mapping.items() if m == "MARK_BETA")
        grafts = [
            Graft(from_label=beta, idea="BETA_GRAFT", fixes_weakness_of_winner="ALPHA has no audit log", compatible=True, compatibility_note="n"),
            Graft(from_label=beta, idea="BETA_VAGUE", fixes_weakness_of_winner="", compatible=True, compatibility_note="n"),
        ]
        state["labels"] = mapping
        return verdict(rubric, totals, grafts=grafts)

    return {
        "Triage": triage(),
        "BriefDraft": brief_draft(items, viable_approaches=["a", "b", "c"], hard_to_reverse=True, **draft_kw),
        "LensSet": lens_set(*LENSES),
        "Candidate": candidate_for,
        "RubricDraft": rubric_draft(),
        "Verdict": judge,
        "ContractOutput": contract_out(),
    }, state


def make_pipeline(llm, tmp_path, asker=None, **opts):
    return Pipeline(llm, asker or AutoAsker(), Store(tmp_path), Settings(), RunOptions(seed=3, web=False, **opts))


# ---- skip -----------------------------------------------------------------------


def test_clear_small_requests_skip_the_pipeline(tmp_path):
    llm = MockLLM({"Triage": triage(ambiguous=False, costly=False), "text:direct": "sorted.sort()"})
    result = run(make_pipeline(llm, tmp_path).run("how do I sort a list"))
    assert result.skipped and result.markdown == "sorted.sort()"
    assert [c.name for c in llm.calls if c.kind == "structured"] == ["Triage"]  # nothing else ran


def test_force_runs_the_pipeline_anyway(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    handlers["Triage"] = triage(ambiguous=False, costly=False)
    llm = MockLLM(handlers)
    result = run(make_pipeline(llm, tmp_path, force=True).run("x"))
    assert not result.skipped


# ---- full flow ---------------------------------------------------------------------


def test_full_pipeline_end_to_end(tmp_path):
    handlers, state = full_handlers([item("lang", default="python"), ask_item("db", topic_key="database")])
    llm = MockLLM(handlers)
    asker = ScriptedAsker({"db": "sqlite"})
    result = run(make_pipeline(llm, tmp_path, asker=asker).run("build the thing"))

    assert not result.skipped
    md = result.markdown
    for heading in ("## Goal and done-when", "## Plan", "## Assumptions I made", "## Not doing", "## Trade-offs accepted", "## Risks"):
        assert heading in md
    assert md.rstrip().endswith("Say **go** to execute, or tell me which assumption to flip.")
    # assumptions come from the harness's ledger, not from the model
    assert "Which lang? → python" in md
    # the answered question is confirmed, not an assumption
    assert "Which db? → sqlite" not in md
    assert len(asker.asked) == 1 and asker.asked[0].id == "db"


def test_candidates_run_per_lens_and_judge_sees_only_anonymous_labels(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    llm = MockLLM(handlers)
    run(make_pipeline(llm, tmp_path).run("x"))

    cand_calls = llm.calls_for("Candidate")
    assert len(cand_calls) == 3
    judge_call = llm.calls_for("Verdict")[0]
    assert len(llm.calls_for("Verdict")) == 1
    for lens in LENSES:
        assert lens not in judge_call.user  # the lens names never reach the judge
    assert all(f"### Candidate {l}" in judge_call.user for l in "ABC")


def test_contractor_sees_only_the_scrubbed_summary(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    llm = MockLLM(handlers)
    run(make_pipeline(llm, tmp_path).run("x"))

    contractor = llm.calls_for("ContractOutput")[0].user
    payload = json.loads(contractor)
    assert "MARK_ALPHA" in contractor  # the chosen approach
    assert "MARK_BETA" not in contractor and "MARK_GAMMA" not in contractor  # raw losers are scrubbed
    assert payload["approved_grafts"] == [{"idea": "BETA_GRAFT", "fixes": "ALPHA has no audit log"}]
    assert "BETA_VAGUE" not in contractor  # graft with no named weakness is rejected
    assert len(payload["rejected_alternatives"]) == 2
    assert "verdict" not in payload and "scores" not in payload


def test_audit_trail_records_deliberation_without_polluting_the_plan(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    llm = MockLLM(handlers)
    result = run(make_pipeline(llm, tmp_path).run("x"))
    assert "MARK_BETA" not in result.markdown
    audit = result.audit_markdown()
    assert "## Lenses" in audit and "## Ranking" in audit and "fastest first result" in audit


def test_phase2_is_skipped_when_one_approach_is_clearly_best(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    handlers["BriefDraft"] = brief_draft([item("lang")], viable_approaches=["only"], hard_to_reverse=True)
    llm = MockLLM(handlers)
    run(make_pipeline(llm, tmp_path).run("x"))
    assert not llm.calls_for("LensSet") and not llm.calls_for("Candidate") and not llm.calls_for("Verdict")


def test_a_failed_candidate_is_dropped_and_two_survivors_still_compare(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    inner = handlers["Candidate"]

    def flaky(user):
        if "Lens: least disruption" in user:
            raise RuntimeError("boom")
        return inner(user)

    handlers["Candidate"] = flaky
    llm = MockLLM(handlers)
    result = run(make_pipeline(llm, tmp_path).run("x"))
    assert llm.calls_for("Verdict") and "## Plan" in result.markdown


def test_too_few_surviving_candidates_falls_back_to_a_single_approach(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    handlers["Candidate"] = lambda user: (_ for _ in ()).throw(RuntimeError("down"))
    llm = MockLLM(handlers)
    result = run(make_pipeline(llm, tmp_path).run("x"))
    assert not llm.calls_for("Verdict") and "## Plan" in result.markdown


def test_close_call_with_a_value_question_is_asked_and_can_flip_the_winner(tmp_path):
    handlers, _ = full_handlers([item("lang")])

    def tied(user):
        mapping = labels_to_markers(user, MARKERS)
        rubric = policy.build_rubric(rubric_draft().criteria)
        totals = {lab: 4 for lab in mapping}
        return verdict(rubric, totals, value_call="Do you value speed over upkeep?", flip="run a benchmark")

    handlers["Verdict"] = tied
    llm = MockLLM(handlers)
    asker = ScriptedAsker()
    run(make_pipeline(llm, tmp_path, asker=asker).run("x"))
    assert [q.id for q in asker.asked] == ["value-call"]
    assert len(asker.asked[0].options) == 2

    # picking the runner-up flips the chosen approach
    llm2 = MockLLM(full_handlers([item("lang")])[0] | {"Verdict": tied})
    first = run(make_pipeline(llm2, tmp_path, asker=ScriptedAsker()).run("x"))
    winner_first = json.loads(llm2.calls_for("ContractOutput")[0].user)["chosen_approach"]["approach"]
    runner_lens = asker.asked[0].options[1]
    llm3 = MockLLM(full_handlers([item("lang")])[0] | {"Verdict": tied})
    run(make_pipeline(llm3, tmp_path, asker=ScriptedAsker({"value-call": runner_lens})).run("x"))
    winner_after = json.loads(llm3.calls_for("ContractOutput")[0].user)["chosen_approach"]["approach"]
    assert winner_after != winner_first
    assert first.markdown  # sanity


def test_non_interactive_close_call_does_not_ask_and_notes_the_open_issue(tmp_path):
    handlers, _ = full_handlers([item("lang")])

    def tied(user):
        mapping = labels_to_markers(user, MARKERS)
        return verdict(policy.build_rubric(rubric_draft().criteria), {lab: 4 for lab in mapping}, value_call="Speed or upkeep?", flip="run a benchmark")

    handlers["Verdict"] = tied
    handlers["ContractOutput"] = lambda user: contract_out()
    llm = MockLLM(handlers)
    result = run(make_pipeline(llm, tmp_path).run("x"))
    payload = json.loads(llm.calls_for("ContractOutput")[0].user)
    assert "run a benchmark" in payload["open_issue"]
    assert "run a benchmark" in result.markdown  # surfaced as an open item


# ---- questions ----------------------------------------------------------------------


def test_obvious_and_cheap_questions_are_never_asked(tmp_path):
    items = [ask_item("a", obvious_to_user=True), ask_item("b", costly_if_wrong=False), item("c")]
    handlers, _ = full_handlers(items)
    asker = ScriptedAsker()
    run(make_pipeline(MockLLM(handlers), tmp_path, asker=asker).run("x"))
    assert asker.asked == []


def test_overridden_answers_become_standing_preferences_after_two_runs(tmp_path):
    store_dir = tmp_path
    for _ in range(2):
        handlers, _ = full_handlers([ask_item("db", topic_key="database")])
        run(make_pipeline(MockLLM(handlers), store_dir, asker=ScriptedAsker({"db": "sqlite"})).run("x"))
    assert Store(store_dir).standing("database") == "sqlite"

    # third run: no question, the standing preference is the default
    handlers, _ = full_handlers([ask_item("db", topic_key="database")])
    asker = ScriptedAsker()
    result = run(make_pipeline(MockLLM(handlers), store_dir, asker=asker).run("x"))
    assert asker.asked == []
    assert "Which db? → sqlite" in result.markdown


def test_accepting_defaults_does_not_create_a_preference_and_you_decide_counts_as_accepting(tmp_path):
    for answer in (None, YOU_DECIDE):
        handlers, _ = full_handlers([ask_item("db", topic_key="database")])
        asker = ScriptedAsker({} if answer is None else {"db": answer})
        run(make_pipeline(MockLLM(handlers), tmp_path, asker=asker).run("x"))
    store = Store(tmp_path)
    assert store.standing("database") is None
    outcomes = [e["outcome"] for e in store.events() if e["kind"] == "question"]
    assert outcomes == ["accepted_default", "you_decide"]


def test_auto_asker_logs_auto_and_stats_ignore_it(tmp_path):
    handlers, _ = full_handlers([ask_item("db")])
    run(make_pipeline(MockLLM(handlers), tmp_path).run("x"))
    assert Store(tmp_path).stats()["questions_asked"] == 0


# ---- discovery -------------------------------------------------------------------------


def test_discoverable_items_are_looked_up_and_become_ledger_entries(tmp_path):
    handlers, _ = full_handlers([item("pm", Bucket.discoverable, default="npm")])
    handlers["research:research"] = "Found pnpm-lock.yaml"
    handlers["Resolutions"] = Resolutions(items=[Resolution(id="pm", answer="pnpm", evidence="pnpm-lock.yaml", confident=True)])
    llm = MockLLM(handlers)
    pipe = Pipeline(llm, AutoAsker(), Store(tmp_path), Settings(), RunOptions(seed=1, web=True))
    result = run(pipe.run("x"))
    assert "Which pm? → pnpm" in result.markdown
    assert llm.calls_for("research:research")


def test_unresolved_discovery_falls_back_to_the_default(tmp_path):
    handlers, _ = full_handlers([item("pm", Bucket.discoverable, default="npm")])
    handlers["research:research"] = "Nothing conclusive"
    handlers["Resolutions"] = Resolutions(items=[Resolution(id="pm", answer="maybe yarn", evidence="", confident=False)])
    pipe = Pipeline(MockLLM(handlers), AutoAsker(), Store(tmp_path), Settings(), RunOptions(seed=1, web=True))
    assert "Which pm? → npm" in run(pipe.run("x")).markdown


def test_no_tools_means_discoverable_items_default_without_a_lookup(tmp_path):
    handlers, _ = full_handlers([item("pm", Bucket.discoverable, default="npm")])
    llm = MockLLM(handlers)  # no research handler: calling it would fail the test
    result = run(make_pipeline(llm, tmp_path).run("x"))  # web=False, no workdir
    assert "Which pm? → npm" in result.markdown


def test_research_failure_degrades_to_defaults(tmp_path):
    from double_diamond.llm import LLMError

    handlers, _ = full_handlers([item("pm", Bucket.discoverable, default="npm")])
    handlers["research:research"] = lambda user: (_ for _ in ()).throw(LLMError("search down"))
    pipe = Pipeline(MockLLM(handlers), AutoAsker(), Store(tmp_path), Settings(), RunOptions(seed=1, web=True))
    assert "Which pm? → npm" in run(pipe.run("x")).markdown


# ---- recommendation ----------------------------------------------------------------------


def opt(name, grade):
    return Option(name=name, price="$12", price_basis="quoted", quality_evidence="ok", trust_evidence="ok",
                  evidence_grade=grade, red_flags=[], disqualifying=False, tradeoff="t", payment_note="card only")


def test_recommendation_path_vets_and_overrides_a_rating_only_pick(tmp_path):
    handlers = {
        "Triage": triage(TaskType.recommendation, money=True),
        "BriefDraft": brief_draft([], sensitive_data=True, restatement="Print immigration forms."),
        "research:research": "notes with urls",
        "Shortlist": Shortlist(
            options=[opt("Slick Prints", EvidenceGrade.platform_rating_only), opt("City Library", EvidenceGrade.verified)],
            recommended="Slick Prints", privacy_note="", user_should_check=[], caveats=[],
        ),
    }
    llm = MockLLM(handlers)
    pipe = Pipeline(llm, AutoAsker(), Store(tmp_path), Settings(), RunOptions(web=True))
    result = run(pipe.run("where can I print my immigration forms"))
    md = result.markdown
    assert "## Shortlist" in md and "**Recommended:** City Library" in md
    assert "platform_rating_only" in md and "personal information" in md
    assert "Check these yourself before paying" in md
    assert not llm.calls_for("LensSet")  # no candidate fan-out on this path


def test_recommendation_without_web_cannot_verify_anything(tmp_path):
    handlers = {
        "Triage": triage(TaskType.recommendation, money=True),
        "BriefDraft": brief_draft([]),
        "Shortlist": Shortlist(options=[opt("A", EvidenceGrade.unverified)], recommended="A", privacy_note="p", user_should_check=["c"], caveats=[]),
    }
    llm = MockLLM(handlers)
    pipe = Pipeline(llm, AutoAsker(), Store(tmp_path), Settings(), RunOptions(web=False))
    run(pipe.run("find me a printer"))
    assert "No web access" in llm.calls_for("Shortlist")[0].user


# ---- reopen ------------------------------------------------------------------------------


def test_reopen_flips_an_assumption_logs_it_and_recontracts(tmp_path):
    handlers, _ = full_handlers([item("lang", default="python")])
    pipe = make_pipeline(MockLLM(handlers), tmp_path)
    first = run(pipe.run("x"))

    handlers2 = {
        "ReopenDecision": ReopenDecision(verdict=ReopenVerdict.flip, new_value="go", reason="repo is Go", changes_approach=True, question_options=[]),
        "ContractOutput": contract_out(goal="Amended goal"),
    }
    llm2 = MockLLM(handlers2)
    pipe2 = Pipeline(llm2, AutoAsker(), Store(tmp_path), Settings(), RunOptions())
    result = run(pipe2.reopen(first.run_id, "lang", "the repo only contains .go files"))
    assert "Which lang? → go" in result.markdown and "Amended goal" in result.markdown
    assert "--force" in result.markdown  # approach may need re-comparing
    assert [e for e in Store(tmp_path).events() if e["kind"] == "flip"]
    assert Store(tmp_path).load_run(first.run_id)["revision"] == 2


def test_reopen_leaves_a_valid_assumption_alone(tmp_path):
    handlers, _ = full_handlers([item("lang", default="python")])
    first = run(make_pipeline(MockLLM(handlers), tmp_path).run("x"))
    llm2 = MockLLM({"ReopenDecision": ReopenDecision(verdict=ReopenVerdict.still_valid, new_value="", reason="still fine", changes_approach=False, question_options=[])})
    result = run(Pipeline(llm2, AutoAsker(), Store(tmp_path), Settings(), RunOptions()).reopen(first.run_id, "lang", "weak hint"))
    assert "stands" in result.markdown
    assert not llm2.calls_for("ContractOutput")


def test_reopen_unknown_item_lists_what_exists(tmp_path):
    handlers, _ = full_handlers([item("lang")])
    first = run(make_pipeline(MockLLM(handlers), tmp_path).run("x"))
    with pytest.raises(KeyError, match="lang"):
        run(Pipeline(MockLLM({}), AutoAsker(), Store(tmp_path), Settings(), RunOptions()).reopen(first.run_id, "nope", "e"))
