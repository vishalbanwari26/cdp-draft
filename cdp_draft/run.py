"""Run the whole thing: facts, checks, answers. Writes out/results.json.

    python -m cdp_draft.run data/HM_ASR25_en.pdf --year 2025
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from .answer import QUESTIONS, draft
from .checks import run_checks
from .facts import extract
from .llm import LLM
from .report import Report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--company", default="Heidelberg Materials")
    ap.add_argument("--out", default="out/results.json")
    args = ap.parse_args()
    load_dotenv()

    t0 = time.time()
    report = Report(args.pdf)
    llm = LLM()
    facts, rejected = extract(llm, report, args.year)
    checks = run_checks(facts, args.year)
    answers = [draft(llm, report, q) for q in QUESTIONS]

    result = {
        "company": args.company,
        "report": Path(args.pdf).name,
        "pages": len(report.pages),
        "year": args.year,
        "model": llm.model,
        "facts": [f.to_dict() for f in facts],
        "rejected_facts": rejected,
        "checks": [asdict(c) for c in checks],
        "answers": answers,
        "stats": {
            "model_calls": llm.calls,
            "from_cache": llm.cached,
            "tokens": llm.tokens,
            "seconds": round(time.time() - t0, 1),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False))

    print(f"{len(facts)} facts kept, {len(rejected)} rejected")
    for c in checks:
        print(f"  [{'ok' if c.ok else '!!'}] {c.title}: {c.detail}")
    for a in answers:
        print(f"  {a['id']:8} {a['status']:17} pages {a['retrieved_pages']}  attempts {a['attempts']}")
    print(f"model calls {llm.calls} (+{llm.cached} cached), {llm.tokens} tokens -> {out}")


if __name__ == "__main__":
    main()
