"""Pull every absolute emissions figure out of the report into one table.

The model reads a page and lists the figures with the exact words they came
from. Code then decides what to keep: the quote must be on that page, the
number must be in the quote, and the unit must be a mass of CO2. A figure that
fails is sent back once with the reason, then dropped and counted.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .llm import LLM
from .report import Page, Report

SCOPES = {"1", "2", "2_location", "2_market", "3", "total_location", "total_market", "total"}

SYSTEM = """You extract greenhouse gas figures from one page of a company report.
Return JSON: {"facts": [ ... ]}. One entry per absolute emissions figure on the page:
{"scope": one of "1", "2_location", "2_market", "2" (Scope 2 with no method stated), "3",
          "total_location", "total_market", "total",
 "category": Scope 3 category number 1-15, a list of numbers if the figure combines
             several categories, or null,
 "segment": "group" for company-wide figures, otherwise the business line or source
            named in the text, lowercase (e.g. "cement", "aggregates"),
 "basis": "gross" or "net",
 "year": the year the figure is for. In tables, read the year from the column header;
         a dash means no value for that column,
 "value": the number only, as written (no unit),
 "unit": the unit as written,
 "quote": the exact words from the page that state the figure, copied character for character.
          For a table row, copy the row label followed by its numbers and unit.}
"total_location" and "total_market" mean Scope 1 + 2 + 3 combined. A row such as
"Total location-based Scope 2 emissions" is scope "2_location", not a total.
Rules:
- Only absolute emissions in tonnes. Skip intensities, per-tonne figures, percentages,
  targets, CO2 captured, transferred, stored or removed, and figures the text says are
  not included in a scope.
- The page text is followed by its tables with column headers. Use the tables to read
  years, but copy quotes from the page text, in the page text's order. Do not add units
  or words that are not there.
- Copy what the page says, even if it looks inconsistent with other figures. Do not fix it.
- A table row with a 2024 and a 2025 column gives two facts.
- If there are no such figures, return {"facts": []}."""

_UNITS = [
    (r"\b(million|mio\.?|mn)\s*t|\bmt\b|mtco2", 1.0),
    (r"\b(thousand|kilo)\s*t|\bkt\b", 0.001),
    (r"\bt\s*co2|\btonnes?\b|\bt\b", 1e-6),
]


def to_million_t(unit: str) -> float | None:
    u = unit.lower().replace(" ", " ")
    if "/" in u or "per " in u or "%" in u or "kg" in u:
        return None
    for pat, factor in _UNITS:
        if re.search(pat, u):
            return factor
    return None


def numbers_in(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", text.replace(" ", " "))]


_NOT_EMISSIONS = re.compile(r"\b(captured|transferred|storage|stored|removals?|removed|offsets?)\b", re.I)


def _first_number(v: object) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    if not m:
        raise ValueError(f"no number in {v!r}")
    return float(m.group(0).replace(",", ""))


def _category(v: object) -> tuple[int, ...] | None:
    if v in (None, "", "null", []):
        return None
    items = v if isinstance(v, list) else re.findall(r"\d+", str(v))
    cats = tuple(sorted({int(x) for x in items}))
    return cats or None


@dataclass
class Fact:
    scope: str
    category: tuple[int, ...] | None  # one Scope 3 category, or several combined
    segment: str
    basis: str
    year: int
    value: float  # as written
    unit: str
    mt: float  # million tonnes CO2e
    page: int
    quote: str

    def to_dict(self) -> dict:
        return asdict(self)


def _check(raw: dict, page: Page, max_year: int | None = None) -> tuple[Fact | None, str | None]:
    try:
        scope = str(raw["scope"]).lower()
        value = _first_number(raw["value"])
        year = int(_first_number(raw["year"]))
        quote = str(raw["quote"])
        unit = str(raw.get("unit") or "")
        category = _category(raw.get("category"))
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"malformed entry ({exc})"
    if scope not in SCOPES:
        return None, f"unknown scope {scope!r}"
    if _NOT_EMISSIONS.search(quote):
        return None, f"not an emission (capture, storage or removal): {quote[:60]!r}"
    if max_year is not None and year > max_year:
        return None, f"year {year} is after the reporting year, so this is a target, not an emission"
    plain = re.sub(r"not (?:reported|included) in scope[^)]*", "", quote.replace(" ", " ").replace(" ", " "), flags=re.I)
    named = set(re.findall(r"scope\s*([123])", plain, re.I))
    if scope.startswith("total") and len(named) == 1:
        return None, f"labelled {scope} but the quote names only Scope {named.pop()}; totals cover Scopes 1, 2 and 3"
    if scope[0] in "123" and named and scope[0] not in named:
        return None, f"labelled Scope {scope[0]} but the quote names Scope {', '.join(sorted(named))}"
    if scope[0] in "12" and not named:
        return None, f"the quote does not say which scope this is; quote the words that name Scope {scope[0]}"
    if scope in ("2_location", "2_market") and scope[2:].split("_")[0] not in plain.lower():
        return None, f"labelled {scope} but the quote does not state the method; use scope \"2\""
    if scope == "3" and category is None and not re.search(r"scope\s*3|total", quote, re.I):
        return None, "a Scope 3 figure with no category must be the stated Scope 3 total; give its category number"
    if not page.find(quote):
        return None, f"quote not found on page {page.number}: {quote[:80]!r}"
    if not any(abs(n - value) < 1e-9 for n in numbers_in(quote)):
        return None, f"value {value} does not appear in its quote"
    factor = to_million_t(unit)
    if factor is None:
        return None, f"unit {unit!r} is not an absolute mass of CO2"
    return (
        Fact(
            scope=scope,
            category=category,
            segment=str(raw.get("segment") or "group").lower().strip(),
            basis=str(raw.get("basis") or "gross").lower(),
            year=year,
            value=value,
            unit=unit,
            mt=round(value * factor, 6),
            page=page.number,
            quote=quote,
        ),
        None,
    )


def candidate_pages(report: Report) -> list[Page]:
    """Pages that state absolute emissions: they mention a scope and a CO2 mass."""
    out = []
    for p in report.pages:
        t = p.text.lower().replace(" ", " ")
        if re.search(r"scope\s*[123]", t) and re.search(r"(million|mio)\.?\s*t", t) and "co2" in t:
            out.append(p)
    return out


def extract_page(llm: LLM, report: Report, page: Page, year: int | None = None) -> tuple[list[Fact], list[str]]:
    user = f"Page {page.number}:\n\n{page.text}"
    tables = report.tables(page.number)
    if tables:
        user += "\n\nTables on this page, with column headers:\n\n" + "\n\n".join(tables)
    raw = llm.json(SYSTEM, user).get("facts", [])
    kept, errors = [], []
    for r in raw:
        f, err = _check(r, page, year)
        (kept.append(f) if f else errors.append(err))
    if errors:
        retry = llm.json(
            SYSTEM,
            user
            + "\n\nYour previous answer had entries that failed checks:\n- "
            + "\n- ".join(errors)
            + "\nReturn the corrected full list. Copy quotes exactly from the page.",
        ).get("facts", [])
        kept, errors = [], []
        for r in retry:
            f, err = _check(r, page, year)
            (kept.append(f) if f else errors.append(err))
    return kept, errors


def extract(llm: LLM, report: Report, year: int | None = None) -> tuple[list[Fact], list[dict]]:
    facts, rejected = [], []
    for page in candidate_pages(report):
        kept, errors = extract_page(llm, report, page, year)
        facts += kept
        rejected += [{"page": page.number, "reason": e} for e in errors]
    return facts, rejected
