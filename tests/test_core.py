"""Offline tests: no API key needed. A fake LLM returns canned JSON.

Run:  python -m pytest -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import analysis as an  # noqa: E402
from core.diagram import build_mermaid  # noqa: E402
from core.export import requirements_df, to_excel, to_markdown
from core.export import testability_df as make_test_df  # noqa: E402
from core.llm import parse_json  # noqa: E402
from core.loaders import chunk_text  # noqa: E402
from core.rules import check_requirement  # noqa: E402


# ------------------------------------------------------------------ rules
def test_good_requirement_scores_high():
    r = check_requirement("The OTP shall expire 5 minutes after it is sent.", "FR")
    assert r.score == 10.0, r.flags


def test_vague_nfr_scores_low():
    r = check_requirement("The app should be fast.", "NFR")
    assert r.score <= 6
    assert any("Vague" in f for f in r.flags)
    assert any("measurable" in f for f in r.flags)


def test_tbd_is_incomplete():
    r = check_requirement("Coupon rules are TBD.", "FR")
    assert r.criteria["complete"] == 0


def test_bundled_obligations_not_atomic():
    r = check_requirement("The system shall allow customers to add items to a cart and place an "
                          "order, and shall send an SMS and email confirmation.", "FR")
    assert r.criteria["atomic"] < 2


def test_measurable_nfr_passes():
    r = check_requirement("The home screen shall load within 2 seconds for 95% of requests.", "NFR")
    assert r.criteria["verifiable"] == 2


def test_standard_reference_counts_as_measurable():
    r = check_requirement("All payment data shall be handled in compliance with PCI DSS.", "NFR")
    assert r.criteria["verifiable"] == 2


# ------------------------------------------------------------------ parsing / chunking
def test_parse_json_with_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1,2]} thanks') == {"a": [1, 2]}


def test_chunking_keeps_paragraphs():
    text = "\n\n".join(f"Paragraph {i} " + "x" * 50 for i in range(40))
    chunks = chunk_text(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).count("Paragraph") == 40


# ------------------------------------------------------------------ pipeline with a fake LLM
class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat_json(self, system, user, **kw):
        self.calls.append(user)
        if "Extract every software requirement" in user:
            return {"is_requirements_document": True, "reason": "SRS",
                    "requirements": [
                        {"text": "The OTP shall expire 5 minutes after it is sent.", "type": "FR",
                         "category": "Auth", "actor": "Customer"},
                        {"text": "The app should be fast.", "type": "non-functional",
                         "category": "Performance", "actor": "System"},
                        {"text": "Coupon rules are TBD.", "type": "FR"},
                        {"text": "The app should be fast.", "type": "NFR"},  # duplicate
                    ]}
        if "TESTABILITY" in user:
            ids = [line.split(" ")[0] for line in user.split("Requirements to score:\n")[1]
                   .split("\n\nReturn JSON")[0].splitlines() if line.strip()]
            # deliberately skip the last id to exercise the re-ask path on first call
            scores = []
            for i in ids:
                bad = i != "FR-1"
                scores.append({"id": i, "unambiguous": 0 if bad else 2, "verifiable": "1" if bad else 2,
                               "atomic": 2, "complete": 0 if bad else 2, "consistent": 2,
                               "issues": "vague" if bad else [], "rewrite": f"{i} rewritten",
                               "acceptance": {"given": "g", "when": "w", "then": "t"}})
            if len(ids) > 1:
                scores = scores[:-1]
            return {"scores": scores}
        if "MoSCoW" in user:
            return {"priorities": [{"id": "FR-1", "moscow": "Must", "business_value": 5, "complexity": 9},
                                   {"id": "NFR-1", "moscow": "won't-this-release"}]}
        if "use cases" in user:
            return {"actors": [{"name": "Customer", "kind": "human"},
                               {"name": "Payment Gateway", "kind": "system"}],
                    "use_cases": [{"name": "Log in", "actors": ["Customer"]},
                                  {"name": "Pay for order", "actors": ["Customer", "Payment Gateway", "Bank"]}],
                    "includes": [{"base": "Pay for order", "included": "Log in"}]}
        if "conflicts" in user:
            return {"conflicts": [{"ids": ["FR-1", "FR-2"], "explanation": "x"}],
                    "gaps": [{"area": "Security", "explanation": "y"}], "questions": ["bad item"]}
        raise AssertionError("unexpected prompt")

    def chat(self, system, user, **kw):
        return "Covered by [FR-1]."


def _pipeline():
    llm = FakeLLM()
    reqs, meta = an.extract_requirements(llm, "dummy text")
    return llm, reqs, meta


def test_extract_dedupes_and_numbers():
    _, reqs, meta = _pipeline()
    assert meta["is_requirements_document"]
    assert [r.id for r in reqs] == ["FR-1", "NFR-1", "FR-2"]
    assert reqs[1].type == "NFR"


def test_scoring_merges_rules_llm_and_reasks_missing():
    llm, reqs, _ = _pipeline()
    res = an.score_testability(llm, reqs, batch_size=10)
    assert len(res) == 3
    by = {r.id: r for r in res}
    assert by["FR-1"].llm_score == 10.0 and by["FR-1"].final_score == 10.0
    assert by["NFR-1"].llm.verifiable == 1               # "1" string coerced to int
    assert by["FR-2"].llm.rewrite == "FR-2 rewritten"    # recovered by the re-ask
    assert by["NFR-1"].final_score < 6
    stats = an.agreement_stats(res)
    assert 0 <= stats["agreement"] <= 100 and stats["kappa"] is not None


def test_prioritize_normalises_values():
    llm, reqs, _ = _pipeline()
    rows = {r["id"]: r for r in an.prioritize(llm, reqs)}
    assert rows["FR-1"]["moscow"] == "Must" and rows["FR-1"]["complexity"] == 5  # clamped
    assert rows["NFR-1"]["moscow"] == "Won't"
    assert rows["FR-2"]["moscow"] == "Should"  # missing -> default


def test_use_case_diagram_is_valid_mermaid():
    llm, reqs, _ = _pipeline()
    uc = an.use_cases(llm, reqs)
    assert any(a["name"] == "Bank" for a in uc["actors"])  # unknown actor auto-added
    code = build_mermaid(uc)
    assert code.startswith("flowchart LR")
    assert "subgraph SYS" in code and "include" in code
    assert code.count("(((") == 0  # labels sanitised, shapes well-formed


def test_issues_filters_bad_items():
    llm, reqs, _ = _pipeline()
    iss = an.document_issues(llm, reqs)
    assert iss["questions"] == [] and len(iss["conflicts"]) == 1


def test_exports():
    llm, reqs, _ = _pipeline()
    res = an.score_testability(llm, reqs)
    tdf = make_test_df(reqs, res)
    xlsx = to_excel({"Requirements": requirements_df(reqs), "Testability": tdf})
    assert xlsx[:2] == b"PK"  # zip container
    md = to_markdown("Demo", reqs, tdf, an.prioritize(llm, reqs), an.document_issues(llm, reqs),
                     build_mermaid(an.use_cases(llm, reqs)))
    assert "## Requirements that need rewriting" in md and "```mermaid" in md


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failures else 0)
