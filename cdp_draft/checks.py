"""Arithmetic the model is not trusted with.

Two kinds of check over the fact table:
- sums: business lines add up to the company figure, Scope 3 categories add up
  to Scope 3, and Scope 1 + 2 + 3 adds up to the stated total;
- conflicts: the report states the same figure twice with different values.

Report figures are rounded, usually to one decimal in million tonnes, so each
sum is allowed half a rounding step per term. Anything outside that goes to a
person with both sides of the disagreement cited.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .facts import Fact

SCOPE_LABEL = {
    "1": "Scope 1",
    "2": "Scope 2 (method not stated)",
    "2_location": "Scope 2 location-based",
    "2_market": "Scope 2 market-based",
    "3": "Scope 3",
    "total_location": "Total (location-based)",
    "total_market": "Total (market-based)",
    "total": "Total",
}


def _decimals(v: float) -> int:
    s = f"{v}"
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _step(facts: list[Fact]) -> float:
    """Half the rounding step of the least precise figure, in million t."""
    worst = 0.0
    for f in facts:
        step = 10 ** (-_decimals(f.value)) * (f.mt / f.value if f.value else 1)
        worst = max(worst, step / 2)
    return worst


@dataclass
class Term:
    label: str
    mt: float
    page: int
    quote: str


@dataclass
class Check:
    kind: str  # "sum" or "conflict"
    title: str
    ok: bool
    detail: str
    scopes: list[str]
    terms: list[Term] = field(default_factory=list)
    stated: Term | None = None


def _term(f: Fact, label: str | None = None) -> Term:
    return Term(label or f.segment, f.mt, f.page, f.quote)


def _one(facts: list[Fact]) -> Fact:
    # Several pages can repeat a figure; the table row is the usual source.
    return sorted(facts, key=lambda f: (-len(f.quote), f.page))[0]


def index(facts: list[Fact]) -> dict[tuple, list[Fact]]:
    idx: dict[tuple, list[Fact]] = defaultdict(list)
    for f in facts:
        # Net figures and "thereof" rows (a part of a category, shown under it)
        # are real but do not take part in the sums.
        if f.basis == "net" or re.search(r"\b(thereof|of which)\b", f.quote, re.I):
            continue
        idx[(f.scope, f.category, f.segment, f.year)].append(f)
    return idx


def sum_check(title: str, scopes: list[str], stated: Fact, parts: list[tuple[str, Fact]]) -> Check:
    total = sum(p.mt for _, p in parts)
    tol = _step([stated] + [p for _, p in parts]) * (len(parts) + 1) + 1e-9
    diff = total - stated.mt
    ok = abs(diff) <= tol
    detail = (
        f"{' + '.join(f'{p.mt:g}' for _, p in parts)} = {total:.2f} vs stated {stated.mt:g} million t"
        + (f" (difference {diff:+.2f}, within rounding)" if ok and abs(diff) > 1e-9 else "")
        + ("" if ok else f" (difference {diff:+.2f}, more than rounding allows)")
    )
    return Check("sum", title, ok, detail, scopes, [_term(p, l) for l, p in parts], _term(stated, "stated"))


def run_checks(facts: list[Fact], year: int) -> list[Check]:
    idx = index(facts)
    checks: list[Check] = []

    def group(scope: str, category: int | None = None) -> Fact | None:
        hits = idx.get((scope, category, "group", year))
        return _one(hits) if hits else None

    # Business lines add up to the company figure.
    for scope in ("1", "2_location", "2_market"):
        stated = group(scope)
        parts = [
            (seg, _one(fs))
            for (s, c, seg, y), fs in sorted(idx.items(), key=lambda kv: kv[0][2])
            if s == scope and c is None and y == year and seg != "group"
        ]
        if stated and len(parts) >= 2:
            checks.append(sum_check(f"{SCOPE_LABEL[scope]}: business lines add up", [scope.split("_")[0]], stated, parts))

    # Scope 3 categories add up to Scope 3.
    stated3 = group("3")
    single = {
        c[0]: _one(fs)
        for (s, c, seg, y), fs in idx.items()
        if s == "3" and c is not None and len(c) == 1 and seg == "group" and y == year
    }
    cats = [(f"category {c}", single[c]) for c in sorted(single)]
    if stated3 and len(cats) >= 2:
        checks.append(sum_check("Scope 3: categories add up", ["3"], stated3, cats))

    # A figure given for several categories together matches those categories.
    for (s, c, seg, y), fs in sorted(idx.items(), key=lambda kv: str(kv[0][1])):
        if s == "3" and c is not None and len(c) > 1 and seg == "group" and y == year and all(x in single for x in c):
            name = " + ".join(str(x) for x in c)
            checks.append(
                sum_check(f"Scope 3: combined figure for categories {name}", ["3"], _one(fs), [(f"category {x}", single[x]) for x in c])
            )

    # Scope 1 + 2 + 3 adds up to the total, for each Scope 2 method.
    s1 = group("1")
    for method in ("location", "market"):
        s2, tot = group(f"2_{method}"), group(f"total_{method}")
        if s1 and s2 and stated3 and tot:
            checks.append(
                sum_check(
                    f"Scope 1 + 2 + 3 = total ({method}-based)",
                    ["1", "2", "3", "total"],
                    tot,
                    [("Scope 1", s1), (f"Scope 2 {method}", s2), ("Scope 3", stated3)],
                )
            )

    # The same figure stated twice with different values.
    for (scope, cat, seg, y), fs in sorted(idx.items(), key=lambda kv: (kv[0][2], kv[0][3], kv[0][0])):
        others = list(fs)
        compare_label = SCOPE_LABEL[scope]
        if scope == "2":
            # "Scope 2" without a method must match at least one of the methods.
            others += idx.get(("2_location", cat, seg, y), []) + idx.get(("2_market", cat, seg, y), [])
        values = sorted({round(f.mt, 6) for f in others})
        if len(values) < 2:
            continue
        tol = _step(others) * 2 + 1e-9
        if scope == "2":
            own = {round(f.mt, 6) for f in fs}
            method_vals = {round(f.mt, 6) for f in others if f.scope != "2"}
            if not method_vals or any(abs(a - b) <= tol for a in own for b in method_vals):
                continue
        elif values[-1] - values[0] <= tol:
            continue
        distinct = {}
        for f in sorted(others, key=lambda f: (f.page, -len(f.quote))):
            distinct.setdefault((f.scope, round(f.mt, 6), f.page), f)
        terms = [_term(f, f"{SCOPE_LABEL[f.scope]}, page {f.page}") for f in distinct.values()]
        where = f"category {'+'.join(map(str, cat))}, " if cat else ""
        checks.append(
            Check(
                "conflict",
                f"{compare_label}, {where}{seg}, {y}: the report gives different values",
                False,
                "; ".join(f"{t.mt:g} million t on page {t.page}" for t in terms),
                [scope.split("_")[0]],
                terms,
            )
        )
    return checks
