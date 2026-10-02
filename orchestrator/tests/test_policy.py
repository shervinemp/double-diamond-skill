import random

from double_diamond import policy
from double_diamond.models import (
    Bucket,
    Confidence,
    Criterion,
    EvidenceGrade,
    Graft,
    Option,
    Shortlist,
    WorkItem,
)
from fakes import ask_item, brief, candidate, item, triage, verdict


def work(di) -> WorkItem:
    return WorkItem(**di.model_dump())


def no_standing(_key):
    return None


# ---- triage ---------------------------------------------------------------


def test_triage_needs_both_gates():
    assert policy.should_run_pipeline(triage(ambiguous=True, costly=True))
    assert not policy.should_run_pipeline(triage(ambiguous=False, costly=True))
    assert not policy.should_run_pipeline(triage(ambiguous=True, costly=False))


def test_triage_money_counts_as_costly_and_force_overrides():
    assert policy.should_run_pipeline(triage(ambiguous=True, costly=False, money=True))
    assert policy.should_run_pipeline(triage(ambiguous=False, costly=False), force=True)


# ---- ask tests ------------------------------------------------------------


def test_ask_item_that_passes_all_tests_stays_ask():
    items, notes = policy.enforce_buckets([work(ask_item())], no_standing)
    assert items[0].bucket == Bucket.ask and notes == []


def test_each_failed_test_demotes_to_default():
    for field in ("changes_plan", "toss_up", "costly_if_wrong"):
        items, notes = policy.enforce_buckets([work(ask_item(**{field: False}))], no_standing)
        assert items[0].bucket == Bucket.default
        assert items[0].source == "defaulted (failed ask test)"
        assert notes


def test_obvious_questions_are_demoted():
    items, _ = policy.enforce_buckets([work(ask_item(obvious_to_user=True))], no_standing)
    assert items[0].bucket == Bucket.default


def test_question_cap_keeps_the_highest_regret():
    asks = [work(ask_item(f"q{i}", regret=i)) for i in range(1, 6)]
    items, notes = policy.enforce_buckets(asks, no_standing, ask_cap=3)
    kept = {i.id for i in items if i.bucket == Bucket.ask}
    assert kept == {"q5", "q4", "q3"}
    assert sum("question cap" in n for n in notes) == 2


def test_standing_preference_replaces_a_question():
    items, notes = policy.enforce_buckets(
        [work(ask_item("db", topic_key="database"))], lambda k: "sqlite" if k == "database" else None
    )
    assert items[0].bucket == Bucket.default and items[0].default == "sqlite"
    assert items[0].source == "standing preference" and notes


def test_missing_default_is_filled():
    di = item("x", Bucket.default, default="  ", options=["first", "second"])
    items, _ = policy.enforce_buckets([work(di)], no_standing)
    assert items[0].default == "first"
    di2 = item("y", Bucket.default, default="")
    assert policy.enforce_buckets([work(di2)], no_standing)[0][0].default == policy.DEFAULT_PLACEHOLDER


def test_irrelevant_items_are_left_alone():
    items, _ = policy.enforce_buckets([work(item("z", Bucket.irrelevant, default=""))], no_standing)
    assert items[0].bucket == Bucket.irrelevant


def test_should_diverge_needs_two_approaches_and_a_reason():
    assert not policy.should_diverge(brief(viable_approaches=["a"], hard_to_reverse=True))
    assert not policy.should_diverge(brief(viable_approaches=["a", "b"]))
    assert policy.should_diverge(brief(viable_approaches=["a", "b"], hard_to_reverse=True))
    assert policy.should_diverge(brief(viable_approaches=["a", "b"], tradeoff_not_obvious=True))


# ---- anonymization ---------------------------------------------------------


def test_anonymize_is_a_reversible_shuffle():
    cands = [candidate(f"M{i}") for i in range(4)]
    anon = policy.anonymize(cands, random.Random(1))
    assert anon.labels == ["A", "B", "C", "D"]
    assert sorted(anon.origin.values()) == [0, 1, 2, 3]
    for label, idx in anon.origin.items():
        assert anon.candidates[label] is cands[idx]


