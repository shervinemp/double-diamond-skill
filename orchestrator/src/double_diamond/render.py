"""Turn structured results into the user-facing Markdown."""

from __future__ import annotations

import json
from typing import Any

from .models import Plan

FOOTER = "Say **go** to execute, or tell me which assumption to flip."


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def render_plan(plan: Plan) -> str:
    out: list[str] = [f"## Goal and done-when\n{plan.goal_and_done}"]

    if plan.kind == "shortlist" and plan.shortlist is not None:
        sl = plan.shortlist
        rows = [
            "| Option | Price | Quality evidence | Trust evidence | Evidence grade | Trade-off |",
            "|---|---|---|---|---|---|",
        ]
        for o in sl.options:
            flags = f" ⚠ {'; '.join(o.red_flags)}" if o.red_flags else ""
            tag = " (ruled out)" if o.disqualifying else ""
            rows.append(
                f"| {o.name}{tag} | {o.price} ({o.price_basis}) | {o.quality_evidence} | "
                f"{o.trust_evidence}{flags} | {o.evidence_grade.value} | {o.tradeoff} |"
            )
        out.append("## Shortlist\n" + "\n".join(rows))
        out.append(f"**Recommended:** {sl.recommended}" if sl.recommended else "**Recommended:** none passed vetting")
        pay = [o for o in sl.options if o.payment_note.strip()]
        if pay:
            out.append("**Payment:** " + "; ".join(f"{o.name}: {o.payment_note}" for o in pay))
        out.append(f"**Privacy:** {sl.privacy_note}")
        out.append("**Check these yourself before paying**\n" + _bullets(sl.user_should_check))
        if sl.caveats:
            out.append("**Caveats**\n" + _bullets(sl.caveats))
    else:
        out.append("## Plan\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(plan.steps, 1)))

    if plan.assumptions:
        lines = [f"- **{a.assumption}** — {a.why} *To change: {a.how_to_flip}*" for a in plan.assumptions]
        out.append("## Assumptions I made\n" + "\n".join(lines))
    if plan.non_goals:
        out.append("## Not doing\n" + _bullets(plan.non_goals))
    if plan.trade_offs:
        out.append("## Trade-offs accepted\n" + _bullets(plan.trade_offs))
    if plan.risks:
        out.append("## Risks\n" + "\n".join(f"- {r.risk} — *Mitigation:* {r.mitigation}" for r in plan.risks))
    if plan.confidence_note:
        out.append(f"> {plan.confidence_note}")
    if plan.open_items:
        out.append("## Open items\n" + _bullets(plan.open_items))
    out.append(FOOTER)
    return "\n\n".join(out)


def render_audit(audit: dict[str, Any]) -> str:
    """The deliberation, shown only on request."""
    parts: list[str] = []
    triage = audit.get("triage")
    if triage:
        parts.append("## Triage\n```json\n" + json.dumps(triage, indent=2) + "\n```")
    if audit.get("bucket_notes"):
        parts.append("## Question policy\n" + _bullets(audit["bucket_notes"]))
    if audit.get("discovery"):
        parts.append("## Discovery notes\n" + str(audit["discovery"]))
    p2 = audit.get("phase2")
    if p2:
        parts.append("## Lenses\n" + "\n".join(f"- **{l['name']}**: {l['priority']}" for l in p2["lenses"]))
        parts.append("## Rubric\n" + "\n".join(f"- {c['name']}: {c['weight']}" for c in p2["rubric"]))
        parts.append(
            "## Ranking\n"
            + "\n".join(
                f"- {r['label']} ({p2['lens_by_label'].get(r['label'], '?')}): {r['total']}"
                + (" — disqualified" if r["disqualified"] else "")
                for r in p2["ranking"]
            )
            + f"\n\nWinner: {p2['winner']} · margin {p2['margin']} · confidence {p2['confidence']}"
        )
        if p2.get("grafts"):
            parts.append("## Grafts accepted\n" + _bullets([f"{g['idea']} (fixes: {g['fixes_weakness_of_winner']})" for g in p2["grafts"]]))
    if audit.get("recommendation_notes"):
        parts.append("## Research notes\n" + str(audit["recommendation_notes"]))
    return "\n\n".join(parts) or "(no deliberation recorded)"
