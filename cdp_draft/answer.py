"""Draft an answer to a questionnaire question, with evidence, or say it is missing.

The model gets the few pages retrieval found and must quote them. Code checks
every quote against its page and every number against its quote. An answer
that fails is sent back once with the reasons; if it still fails it is marked
for review rather than shown as an answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .facts import _first_number, numbers_in
from .llm import LLM
from .report import Report


@dataclass
class Question:
    id: str
    scope: str | None  # links the question to the arithmetic checks
    text: str
    search: str  # words for retrieval, written once per question like a question bank


# CDP-style climate questions, paraphrased (not the official CDP wording).
QUESTIONS = [
    Question(
        "scope1",
        "1",
        "What were the organisation's gross global Scope 1 emissions in the reporting year, in metric tonnes CO2e?",
        "gross scope 1 greenhouse gas emissions total million t CO2eq inventory",
    ),
    Question(
        "scope2",
        "2",
        "What were the organisation's gross global Scope 2 emissions in the reporting year, location-based and market-based?",
        "total location-based market-based scope 2 GHG emissions million t CO2eq",
    ),
    Question(
        "scope3",
        "3",
        "What were the organisation's gross global Scope 3 emissions in the reporting year, by relevant category?",
        "total gross indirect scope 3 GHG emissions categories purchased goods transportation investments",
    ),
    Question(
        "targets",
        None,
        "Did the organisation have an emissions reduction target active in the reporting year? Give the scope covered, base year, target year and the reduction targeted.",
        "target 2030 reduce scope 1 CO2 emissions base year 2020 net zero 2050 scope 3 target",
    ),
    Question(
        "by_gas",
        "1",
        "Break down the organisation's gross global Scope 1 emissions by greenhouse gas type (CO2, CH4, N2O, HFCs, PFCs, SF6, NF3).",
        "scope 1 emissions by greenhouse gas type CO2 methane CH4 nitrous oxide N2O",
    ),
]

SYSTEM = """You draft answers to a sustainability questionnaire from a company's own report.
You are given report pages. Answer only from them.
Return JSON:
{"status": "answered" | "missing_evidence",
 "answer": "the answer in two or three plain sentences, with figures and units",
 "values": [{"label": "...", "value": number as written in the report, "unit": "...", "year": 2025}],
 "citations": [{"page": page number, "quote": "exact words from that page, copied character for character"}],
 "missing": "what the question asks for that the pages do not state, or empty"}
Rules:
- Every figure in "values" must appear in one of the quoted passages.
- For a table figure, quote the row label followed by its numbers and unit.
- If the pages do not state what is asked, use status "missing_evidence" and say what is missing.
  Do not estimate, derive, or fill gaps from general knowledge.
- Prefer the company-wide (Group) figure. Say which reporting year the figures are for."""


def _check(out: dict, report: Report, allowed: set[int]) -> list[str]:
    errors = []
    status = out.get("status")
    if status not in ("answered", "missing_evidence"):
        errors.append(f"status must be answered or missing_evidence, got {status!r}")
    cites = out.get("citations") or []
    if status == "answered" and not cites:
        errors.append("an answer needs at least one citation")
    quotes = []
    for c in cites:
        try:
            page = int(c["page"])
            quote = str(c["quote"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"malformed citation {c!r}")
            continue
        if page not in allowed:
            errors.append(f"page {page} was not among the pages provided")
        elif not report.page(page).find(quote):
            errors.append(f"quote not found on page {page}: {quote[:80]!r}")
        else:
            quotes.append(quote)
    for v in out.get("values") or []:
        try:
            value = _first_number(v["value"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"malformed value {v!r}")
            continue
        if not any(abs(n - abs(value)) < 1e-9 for q in quotes for n in numbers_in(q)):
            errors.append(f"value {value} ({v.get('label')}) is not in any verified quote")
    return errors


def draft(llm: LLM, report: Report, q: Question, k: int = 3) -> dict:
    pages = report.search(q.search, k=k)
    allowed = {p.number for p in pages}
    context = "\n\n".join(f"=== Page {p.number} ===\n{p.text}" for p in pages)
    user = f"Question: {q.text}\n\nReport pages:\n\n{context}"
    out = llm.json(SYSTEM, user)
    errors = _check(out, report, allowed)
    attempts = 1
    if errors:
        attempts = 2
        out = llm.json(
            SYSTEM,
            user
            + "\n\nYour previous answer failed these checks:\n- "
            + "\n- ".join(errors)
            + "\nReturn a corrected answer. Copy quotes exactly from the pages.",
        )
        errors = _check(out, report, allowed)
    status = out.get("status") if not errors else "needs_review"
    return {
        "id": q.id,
        "question": q.text,
        "scope": q.scope,
        "retrieved_pages": sorted(allowed),
        "status": status,
        "answer": out.get("answer", ""),
        "values": out.get("values") or [],
        "citations": out.get("citations") or [],
        "missing": out.get("missing") or "",
        "attempts": attempts,
        "errors": errors,
    }
