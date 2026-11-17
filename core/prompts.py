"""All prompts in one place, so they are easy to read, tune and version."""

SYSTEM_ANALYST = (
    "You are a senior requirements engineer who knows ISO/IEC/IEEE 29148 well. "
    "You are precise, you never invent requirements that are not in the source, "
    "and you always reply with valid JSON in exactly the schema requested."
)

EXTRACT = """Extract every software requirement from the document excerpt below.

Rules:
- One item per individual requirement. If a sentence holds several obligations, keep it as
  written (do NOT split or rewrite) — testability scoring will flag it later.
- Copy the requirement text as close to the source wording as possible.
- type: "FR" (functional: what the system does) or "NFR" (quality: performance, security,
  usability, reliability, availability, compliance, maintainability, portability...).
- category: short label, e.g. "Authentication", "Performance", "Payments".
- actor: the user role or external system that triggers or benefits from it, or "System".
- Skip headings, glossary entries, background and purely descriptive text.
- is_requirements_document: false if this text is clearly not a requirements/spec/PRD/user-story
  document (e.g. a recipe, a news article, a poem).

Return JSON:
{{"is_requirements_document": true, "reason": "<one line>",
  "requirements": [{{"text": "...", "type": "FR", "category": "...", "actor": "..."}}]}}

Document excerpt ({part}):
\"\"\"
{text}
\"\"\""""

SCORE = """Score each requirement below for TESTABILITY using this rubric.
Each criterion gets 0, 1 or 2:

- unambiguous: 2 = only one reasonable interpretation; 1 = minor vague wording;
  0 = key terms are vague or subjective ("fast", "user-friendly", "secure").
- verifiable: 2 = a tester could write a pass/fail test from it alone (measurable target,
  concrete behaviour); 1 = testable only after assumptions; 0 = no objective way to test.
- atomic: 2 = exactly one requirement; 1 = two closely related obligations;
  0 = several unrelated obligations bundled together.
- complete: 2 = conditions, actor, inputs/outputs and limits are stated; 1 = some detail
  missing; 0 = placeholders (TBD) or essential information missing.
- consistent: 2 = no conflict with other listed requirements; 1 = possible tension;
  0 = directly contradicts another requirement (name its id in issues).

For EACH requirement also give:
- issues: short list of concrete problems (empty list if none).
- rewrite: an improved, testable version using "shall", measurable values and one obligation.
  Where the source lacks a number, put a clearly marked placeholder like <N seconds> rather
  than inventing a value. If it is already excellent, repeat it unchanged.
- acceptance: one acceptance criterion as {{"given": "...", "when": "...", "then": "..."}}.

{context}Requirements to score:
{batch}

Return JSON:
{{"scores": [{{"id": "FR-1", "unambiguous": 2, "verifiable": 1, "atomic": 2, "complete": 1,
  "consistent": 2, "issues": ["..."], "rewrite": "...",
  "acceptance": {{"given": "...", "when": "...", "then": "..."}}}}]}}
Include every id listed above exactly once."""

PRIORITIZE = """Prioritise these requirements with MoSCoW (Must, Should, Could, Won't-this-release).

Consider business value to the main users, implementation complexity, risk, and dependencies
(a requirement that others depend on should rank at least as high as them).

Requirements:
{reqs}

Return JSON:
{{"priorities": [{{"id": "FR-1", "moscow": "Must", "business_value": 5, "complexity": 3,
  "depends_on": ["FR-2"], "justification": "<one sentence>"}}]}}
business_value and complexity are 1 (low) to 5 (high). Include every id exactly once."""

USE_CASES = """From these requirements, identify the actors and the use cases (user goals,
named as short verb phrases like "Place order") and which actor performs each use case.
Group fine-grained requirements into meaningful use cases (aim for 4-15 use cases).

Requirements:
{reqs}

Return JSON:
{{"actors": [{{"name": "Customer", "kind": "human"}}, {{"name": "Payment Gateway", "kind": "system"}}],
  "use_cases": [{{"name": "Place order", "actors": ["Customer", "Payment Gateway"],
                  "requirement_ids": ["FR-1", "FR-4"]}}],
  "includes": [{{"base": "Place order", "included": "Log in"}}]}}"""

ISSUES = """Review this set of requirements as a whole.

Find:
1. conflicts: pairs/groups of requirements that contradict each other.
2. gaps: important things a requirements set like this would normally cover but does not
   (e.g. error handling, security, data retention, performance targets, accessibility,
   logging, backup). Only list gaps that genuinely matter for this system.
3. questions: the most important clarification questions to ask stakeholders.

Requirements:
{reqs}

Return JSON:
{{"conflicts": [{{"ids": ["FR-2", "NFR-1"], "explanation": "..."}}],
  "gaps": [{{"area": "Error handling", "explanation": "...", "suggested_requirement": "..."}}],
  "questions": [{{"question": "...", "related_ids": ["FR-3"]}}]}}"""

QA_SYSTEM = (
    "You answer questions about a software requirements set. Use only the information given. "
    "Cite requirement ids in square brackets, e.g. [FR-3], after each claim. If the answer is "
    "not in the requirements, say so plainly and suggest what requirement is missing. "
    "Use short paragraphs or bullet points."
)

QA_USER = """Requirements:
{reqs}
{doc}
Question: {question}"""
