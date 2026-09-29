"""Review UI: answers, the checks behind them, and the highlighted source page.

    uvicorn cdp_draft.server:app --port 8010
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from .report import Report

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / os.environ.get("CDP_RESULTS", "out/results.json")
EVAL = ROOT / "out" / "eval.json"

app = FastAPI(title="cdp-draft")


@lru_cache(maxsize=1)
def _report() -> Report:
    name = json.loads(RESULTS.read_text())["report"]
    return Report(ROOT / "data" / name)


@lru_cache(maxsize=256)
def _png(number: int, quotes: tuple[str, ...], focus: bool) -> tuple[bytes, float | None]:
    report = _report()
    rects = [r for q in quotes for r in report.page(number).find(q) or []]
    height = report.doc[number - 1].rect.height
    top = min(r.y0 for r in rects) / height if rects and not focus else None
    return report.render(number, list(quotes), focus=focus), top


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/results")
def results() -> JSONResponse:
    if not RESULTS.exists():
        raise HTTPException(404, "run python -m cdp_draft.run first")
    data = json.loads(RESULTS.read_text())
    if EVAL.exists():
        data["eval"] = json.loads(EVAL.read_text())
    return JSONResponse(data)


@app.get("/api/page/{number}.png")
def page(number: int, q: list[str] = Query(default=[]), focus: bool = False) -> Response:
    report = _report()
    if not 1 <= number <= len(report.pages):
        raise HTTPException(404, "no such page")
    png, top = _png(number, tuple(q), focus)
    headers = {"Cache-Control": "max-age=3600", "Access-Control-Expose-Headers": "X-Highlight-Top"}
    if top is not None:
        headers["X-Highlight-Top"] = f"{top:.4f}"
    return Response(png, media_type="image/png", headers=headers)
