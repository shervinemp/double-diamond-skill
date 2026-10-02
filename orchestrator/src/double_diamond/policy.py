"""Deterministic enforcement of the design.

The model proposes; this module decides. Everything here is plain code so the
rules (ask tests, question cap, baseline criteria, scoring, graft gate,
scrubbing) hold regardless of what a model says, and can be unit tested.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable

from .models import (
    Brief,
    Bucket,
    Candidate,
    Confidence,
    Criterion,
    EvidenceGrade,
    Graft,
    Shortlist,
    Triage,
    Verdict,
    WorkItem,
)

BASELINE_CRITERIA = (
    "cost_and_value",
    "quality",
    "legitimacy_and_trust",
    "time_and_convenience",
    "safety_privacy_recourse",
    "rules_of_the_task",
)
BASELINE_DESCRIPTIONS = {
    "cost_and_value": "Real cost and value for money, compared rather than assumed.",
    "quality": "Quality of the result, and how well that is evidenced.",
    "legitimacy_and_trust": "Real, accountable and corroborated; platform ratings alone are weak evidence.",
    "time_and_convenience": "Time to result and effort for the user.",
    "safety_privacy_recourse": "Safety, privacy of what is handed over, and recourse if it goes wrong.",
    "rules_of_the_task": "Official or domain requirements of the task itself.",
}
BASELINE_FLOOR = 4.0

CLOSE_MARGIN = 5.0
CLEAR_MARGIN = 12.0
EVIDENCE_MARGIN = 8.0

DEFAULT_PLACEHOLDER = "the conventional default"


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def norm_label(label: str) -> str:
    return label.strip().upper()[:1]


# --------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------


def should_run_pipeline(triage: Triage, force: bool = False) -> bool:
    """Both gates must hold: ambiguity AND cost of being wrong (money or sensitive
    documents count as costly). ``force`` overrides."""
    if force:
        return True
    costly = triage.costly_if_wrong or triage.money_or_sensitive_data
    return triage.ambiguous_or_multiple_approaches and costly


# --------------------------------------------------------------------------
# Phase 1: buckets and the ask tests
# --------------------------------------------------------------------------


def passes_ask_tests(item: WorkItem) -> bool:
    return item.changes_plan and item.toss_up and item.costly_if_wrong and not item.obvious_to_user


def enforce_buckets(
    items: list[WorkItem],
    standing: Callable[[str], str | None],
    ask_cap: int = 3,
) -> tuple[list[WorkItem], list[str]]:
    """Apply standing preferences, the three ask tests, the obviousness check,
    the question cap, and make sure every default item has a default.

    Returns (items, notes) where notes explain each demotion for the audit trail.
    """
    notes: list[str] = []
    out: list[WorkItem] = []
    for original in items:
        item = original.model_copy()
        if item.bucket == Bucket.irrelevant:
            out.append(item)
            continue

        pref = standing(item.topic_key) if item.topic_key else None
        if pref is not None:
            item.bucket = Bucket.default
            item.default = pref
            item.source = "standing preference"
            notes.append(f"{item.id}: applied standing preference '{pref}'")
            out.append(item)
            continue

        if item.bucket == Bucket.ask and not passes_ask_tests(item):
            failed = [
                label
                for label, ok in (
                    ("changes the plan", item.changes_plan),
                    ("genuine toss-up", item.toss_up),
                    ("costly if wrong", item.costly_if_wrong),
                    ("not obvious", not item.obvious_to_user),
                )
                if not ok
            ]
            item.bucket = Bucket.default
            item.source = "defaulted (failed ask test)"
            notes.append(f"{item.id}: demoted to default; failed: {', '.join(failed)}")
        out.append(item)

    asks = sorted((i for i in out if i.bucket == Bucket.ask), key=lambda i: -i.regret)
    for extra in asks[ask_cap:]:
        extra.bucket = Bucket.default
        extra.source = "defaulted (question cap)"
        notes.append(f"{extra.id}: demoted to default; over the {ask_cap}-question cap")

    for item in out:
        if item.bucket in (Bucket.default, Bucket.ask) and not item.default.strip():
            item.default = item.options[0] if item.options else DEFAULT_PLACEHOLDER
    return out, notes


def should_diverge(brief: Brief) -> bool:
    """Phase 2 runs only for >=2 genuinely distinct approaches where the choice
    is hard to reverse or the trade-off is not obvious."""
    return len(brief.viable_approaches) >= 2 and (brief.hard_to_reverse or brief.tradeoff_not_obvious)


# --------------------------------------------------------------------------
# Phase 2: anonymization, rubric, scoring, grafts
# --------------------------------------------------------------------------


@dataclass
class AnonymizedSet:
    labels: list[str]
    candidates: dict[str, Candidate]  # label -> candidate content (no lens)
    origin: dict[str, int]  # label -> index into the original (lens-ordered) list


def anonymize(candidates: list[Candidate], rng: random.Random) -> AnonymizedSet:
    order = list(range(len(candidates)))
    rng.shuffle(order)
    labels = [chr(ord("A") + i) for i in range(len(candidates))]
    return AnonymizedSet(
        labels=labels,
        candidates={lab: candidates[idx] for lab, idx in zip(labels, order)},
        origin=dict(zip(labels, order)),
    )


def build_rubric(draft: list[Criterion]) -> list[Criterion]:
    """Always include the universal baseline criteria (at least a floor weight each),
    drop non-positive weights, and normalize to sum to 100."""
    weights: dict[str, float] = {}
    descriptions: dict[str, str] = {}
    for c in draft:
        key = norm(c.name)
        if not key or c.weight <= 0:
            continue
        weights[key] = weights.get(key, 0.0) + float(c.weight)
        descriptions.setdefault(key, c.description)
    for b in BASELINE_CRITERIA:
        weights[b] = max(weights.get(b, 0.0), BASELINE_FLOOR)
        descriptions.setdefault(b, BASELINE_DESCRIPTIONS[b])

    base_total = sum(weights[b] for b in BASELINE_CRITERIA)
    others = {k: w for k, w in weights.items() if k not in BASELINE_CRITERIA}
    other_total = sum(others.values())
    budget = 100.0 - base_total
    if others and budget > 0:
        scaled = {k: w * budget / other_total for k, w in others.items()}
        final = {**{b: weights[b] for b in BASELINE_CRITERIA}, **scaled}
    else:
        total = sum(weights.values())
        final = {k: w * 100.0 / total for k, w in weights.items()}
    return [
        Criterion(name=k, weight=round(w), description=descriptions[k])
        for k, w in final.items()
    ]


@dataclass
class Ranked:
    label: str
    total: float
    disqualified: bool
    missing: list[str]


@dataclass
class Ranking:
    ranked: list[Ranked]
    winner: str | None
    runner_up: str | None
    margin: float
    confidence: Confidence
    needs_evidence: bool
    judge_winner_differs: bool


def weighted_total(score_rows: dict[str, int], rubric: list[Criterion]) -> tuple[float, list[str]]:
    total = 0.0
    weight_sum = sum(c.weight for c in rubric) or 1
    missing: list[str] = []
    for c in rubric:
        s = score_rows.get(norm(c.name))
        if s is None:
            missing.append(c.name)
            continue
        total += c.weight * (min(5, max(1, s)) / 5.0)
    return total * 100.0 / weight_sum, missing


def rank_candidates(verdict: Verdict, rubric: list[Criterion]) -> Ranking:
    """Compute totals in code (never trust the judge's arithmetic), exclude
    disqualified candidates, and derive margin and confidence."""
    ranked: list[Ranked] = []
    for cs in verdict.scores:
        rows = {norm(x.criterion): x.score for x in cs.criteria_scores}
        total, missing = weighted_total(rows, rubric)
        ranked.append(Ranked(norm_label(cs.label), round(total, 1), cs.disqualified, missing))
    ranked.sort(key=lambda r: (r.disqualified, -r.total))

    viable = [r for r in ranked if not r.disqualified]
    if not viable:
        return Ranking(ranked, None, None, 0.0, Confidence.low, True, False)

    winner = viable[0]
    runner = viable[1] if len(viable) > 1 else None
    margin = round(winner.total - (runner.total if runner else 0.0), 1)
    margin_conf = (
        Confidence.low if margin < CLOSE_MARGIN else Confidence.medium if margin < CLEAR_MARGIN else Confidence.high
    )
    order = [Confidence.low, Confidence.medium, Confidence.high]
    confidence = min(verdict.confidence, margin_conf, key=order.index) if runner else verdict.confidence
    return Ranking(
        ranked=ranked,
        winner=winner.label,
        runner_up=runner.label if runner else None,
        margin=margin,
        confidence=confidence,
        needs_evidence=runner is not None and margin < EVIDENCE_MARGIN,
        judge_winner_differs=norm_label(verdict.winner_label) != winner.label,
    )


def approve_grafts(verdict: Verdict, winner: str, max_grafts: int = 2) -> list[Graft]:
    """A graft must come from a loser, be judged compatible, and name the specific
    weakness of the winner it fixes. Anything else is scope creep."""
    approved: list[Graft] = []
    for g in verdict.grafts:
        if len(approved) >= max_grafts:
            break
        if norm_label(g.from_label) == winner:
            continue
        if not g.compatible:
            continue
        if not g.fixes_weakness_of_winner.strip():
            continue
        approved.append(g)
    return approved


def rejected_one_liners(
    ranking: Ranking,
    lens_names: dict[str, str],
    verdict: Verdict,
    rubric: list[Criterion],
) -> list[str]:
    """One deterministic line per non-winner: its lens and its weakest criterion
    relative to the winner. This is all the contractor learns about losers."""
    by_label = {norm_label(cs.label): cs for cs in verdict.scores}
    win_rows = {norm(x.criterion): x.score for x in by_label[ranking.winner].criteria_scores} if ranking.winner in by_label else {}
    lines: list[str] = []
    for r in ranking.ranked:
        if r.label == ranking.winner:
            continue
        name = lens_names.get(r.label, r.label)
        if r.disqualified:
            reason = by_label[r.label].disqualification_reason.strip() or "violated a constraint"
            lines.append(f"{name}: disqualified ({reason})")
            continue
        rows = {norm(x.criterion): x.score for x in by_label[r.label].criteria_scores}
        gaps = [
            (win_rows.get(norm(c.name), 0) - rows.get(norm(c.name), 0)) * c.weight
            for c in rubric
        ]
        if gaps and max(gaps) > 0:
            worst = rubric[gaps.index(max(gaps))].name
            lines.append(f"{name}: scored {r.total:.0f}/100, weakest against the winner on {worst}")
        else:
            lines.append(f"{name}: scored {r.total:.0f}/100")
    return lines


# --------------------------------------------------------------------------
# Phase 3: scrubbing
# --------------------------------------------------------------------------


def contractor_payload(
    brief: Brief,
    chosen: Candidate | None,
    grafts: list[Graft],
    rejected: list[str],
    open_issue: str | None,
    confidence_note: str | None,
) -> dict:
    """The only information the contractor sees. Raw candidates, judge scores and
    deliberation are deliberately absent."""
    return {
        "request": brief.restatement,
        "task_type": brief.task_type.value,
        "success_criteria": brief.success_criteria,
        "scope": brief.scope,
        "non_goals": brief.non_goals,
        "constraints": brief.constraints,
        "confirmed_answers": brief.confirmed,
        "assumptions": [f"{e.assumption} ({e.why})" for e in brief.ledger],
        "chosen_approach": chosen.model_dump() if chosen else None,
        "approved_grafts": [
            {"idea": g.idea, "fixes": g.fixes_weakness_of_winner} for g in grafts
        ],
        "rejected_alternatives": rejected,
        "open_issue": open_issue,
        "confidence_note": confidence_note,
    }


# --------------------------------------------------------------------------
# Recommendation path
# --------------------------------------------------------------------------

_GRADE_RANK = {
    EvidenceGrade.verified: 0,
    EvidenceGrade.corroborated: 1,
    EvidenceGrade.platform_rating_only: 2,
    EvidenceGrade.unverified: 3,
}

DEFAULT_PRIVACY_NOTE = (
    "Check what personal information you would hand over, and prefer the option that needs the least."
)
DEFAULT_CHECK = "Call or visit to confirm the price and availability before paying."


def enforce_shortlist(sl: Shortlist, sensitive: bool) -> Shortlist:
    """An option backed only by platform ratings cannot be the top pick while a
    better-evidenced option exists; disqualified options cannot be picked; a
    privacy note and at least one self-check are always present."""
    out = sl.model_copy(deep=True)
    eligible = [o for o in out.options if not o.disqualifying]
    pick = next((o for o in out.options if o.name.strip().lower() == out.recommended.strip().lower()), None)
    caveats = list(out.caveats)

    if eligible:
        best = min(eligible, key=lambda o: _GRADE_RANK[o.evidence_grade])
        if pick is None or pick.disqualifying or _GRADE_RANK[pick.evidence_grade] > _GRADE_RANK[best.evidence_grade]:
            if pick is not None:
                caveats.append(
                    f"The pick was changed from {pick.name} to {best.name}: "
                    f"{best.name} has stronger evidence ({best.evidence_grade.value} vs "
                    f"{pick.evidence_grade.value})."
                )
            out.recommended = best.name
    else:
        caveats.append("No option passed vetting; none is recommended.")
        out.recommended = ""

    if not out.privacy_note.strip():
        out.privacy_note = DEFAULT_PRIVACY_NOTE if sensitive else "No sensitive data appears to be involved."
    if sensitive and "privacy" not in out.privacy_note.lower() and "personal" not in out.privacy_note.lower():
        out.privacy_note = f"{out.privacy_note} {DEFAULT_PRIVACY_NOTE}".strip()
    if not any(s.strip() for s in out.user_should_check):
        out.user_should_check = [DEFAULT_CHECK]
    out.caveats = caveats
    return out
