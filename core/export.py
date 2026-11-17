"""Turn results into tables and downloadable files (Excel, CSV, Markdown)."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from .analysis import Requirement, TestabilityResult


def requirements_df(reqs: list[Requirement]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in reqs],
                        columns=["id", "type", "category", "actor", "text"])


def testability_df(reqs: list[Requirement], results: list[TestabilityResult]) -> pd.DataFrame:
    text = {r.id: r.text for r in reqs}
    rows = []
    for t in results:
        rows.append({
            "id": t.id,
            "requirement": text.get(t.id, ""),
            "score": t.final_score,
            "llm_score": t.llm_score,
            "rule_score": t.rule_score,
            "unambiguous": t.llm.unambiguous,
            "verifiable": t.llm.verifiable,
            "atomic": t.llm.atomic,
            "complete": t.llm.complete,
            "consistent": t.llm.consistent,
            "issues": "; ".join(t.llm.issues),
            "rule_flags": "; ".join(t.rule_flags),
            "rewrite": t.llm.rewrite,
            "given": t.llm.acceptance.given,
            "when": t.llm.acceptance.when,
            "then": t.llm.acceptance.then,
        })
    return pd.DataFrame(rows)


def band(score: float) -> str:
    return "Good" if score >= 8 else "Needs work" if score >= 5 else "Poor"


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for col_cells in ws.columns:
                longest = max((len(str(c.value or "")) for c in col_cells), default=8)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, longest + 2), 70)
            ws.freeze_panes = "B2"
    return buf.getvalue()


def to_markdown(title: str, reqs: list[Requirement], test_df: pd.DataFrame | None,
                prio: list[dict] | None, issues: dict | None, mermaid: str | None) -> str:
    out = [f"# Requirements review — {title}", f"_Generated {date.today().isoformat()}_", ""]
    fr = sum(r.type == "FR" for r in reqs)
    out.append(f"**{len(reqs)} requirements** ({fr} functional, {len(reqs) - fr} non-functional)")
    if test_df is not None and not test_df.empty:
        out.append(f"**Average testability:** {test_df['score'].mean():.1f}/10 · "
                   f"{(test_df['score'] < 5).sum()} poor, "
                   f"{((test_df['score'] >= 5) & (test_df['score'] < 8)).sum()} need work, "
                   f"{(test_df['score'] >= 8).sum()} good")
    out.append("")

    out.append("## Requirements")
    pmap = {p["id"]: p["moscow"] for p in (prio or [])}
    smap = dict(zip(test_df["id"], test_df["score"])) if test_df is not None and not test_df.empty else {}
    out.append("| ID | Type | Requirement | Testability | Priority |")
    out.append("|---|---|---|---|---|")
    for r in reqs:
        s = f"{smap[r.id]:.1f}" if r.id in smap else "–"
        out.append(f"| {r.id} | {r.type} | {r.text.replace('|', '/')} | {s} | {pmap.get(r.id, '–')} |")
    out.append("")

    if test_df is not None and not test_df.empty:
        out.append("## Requirements that need rewriting")
        for _, row in test_df[test_df["score"] < 8].sort_values("score").iterrows():
            out.append(f"### {row['id']} — {row['score']:.1f}/10")
            out.append(f"- **Original:** {row['requirement']}")
            if row["issues"]:
                out.append(f"- **Issues:** {row['issues']}")
            out.append(f"- **Suggested:** {row['rewrite']}")
            out.append(f"- **Acceptance:** Given {row['given']}, when {row['when']}, then {row['then']}")
            out.append("")

    if issues:
        if issues.get("conflicts"):
            out.append("## Conflicts")
            out += [f"- {', '.join(c.get('ids', []))}: {c.get('explanation', '')}" for c in issues["conflicts"]]
            out.append("")
        if issues.get("gaps"):
            out.append("## Gaps")
            out += [f"- **{g.get('area', '')}:** {g.get('explanation', '')}" for g in issues["gaps"]]
            out.append("")
        if issues.get("questions"):
            out.append("## Questions for stakeholders")
            out += [f"- {q.get('question', '')}" for q in issues["questions"]]
            out.append("")

    if mermaid:
        out += ["## Use-case diagram", "```mermaid", mermaid, "```", ""]
    return "\n".join(out)
