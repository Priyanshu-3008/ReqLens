"""The analysis pipeline: extract -> score testability -> prioritise / use cases / issues / Q&A.

Everything works on a structured requirement list (no RAG): the document is read once,
requirements are extracted with IDs, and later steps only send that compact list.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from pydantic import BaseModel, Field, field_validator

from . import prompts
from .config import CONTEXT_LIST_MAX_CHARS, EXTRACT_CHUNK_CHARS, SCORE_BATCH_SIZE
from .loaders import chunk_text
from .rules import CRITERIA, RULE_CRITERIA, check_requirement

Progress = Optional[Callable[[float, str], None]]


# ---------------------------------------------------------------- schemas
class Requirement(BaseModel):
    id: str = ""
    text: str
    type: str = "FR"
    category: str = "General"
    actor: str = "System"

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, v):
        v = str(v or "FR").upper()
        return "NFR" if v.startswith("N") or "NON" in v else "FR"


class Acceptance(BaseModel):
    given: str = ""
    when: str = ""
    then: str = ""


def _clamp(v) -> int:
    try:
        return max(0, min(2, int(round(float(v)))))
    except (TypeError, ValueError):
        return 1


class LLMScore(BaseModel):
    id: str
    unambiguous: int = 1
    verifiable: int = 1
    atomic: int = 1
    complete: int = 1
    consistent: int = 2
    issues: list[str] = Field(default_factory=list)
    rewrite: str = ""
    acceptance: Acceptance = Field(default_factory=Acceptance)

    @field_validator(*CRITERIA, mode="before")
    @classmethod
    def _criteria(cls, v):
        return _clamp(v)

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, v):
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x) for x in (v or [])]

    @field_validator("acceptance", mode="before")
    @classmethod
    def _acc(cls, v):
        return v if isinstance(v, dict) else {}


class TestabilityResult(BaseModel):
    id: str
    llm: LLMScore
    rule_flags: list[str]
    rule_criteria: dict[str, int]
    rule_score: float
    llm_score: float
    final_score: float


# ---------------------------------------------------------------- helpers
def _fmt_reqs(reqs: list[Requirement]) -> str:
    return "\n".join(f"{r.id} [{r.type}] ({r.actor}): {r.text}" for r in reqs)


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def assign_ids(reqs: list[Requirement]) -> list[Requirement]:
    counters = {"FR": 0, "NFR": 0}
    for r in reqs:
        counters[r.type] += 1
        r.id = f"{r.type}-{counters[r.type]}"
    return reqs


# ---------------------------------------------------------------- 1. extract
def extract_requirements(llm, text: str, progress: Progress = None) -> tuple[list[Requirement], dict]:
    chunks = chunk_text(text, EXTRACT_CHUNK_CHARS)
    reqs: list[Requirement] = []
    seen: set[str] = set()
    verdicts: list[tuple[bool, str]] = []

    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress((i - 1) / len(chunks), f"Reading part {i} of {len(chunks)}…")
        data = llm.chat_json(prompts.SYSTEM_ANALYST,
                             prompts.EXTRACT.format(part=f"{i}/{len(chunks)}", text=chunk))
        verdicts.append((bool(data.get("is_requirements_document", True)),
                         str(data.get("reason", ""))))
        for item in data.get("requirements", []) or []:
            if isinstance(item, str):
                item = {"text": item}
            try:
                r = Requirement(**{k: v for k, v in item.items() if k != "id"})
            except Exception:
                continue
            key = _norm(r.text)
            if len(key) < 8 or key in seen:  # drop empties and duplicates across chunks
                continue
            seen.add(key)
            reqs.append(r)

    if progress:
        progress(1.0, "Done")
    is_req_doc = any(v for v, _ in verdicts) if verdicts else False
    reason = next((why for v, why in verdicts if v == is_req_doc and why), "")
    return assign_ids(reqs), {"is_requirements_document": is_req_doc, "reason": reason,
                              "chunks": len(chunks)}


# ---------------------------------------------------------------- 2. testability
def score_testability(llm, reqs: list[Requirement], batch_size: int = SCORE_BATCH_SIZE,
                      llm_weight: float = 0.6, progress: Progress = None) -> list[TestabilityResult]:
    full_list = _fmt_reqs(reqs)
    context = ""
    if len(full_list) <= CONTEXT_LIST_MAX_CHARS:
        context = ("All requirements in this document (use these to judge consistency):\n"
                   f"{full_list}\n\n")

    by_id: dict[str, LLMScore] = {}
    batches = list(_batches(reqs, batch_size))
    for i, batch in enumerate(batches, 1):
        if progress:
            progress((i - 1) / len(batches), f"Scoring batch {i} of {len(batches)}…")
        data = llm.chat_json(prompts.SYSTEM_ANALYST,
                             prompts.SCORE.format(context=context, batch=_fmt_reqs(batch)))
        for item in data.get("scores", []) or []:
            try:
                s = LLMScore(**item)
            except Exception:
                continue
            by_id[s.id.strip()] = s

        # Re-ask once, individually, for any requirement the model skipped
        missing = [r for r in batch if r.id not in by_id]
        if missing:
            data = llm.chat_json(prompts.SYSTEM_ANALYST,
                                 prompts.SCORE.format(context=context, batch=_fmt_reqs(missing)))
            for item in data.get("scores", []) or []:
                try:
                    s = LLMScore(**item)
                    by_id[s.id.strip()] = s
                except Exception:
                    pass

    if progress:
        progress(1.0, "Done")

    results = []
    for r in reqs:
        rule = check_requirement(r.text, r.type)
        s = by_id.get(r.id) or LLMScore(id=r.id, issues=["Model returned no score"])
        llm_score = round(sum(getattr(s, c) for c in CRITERIA) * 10 / (2 * len(CRITERIA)), 1)
        final = round(llm_weight * llm_score + (1 - llm_weight) * rule.score, 1)
        results.append(TestabilityResult(
            id=r.id, llm=s, rule_flags=rule.flags, rule_criteria=rule.criteria,
            rule_score=rule.score, llm_score=llm_score, final_score=final))
    return results


def agreement_stats(results: list[TestabilityResult]) -> dict:
    """How often the rule checks and the LLM agree on 'has a problem' per criterion.

    A criterion counts as 'problem' when its score is below 2. Reports raw agreement and
    Cohen's kappa (agreement corrected for chance) across the four rule-checkable criteria.
    """
    a = b = c = d = 0  # both-problem, rule-only, llm-only, both-fine
    per: dict[str, list[int]] = {k: [0, 0] for k in RULE_CRITERIA}
    for res in results:
        for k in RULE_CRITERIA:
            rp = res.rule_criteria[k] < 2
            lp = getattr(res.llm, k) < 2
            per[k][0] += int(rp == lp)
            per[k][1] += 1
            if rp and lp: a += 1
            elif rp: b += 1
            elif lp: c += 1
            else: d += 1
    n = a + b + c + d
    if n == 0:
        return {"agreement": None, "kappa": None, "per_criterion": {}, "rule_only": 0, "llm_only": 0}
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {
        "agreement": round(po * 100, 1),
        "kappa": round(kappa, 2),
        "per_criterion": {k: round(v[0] * 100 / v[1], 1) for k, v in per.items() if v[1]},
        "rule_only": b,
        "llm_only": c,
    }


# ---------------------------------------------------------------- 3. prioritise
def prioritize(llm, reqs: list[Requirement], progress: Progress = None) -> list[dict]:
    out: dict[str, dict] = {}
    batches = list(_batches(reqs, 40))
    for i, batch in enumerate(batches, 1):
        if progress:
            progress((i - 1) / len(batches), f"Prioritising batch {i} of {len(batches)}…")
        data = llm.chat_json(prompts.SYSTEM_ANALYST, prompts.PRIORITIZE.format(reqs=_fmt_reqs(batch)))
        for p in data.get("priorities", []) or []:
            if isinstance(p, dict) and p.get("id"):
                out[str(p["id"]).strip()] = p
    if progress:
        progress(1.0, "Done")

    order = {"must": "Must", "should": "Should", "could": "Could", "won't": "Won't", "wont": "Won't"}
    rows = []
    for r in reqs:
        p = out.get(r.id, {})
        moscow = str(p.get("moscow", "Should")).strip().lower().replace("won't-this-release", "won't")
        rows.append({
            "id": r.id,
            "moscow": next((v for k, v in order.items() if moscow.startswith(k)), "Should"),
            "business_value": _int15(p.get("business_value")),
            "complexity": _int15(p.get("complexity")),
            "depends_on": ", ".join(map(str, p.get("depends_on") or [])),
            "justification": str(p.get("justification", "")),
        })
    return rows


def _int15(v) -> int:
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------- 4. use cases
def use_cases(llm, reqs: list[Requirement]) -> dict:
    data = llm.chat_json(prompts.SYSTEM_ANALYST, prompts.USE_CASES.format(reqs=_fmt_reqs(reqs)))
    actors = [a if isinstance(a, dict) else {"name": str(a), "kind": "human"}
              for a in data.get("actors", []) or []]
    ucs = [u for u in data.get("use_cases", []) or [] if isinstance(u, dict) and u.get("name")]
    # Make sure every actor used by a use case exists
    known = {a.get("name") for a in actors}
    for u in ucs:
        for name in u.get("actors", []) or []:
            if name not in known:
                actors.append({"name": name, "kind": "human"})
                known.add(name)
    includes = [i for i in data.get("includes", []) or [] if isinstance(i, dict)]
    return {"actors": actors, "use_cases": ucs, "includes": includes}


# ---------------------------------------------------------------- 5. document issues
def document_issues(llm, reqs: list[Requirement]) -> dict:
    data = llm.chat_json(prompts.SYSTEM_ANALYST, prompts.ISSUES.format(reqs=_fmt_reqs(reqs)))
    return {k: [x for x in data.get(k, []) or [] if isinstance(x, dict)]
            for k in ("conflicts", "gaps", "questions")}


# ---------------------------------------------------------------- 6. Q&A
def answer_question(llm, reqs: list[Requirement], question: str, doc_text: str = "",
                    history: list[dict] | None = None) -> str:
    doc = ""
    if doc_text and len(doc_text) < 20_000:  # small docs: include the source for extra context
        doc = f"\nOriginal document (for background):\n\"\"\"\n{doc_text}\n\"\"\"\n"
    convo = ""
    for turn in (history or [])[-4:]:
        convo += f"\nPrevious {turn['role']}: {turn['content'][:800]}"
    user = prompts.QA_USER.format(reqs=_fmt_reqs(reqs), doc=doc, question=question)
    if convo:
        user = f"Conversation so far:{convo}\n\n{user}"
    return llm.chat(prompts.QA_SYSTEM, user, max_tokens=1500)
