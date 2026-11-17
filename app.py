"""ReqLens — AI requirements reviewer with testability scoring.

Run:  streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from core import analysis as an
from core.config import DEFAULT_PROVIDER, PROVIDERS, SCORE_BATCH_SIZE, resolve_key
from core.diagram import build_mermaid, mermaid_html
from core.export import band, requirements_df, testability_df, to_excel, to_markdown
from core.llm import LLMClient, LLMError
from core.loaders import clean_text, load_text

load_dotenv()
st.set_page_config(page_title="ReqLens — Requirements Reviewer", page_icon="🔎", layout="wide")

SAMPLE = Path(__file__).parent / "samples" / "sample_srs.txt"
STATE_DEFAULTS = {
    "doc_text": "", "doc_name": "", "reqs": [], "meta": {}, "test": [], "prio": [],
    "uc": None, "issues": None, "chat": [], "rewritten": set(),
}
for k, v in STATE_DEFAULTS.items():
    st.session_state.setdefault(k, v.copy() if hasattr(v, "copy") else v)
S = st.session_state


def reset_analysis(keep_doc: bool = True) -> None:
    for k, v in STATE_DEFAULTS.items():
        if keep_doc and k in ("doc_text", "doc_name"):
            continue
        S[k] = v.copy() if hasattr(v, "copy") else v


st.markdown("""
<style>
.block-container {padding-top: 2rem;}
.hero {background: linear-gradient(90deg,#283593,#5c6bc0); color:#fff; padding:1.1rem 1.4rem;
       border-radius:12px; margin-bottom:1rem}
.hero h1 {color:#fff; margin:0; font-size:1.7rem}
.hero p {margin:.2rem 0 0; opacity:.9}
.pill {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.8rem; margin-right:4px}
.good {background:#e8f5e9; color:#2e7d32} .mid {background:#fff8e1; color:#e65100}
.poor {background:#ffebee; color:#c62828}
</style>
<div class="hero"><h1>🔎 ReqLens</h1>
<p>Extract requirements, score their testability, and get rewrites with acceptance criteria.</p></div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Model")
    names = list(PROVIDERS)
    provider = PROVIDERS[st.selectbox("Provider", names,
                                      index=names.index(DEFAULT_PROVIDER) if DEFAULT_PROVIDER in names else 0)]
    st.caption(provider.note)
    model = st.text_input("Model", value=provider.default_model, key=f"model_{provider.name}")
    key_override = ""
    if provider.key_env:
        key_override = st.text_input(f"API key (optional if {provider.key_env} is set)",
                                     type="password", key=f"key_{provider.name}")
    st.divider()
    st.header("🎚️ Scoring")
    batch_size = st.slider("Requirements per scoring call", 4, 25, SCORE_BATCH_SIZE,
                           help="Lower this if you hit free-tier rate limits.")
    llm_weight = st.slider("Weight of LLM vs rule checks", 0.0, 1.0, 0.6, 0.1,
                           help="Final score = w × LLM score + (1 − w) × rule score.")
    st.divider()
    if st.button("Clear everything", use_container_width=True):
        reset_analysis(keep_doc=False)
        st.rerun()


def get_llm() -> LLMClient:
    wait_box = st.empty()
    return LLMClient(provider, resolve_key(provider, key_override), model,
                     on_wait=lambda s: wait_box.info(f"Rate limit hit — waiting {s:.0f}s and retrying…"))


def run(task_label: str, fn, *args, **kwargs):
    """Run an LLM step with a progress bar and friendly errors."""
    bar = st.progress(0.0, text=task_label)
    try:
        return fn(get_llm(), *args, progress=lambda p, t: bar.progress(p, text=t), **kwargs)
    except LLMError as e:
        st.error(str(e))
    except Exception as e:  # keep the app alive on unexpected model output
        st.error(f"Something went wrong: {e}")
    finally:
        bar.empty()
    return None


def run_simple(fn, *args, **kwargs):
    try:
        return fn(get_llm(), *args, **kwargs)
    except LLMError as e:
        st.error(str(e))
    except Exception as e:
        st.error(f"Something went wrong: {e}")
    return None


# ------------------------------------------------------------------ step 1: input
with st.container(border=True):
    st.subheader("1 · Add your requirements document")
    tab_up, tab_paste = st.tabs(["Upload file", "Paste text"])
    with tab_up:
        up = st.file_uploader("PDF, DOCX, TXT or MD", type=["pdf", "docx", "txt", "md"])
        if up is not None and up.name != S.doc_name:
            try:
                S.doc_text = load_text(up.name, up.getvalue())
                S.doc_name = up.name
                reset_analysis()
            except Exception as e:
                st.error(f"Could not read file: {e}")
    with tab_paste:
        pasted = st.text_area("Requirements text", height=180,
                              placeholder="The system shall allow customers to…")
        c1, c2 = st.columns(2)
        if c1.button("Use pasted text", disabled=not pasted.strip()):
            S.doc_text, S.doc_name = clean_text(pasted), "Pasted text"
            reset_analysis()
        if c2.button("Load sample SRS") and SAMPLE.exists():
            S.doc_text, S.doc_name = SAMPLE.read_text(encoding="utf-8"), "Sample SRS (food delivery)"
            reset_analysis()

    if S.doc_text:
        st.success(f"Loaded **{S.doc_name}** · {len(S.doc_text):,} characters")
        with st.expander("Preview"):
            st.text(S.doc_text[:3000] + ("…" if len(S.doc_text) > 3000 else ""))
        if st.button("🔍 Extract requirements", type="primary"):
            out = run("Extracting requirements…", an.extract_requirements, S.doc_text)
            if out:
                reqs, meta = out
                reset_analysis()
                S.reqs, S.meta = reqs, meta
                st.rerun()

if not S.reqs:
    if S.meta and not S.meta.get("is_requirements_document", True):
        st.warning(f"This doesn't look like a requirements document. {S.meta.get('reason', '')}")
    st.stop()

if not S.meta.get("is_requirements_document", True):
    st.warning(f"The model thinks this may not be a requirements document: {S.meta.get('reason', '')}")

# ------------------------------------------------------------------ overview metrics
req_by_id = {r.id: r for r in S.reqs}
test_df = testability_df(S.reqs, S.test) if S.test else pd.DataFrame()
m = st.columns(5)
m[0].metric("Requirements", len(S.reqs))
m[1].metric("Functional", sum(r.type == "FR" for r in S.reqs))
m[2].metric("Non-functional", sum(r.type == "NFR" for r in S.reqs))
m[3].metric("Actors", len({r.actor for r in S.reqs}))
m[4].metric("Avg testability", f"{test_df['score'].mean():.1f}/10" if not test_df.empty else "—")

tabs = st.tabs(["📋 Requirements", "🧪 Testability", "🏆 Priorities", "👥 Use cases",
                "⚠️ Issues", "💬 Ask", "⬇️ Export"])

# ------------------------------------------------------------------ requirements
with tabs[0]:
    st.caption("Fix anything the extractor got wrong, then save. Saving renumbers IDs and "
               "clears earlier analysis.")
    edited = st.data_editor(
        requirements_df(S.reqs), num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
            "type": st.column_config.SelectboxColumn("Type", options=["FR", "NFR"], width="small"),
            "text": st.column_config.TextColumn("Requirement", width="large"),
        }, key="req_editor")
    if st.button("Save edits"):
        rows = edited.fillna("").to_dict("records")
        new = [an.Requirement(text=str(r["text"]).strip(), type=r["type"] or "FR",
                              category=r["category"] or "General", actor=r["actor"] or "System")
               for r in rows if str(r["text"]).strip()]
        meta = S.meta
        reset_analysis()
        S.reqs, S.meta = an.assign_ids(new), meta
        st.rerun()

# ------------------------------------------------------------------ testability
with tabs[1]:
    with st.expander("How the score works", expanded=not S.test):
        st.markdown(
            "Each requirement is scored **0–2 on five ISO/IEC/IEEE 29148 qualities**: "
            "*unambiguous, verifiable, atomic, complete, consistent* (→ out of 10).\n\n"
            "Two independent judges are combined:\n"
            "- **Rule checks** (deterministic): vague words, weak modals (*should, may*), "
            "TBDs, open-ended phrases, NFRs without numbers, bundled obligations.\n"
            "- **LLM review**: applies the rubric, explains issues, proposes a testable rewrite "
            "and a Given/When/Then acceptance criterion.\n\n"
            "The agreement between the two is reported below as a sanity check.")
    c1, c2 = st.columns([1, 1])
    if c1.button("🧪 Score testability", type="primary"):
        res = run("Scoring…", an.score_testability, S.reqs, batch_size=batch_size, llm_weight=llm_weight)
        if res:
            S.test, S.rewritten = res, set()
            st.rerun()
    if S.rewritten and c2.button(f"Re-score {len(S.rewritten)} rewritten requirement(s)"):
        subset = [req_by_id[i] for i in S.rewritten if i in req_by_id]
        res = run("Re-scoring…", an.score_testability, subset, batch_size=batch_size, llm_weight=llm_weight)
        if res:
            new = {r.id: r for r in res}
            S.test = [new.get(t.id, t) for t in S.test]
            S.rewritten = set()
            st.rerun()

    if S.test:
        stats = an.agreement_stats(S.test)
        k = st.columns(4)
        k[0].metric("Poor (< 5)", int((test_df["score"] < 5).sum()))
        k[1].metric("Needs work (5–8)", int(((test_df["score"] >= 5) & (test_df["score"] < 8)).sum()))
        k[2].metric("Good (≥ 8)", int((test_df["score"] >= 8).sum()))
        k[3].metric("Rule–LLM agreement", f"{stats['agreement']}%",
                    help=f"Cohen's κ = {stats['kappa']}. Agreement on whether each criterion "
                         "has a problem, across the 4 rule-checkable criteria.")

        crit = test_df[["unambiguous", "verifiable", "atomic", "complete", "consistent"]].mean()
        st.caption("Average per criterion (0–2) — the weakest bar is where the document needs most work")
        st.bar_chart(crit.rename("average"), height=200)

        only_bad = st.toggle("Show only requirements scoring below 8", value=True)
        view = test_df[test_df["score"] < 8] if only_bad else test_df
        st.dataframe(
            view[["id", "score", "requirement", "issues", "rule_flags"]].sort_values("score"),
            use_container_width=True, hide_index=True,
            column_config={
                "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%.1f"),
                "requirement": st.column_config.TextColumn("Requirement", width="large"),
            })

        st.markdown("#### Details and rewrites")
        for _, row in view.sort_values("score").iterrows():
            cls = {"Good": "good", "Needs work": "mid", "Poor": "poor"}[band(row["score"])]
            stale = " · ✏️ rewritten, re-score pending" if row["id"] in S.rewritten else ""
            with st.expander(f"{row['id']} — {row['score']:.1f}/10{stale}"):
                st.markdown(f"<span class='pill {cls}'>{band(row['score'])}</span>"
                            f"<span class='pill mid'>LLM {row['llm_score']}</span>"
                            f"<span class='pill mid'>Rules {row['rule_score']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**Current:** {req_by_id[row['id']].text}")
                cols = st.columns(5)
                for col, c in zip(cols, ["unambiguous", "verifiable", "atomic", "complete", "consistent"]):
                    col.metric(c.capitalize(), f"{row[c]}/2")
                if row["issues"]:
                    st.markdown("**Issues (LLM):** " + row["issues"])
                if row["rule_flags"]:
                    st.markdown("**Rule flags:** " + row["rule_flags"])
                st.markdown(f"**Suggested rewrite:** {row['rewrite']}")
                st.markdown(f"**Acceptance:** *Given* {row['given']}, *when* {row['when']}, "
                            f"*then* {row['then']}")
                if row["rewrite"] and row["rewrite"] != req_by_id[row["id"]].text:
                    if st.button("Accept rewrite", key=f"acc_{row['id']}"):
                        req_by_id[row["id"]].text = row["rewrite"]
                        S.rewritten.add(row["id"])
                        st.rerun()

# ------------------------------------------------------------------ priorities
with tabs[2]:
    if st.button("🏆 Prioritise (MoSCoW)", type="primary"):
        res = run("Prioritising…", an.prioritize, S.reqs)
        if res:
            S.prio = res
            st.rerun()
    if S.prio:
        pdf = pd.DataFrame(S.prio)
        pdf.insert(1, "requirement", pdf["id"].map(lambda i: req_by_id[i].text if i in req_by_id else ""))
        order = ["Must", "Should", "Could", "Won't"]
        counts = pdf["moscow"].value_counts().reindex(order, fill_value=0)
        c = st.columns(4)
        for col, name in zip(c, order):
            col.metric(name, int(counts[name]))
        pdf["_o"] = pdf["moscow"].map({n: i for i, n in enumerate(order)})
        st.dataframe(pdf.sort_values(["_o", "business_value"], ascending=[True, False]).drop(columns="_o"),
                     use_container_width=True, hide_index=True,
                     column_config={"requirement": st.column_config.TextColumn(width="large")})
        st.caption("Value vs complexity — top-left are quick wins")
        st.scatter_chart(pdf, x="complexity", y="business_value", color="moscow", height=300)

# ------------------------------------------------------------------ use cases
with tabs[3]:
    if st.button("👥 Generate use-case diagram", type="primary"):
        with st.spinner("Finding actors and use cases…"):
            res = run_simple(an.use_cases, S.reqs)
        if res:
            S.uc = res
            st.rerun()
    if S.uc:
        code = build_mermaid(S.uc)
        components.html(mermaid_html(code, 620), height=620, scrolling=True)
        with st.expander("Mermaid code (paste into mermaid.live or a README)"):
            st.code(code, language="text")
        st.dataframe(pd.DataFrame([{
            "use case": u["name"], "actors": ", ".join(u.get("actors", [])),
            "requirements": ", ".join(u.get("requirement_ids", []))} for u in S.uc["use_cases"]]),
            use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ issues
with tabs[4]:
    if st.button("⚠️ Find conflicts, gaps and open questions", type="primary"):
        with st.spinner("Reviewing the whole set…"):
            res = run_simple(an.document_issues, S.reqs)
        if res:
            S.issues = res
            st.rerun()
    if S.issues:
        st.markdown("#### Conflicts")
        if not S.issues["conflicts"]:
            st.write("None found.")
        for c in S.issues["conflicts"]:
            st.error(f"**{' vs '.join(c.get('ids', []))}** — {c.get('explanation', '')}")
        st.markdown("#### Gaps")
        for g in S.issues["gaps"]:
            with st.container(border=True):
                st.markdown(f"**{g.get('area', '')}** — {g.get('explanation', '')}")
                if g.get("suggested_requirement"):
                    st.caption(f"Suggested: {g['suggested_requirement']}")
        st.markdown("#### Questions for stakeholders")
        for q in S.issues["questions"]:
            rel = f" ({', '.join(q.get('related_ids', []))})" if q.get("related_ids") else ""
            st.markdown(f"- {q.get('question', '')}{rel}")

# ------------------------------------------------------------------ Q&A
with tabs[5]:
    st.caption("Answers cite requirement IDs like [FR-3] so you can check them.")
    for msg in S.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    q = st.chat_input("e.g. Which requirements cover payments? What happens if login fails?")
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"), st.spinner("Thinking…"):
            ans = run_simple(an.answer_question, S.reqs, q, S.doc_text, S.chat)
            if ans:
                st.markdown(ans)
        if ans:
            S.chat += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]

# ------------------------------------------------------------------ export
with tabs[6]:
    title = Path(S.doc_name).stem or "requirements"
    sheets = {"Requirements": requirements_df(S.reqs), "Testability": test_df,
              "Priorities": pd.DataFrame(S.prio)}
    if S.issues:
        sheets["Gaps"] = pd.DataFrame(S.issues["gaps"])
        sheets["Questions"] = pd.DataFrame(S.issues["questions"])
    mermaid = build_mermaid(S.uc) if S.uc else None
    c = st.columns(3)
    c[0].download_button("Excel report (.xlsx)", to_excel(sheets), f"{title}_review.xlsx",
                         use_container_width=True)
    c[1].download_button("Markdown report (.md)",
                         to_markdown(title, S.reqs, test_df, S.prio, S.issues, mermaid),
                         f"{title}_review.md", use_container_width=True)
    c[2].download_button("Requirements (.csv)", requirements_df(S.reqs).to_csv(index=False),
                         f"{title}_requirements.csv", use_container_width=True)
    st.caption("Tip: run Testability, Priorities, Use cases and Issues first for a complete report.")