def test_anonymize_is_seed_deterministic_and_actually_shuffles():
    cands = [candidate(f"M{i}") for i in range(4)]
    a = policy.anonymize(cands, random.Random(7)).origin
    assert a == policy.anonymize(cands, random.Random(7)).origin
    orders = {tuple(policy.anonymize(cands, random.Random(s)).origin.values()) for s in range(20)}
    assert len(orders) > 1


# ---- rubric ----------------------------------------------------------------


def test_rubric_always_contains_the_baseline_at_or_above_floor():
    rubric = policy.build_rubric([Criterion(name="Fit To Goal", weight=60, description="d"), Criterion(name="speed", weight=40, description="d")])
    names = {c.name for c in rubric}
    assert set(policy.BASELINE_CRITERIA) <= names
    assert {"fit_to_goal", "speed"} <= names
    for c in rubric:
        if c.name in policy.BASELINE_CRITERIA:
            assert c.weight >= round(policy.BASELINE_FLOOR)
    assert abs(sum(c.weight for c in rubric) - 100) <= 2


def test_rubric_drops_nonpositive_weights_and_handles_empty_draft():
    rubric = policy.build_rubric([Criterion(name="bad", weight=0, description="d"), Criterion(name="neg", weight=-5, description="d")])
    assert {c.name for c in rubric} == set(policy.BASELINE_CRITERIA)
    assert abs(sum(c.weight for c in rubric) - 100) <= 2


def test_user_priority_on_a_baseline_criterion_is_kept():
    rubric = policy.build_rubric([Criterion(name="Cost and Value", weight=30, description="d"), Criterion(name="x", weight=70, description="d")])
    cost = next(c for c in rubric if c.name == "cost_and_value")
    assert cost.weight >= 30


# ---- ranking ---------------------------------------------------------------


def make_rubric():
    return policy.build_rubric([Criterion(name="fit", weight=100, description="d")])


def test_ranking_computes_totals_in_code_not_from_the_judge():
    rubric = make_rubric()
    v = verdict(rubric, {"A": 2, "B": 5, "C": 3}, winner="A")  # judge names the wrong winner
    r = policy.rank_candidates(v, rubric)
    assert r.winner == "B" and r.judge_winner_differs
    assert r.ranked[0].total == 100.0


def test_disqualified_candidates_cannot_win():
    rubric = make_rubric()
    r = policy.rank_candidates(verdict(rubric, {"A": 5, "B": 3}, dq={"A"}), rubric)
    assert r.winner == "B"


def test_all_disqualified_means_no_winner():
    rubric = make_rubric()
    r = policy.rank_candidates(verdict(rubric, {"A": 5, "B": 3}, dq={"A", "B"}), rubric)
    assert r.winner is None and r.confidence == Confidence.low


def test_close_margin_caps_confidence_and_asks_for_evidence():
    rubric = make_rubric()
    r = policy.rank_candidates(verdict(rubric, {"A": 4, "B": 4}, confidence=Confidence.high), rubric)
    assert r.margin == 0 and r.confidence == Confidence.low and r.needs_evidence
    clear = policy.rank_candidates(verdict(rubric, {"A": 5, "B": 2}, confidence=Confidence.high), rubric)
    assert clear.confidence == Confidence.high and not clear.needs_evidence


def test_a_modest_judge_confidence_is_not_inflated_by_a_wide_margin():
    rubric = make_rubric()
    r = policy.rank_candidates(verdict(rubric, {"A": 5, "B": 1}, confidence=Confidence.medium), rubric)
    assert r.confidence == Confidence.medium


def test_missing_criterion_scores_are_flagged_not_hidden():
    rubric = make_rubric()
    v = verdict(rubric, {"A": 5, "B": 5})
    v.scores[0].criteria_scores = v.scores[0].criteria_scores[:2]
    r = policy.rank_candidates(v, rubric)
    assert any(x.missing for x in r.ranked)


