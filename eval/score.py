"""Score the drafted answers against answers written by hand from the report.

    python -m eval.score            # reads out/results.json, writes out/eval.json

An answer is correct when its status matches, one accepted set of figures all
appear among its verified values, and it states no forbidden figure (a real
number given the wrong year, for example). A figure the model states but could
not quote never reaches this point: validation already sent that answer to review.
"""

from __future__ import annotations

import json
from pathlib import Path

from cdp_draft.facts import _first_number

ROOT = Path(__file__).resolve().parent.parent


def _values(answer: dict) -> list[tuple[float, object]]:
    out = []
    for v in answer["values"]:
        try:
            out.append((abs(_first_number(v["value"])), v.get("year")))
        except (KeyError, ValueError):
            pass
    return out


def score(results: dict, gold: dict) -> dict:
    rows = []
    for a in results["answers"]:
        g = gold["answers"].get(a["id"])
        if g is None:
            continue
        vals = _values(a)
        has = lambda x: any(abs(x - v) < 1e-9 for v, _ in vals)  # noqa: E731
        sets = g.get("accept_any") or [[]]
        best = min(([x for x in s if not has(x)] for s in sets), key=len)
        forbidden = [
            f for f in g.get("forbid", [])
            if any(abs(f["value"] - v) < 1e-9 and str(y) == str(f["year"]) for v, y in vals)
        ]
        correct = a["status"] == g["status"] and not best and not forbidden
        if correct:
            note = g["note"]
        elif a["status"] != g["status"]:
            note = f"Expected {g['status']}, got {a['status']}. {g['note']}"
        elif forbidden:
            note = "States " + ", ".join(f"{f['value']:g} as the {f['year']} figure" for f in forbidden) + f", which is wrong. {g['note']}"
        else:
            note = f"Missing {', '.join(f'{m:g}' for m in best)}. {g['note']}"
        rows.append({"id": a["id"], "correct": correct, "note": note})
    return {"correct": sum(r["correct"] for r in rows), "total": len(rows), "answers": rows}


def main() -> None:
    results = json.loads((ROOT / "out" / "results.json").read_text())
    gold = json.loads((ROOT / "eval" / "gold.json").read_text())
    out = score(results, gold)
    (ROOT / "out" / "eval.json").write_text(json.dumps(out, indent=1))
    for r in out["answers"]:
        print(f"  {'ok ' if r['correct'] else 'BAD'} {r['id']:8} {'' if r['correct'] else r['note']}")
    print(f"{out['correct']}/{out['total']} correct")


if __name__ == "__main__":
    main()
