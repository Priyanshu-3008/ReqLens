"""Deterministic testability checks (no LLM).

Each check maps to one of the ISO/IEC/IEEE 29148 quality characteristics we score.
These give explainable, repeatable flags, and act as a cross-check on the LLM's scores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CRITERIA = ["unambiguous", "verifiable", "atomic", "complete", "consistent"]
RULE_CRITERIA = ["unambiguous", "verifiable", "atomic", "complete"]  # consistency needs the LLM

VAGUE_TERMS = [
    "fast", "quick", "quickly", "rapid", "efficient", "efficiently", "user-friendly",
    "user friendly", "easy", "easily", "simple", "intuitive", "flexible", "robust",
    "seamless", "seamlessly", "adequate", "appropriate", "sufficient", "reasonable",
    "minimal", "optimal", "optimize", "maximize", "minimize", "high performance",
    "state-of-the-art", "modern", "secure", "reliable", "scalable", "responsive",
    "large number", "many", "several", "some", "most", "normally", "typically",
    "approximately", "significant", "better", "improved", "enhanced", "good",
]
OPEN_ENDED = [
    "etc", "and so on", "and/or", "including but not limited to", "such as",
    "as needed", "as required", "as appropriate", "if possible", "where possible",
    "if necessary", "when necessary", "as soon as possible", "asap",
]
PLACEHOLDERS = ["tbd", "tbc", "to be decided", "to be determined", "to be confirmed",
                "xxx", "???", "[insert", "n/a"]
WEAK_MODALS = ["should", "may", "might", "could", "would", "ideally", "preferably"]
BINDING = re.compile(r"\b(shall|must|will|is required to)\b", re.I)
MEASURE = re.compile(
    r"\d+(\.\d+)?\s*(%|percent|ms|millisecond|s\b|sec|second|minute|min\b|hour|hr|day|"
    r"week|month|year|kb|mb|gb|tb|users?|requests?|transactions?|rps|tps|concurrent|times|"
    r"x\b|clicks?|steps?|characters?|attempts?|items?|records?|pages?|px|dpi|°|rupees|inr|\$|"
    r"uptime|nines)", re.I)
ANY_NUMBER = re.compile(r"\d")
STANDARD_REF = re.compile(r"\b(ISO|IEC|IEEE|WCAG|OWASP|GDPR|HIPAA|PCI|AES-?\d+|TLS\s?\d|RFC\s?\d+|DPDP)\b", re.I)


@dataclass
class RuleResult:
    flags: list[str] = field(default_factory=list)
    criteria: dict[str, int] = field(default_factory=lambda: {c: 2 for c in RULE_CRITERIA})

    @property
    def score(self) -> float:
        """Out of 10. Consistency can't be judged by rules, so it's assumed fine (2)."""
        return round((sum(self.criteria.values()) + 2) * 10 / (2 * len(CRITERIA)), 1)

    def _hit(self, criterion: str, severity: int, msg: str) -> None:
        # Deductions add up: a vague word AND a weak modal is worse than either alone.
        self.criteria[criterion] = max(0, self.criteria[criterion] - severity)
        self.flags.append(msg)


def _find_terms(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    found = []
    for t in terms:
        pattern = r"(?<![\w-])" + re.escape(t) + r"(?![\w-])"
        if re.search(pattern, low):
            found.append(t)
    return found


def check_requirement(text: str, req_type: str = "FR") -> RuleResult:
    r = RuleResult()
    words = len(text.split())

    # Unambiguous
    vague = _find_terms(text, VAGUE_TERMS)
    if vague:
        r._hit("unambiguous", 2 if len(vague) > 1 else 1, f"Vague term(s): {', '.join(vague)}")
    weak = _find_terms(text, WEAK_MODALS)
    if weak:
        r._hit("unambiguous", 1, f"Weak modal verb(s): {', '.join(weak)} — use 'shall'")
    if re.match(r"^\s*(it|this|they|that)\b", text, re.I):
        r._hit("unambiguous", 1, "Starts with an unclear pronoun")

    # Complete
    holes = _find_terms(text, PLACEHOLDERS)
    if holes:
        r._hit("complete", 2, f"Placeholder: {', '.join(holes)}")
        r._hit("verifiable", 2, "Cannot be tested until the placeholder is filled in")
    open_ended = _find_terms(text, OPEN_ENDED)
    if open_ended:
        r._hit("complete", 1, f"Open-ended phrase: {', '.join(open_ended)}")
    if words < 5:
        r._hit("complete", 1, "Too short to be a complete requirement")

    # Verifiable
    has_measure = bool(MEASURE.search(text)) or bool(STANDARD_REF.search(text))
    if req_type.upper() == "NFR" and not has_measure:
        severity = 1 if ANY_NUMBER.search(text) else 2
        r._hit("verifiable", severity, "Non-functional requirement has no measurable target")
    if vague and not has_measure:
        r._hit("verifiable", 1, "Vague quality with no number or standard to test against")
    if not BINDING.search(text) and not weak:
        r._hit("verifiable", 1, "No binding verb (shall / must)")

    # Atomic
    binding_count = len(BINDING.findall(text)) + len(_find_terms(text, WEAK_MODALS))
    if binding_count > 1:
        r._hit("atomic", 1, "Several obligations in one sentence — split it")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) > 3]
    if len(sentences) > 2:
        r._hit("atomic", 1, "Multiple sentences — may hold several requirements")
    if words > 45:
        r._hit("atomic", 1, f"Long requirement ({words} words)")

    return r