# ---- grafts ----------------------------------------------------------------


def g(frm="B", fixes="fixes the slow deploys", compatible=True):
    return Graft(from_label=frm, idea=f"idea from {frm}", fixes_weakness_of_winner=fixes, compatible=compatible, compatibility_note="n")


def test_graft_gate():
    rubric = make_rubric()
    v = verdict(rubric, {"A": 5, "B": 3}, grafts=[g(), g("B", fixes="  "), g("B", compatible=False), g("A"), g("C"), g("D")])
    approved = policy.approve_grafts(v, "A", max_grafts=2)
    assert [x.from_label for x in approved] == ["B", "C"]  # empty-fix, incompatible, and own-label grafts rejected; capped at 2


def test_rejected_lines_are_one_per_loser():
    rubric = make_rubric()
    v = verdict(rubric, {"A": 5, "B": 2, "C": 4}, dq={"C"})
    ranking = policy.rank_candidates(v, rubric)
    lines = policy.rejected_one_liners(ranking, {"A": "speed", "B": "upkeep", "C": "novelty"}, v, rubric)
    assert len(lines) == 2
    assert any(l.startswith("upkeep") for l in lines)
    assert any(l.startswith("novelty: disqualified") for l in lines)


# ---- scrubbing -------------------------------------------------------------


def test_contractor_payload_has_no_raw_candidates_or_scores():
    b = brief()
    payload = policy.contractor_payload(b, candidate("WIN"), [], ["upkeep: weak on cost"], None, "note")
    text = str(payload)
    assert "WIN" in text and "upkeep" in text
    assert "scores" not in payload and "candidates" not in payload and "verdict" not in payload


# ---- recommendations ---------------------------------------------------------


def opt(name, grade, **kw):
    return Option(
        name=name, price="$10", price_basis="quoted", quality_evidence="q", trust_evidence="t",
        evidence_grade=grade, red_flags=kw.get("red_flags", []), disqualifying=kw.get("disqualifying", False),
        tradeoff="t", payment_note="",
    )


def shortlist(options, recommended, **kw):
    return Shortlist(options=options, recommended=recommended, privacy_note=kw.get("privacy", ""),
                     user_should_check=kw.get("check", []), caveats=[])


def test_rating_only_option_cannot_be_the_top_pick_over_better_evidence():
    sl = shortlist([opt("Slick Prints", EvidenceGrade.platform_rating_only), opt("Library", EvidenceGrade.verified)], "Slick Prints")
    out = policy.enforce_shortlist(sl, sensitive=True)
    assert out.recommended == "Library"
    assert any("changed from Slick Prints" in c for c in out.caveats)


def test_disqualified_pick_is_replaced_and_no_viable_option_means_no_pick():
    sl = shortlist([opt("Scam", EvidenceGrade.verified, disqualifying=True), opt("Ok", EvidenceGrade.corroborated)], "Scam")
    assert policy.enforce_shortlist(sl, sensitive=False).recommended == "Ok"
    none = shortlist([opt("Scam", EvidenceGrade.verified, disqualifying=True)], "Scam")
    out = policy.enforce_shortlist(none, sensitive=False)
    assert out.recommended == "" and any("none is recommended" in c for c in out.caveats)


def test_privacy_note_and_self_check_are_always_present():
    out = policy.enforce_shortlist(shortlist([opt("A", EvidenceGrade.verified)], "A"), sensitive=True)
    assert "personal information" in out.privacy_note
    assert out.user_should_check == [policy.DEFAULT_CHECK]


def test_a_well_evidenced_pick_is_left_alone():
    sl = shortlist([opt("A", EvidenceGrade.verified), opt("B", EvidenceGrade.corroborated)], "A", privacy="Hands over passport copies.", check=["call them"])
    out = policy.enforce_shortlist(sl, sensitive=True)
    assert out.recommended == "A" and out.caveats == [] and out.user_should_check == ["call them"]
