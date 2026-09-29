"""The report as pages of text, plus the one check everything else relies on:
does this quote really appear on this page?

PDF text is messy. Words are split across lines with hyphens, soft hyphens sit
inside words, and table cells come out one per line. So a quote is matched as a
sequence of words, compared without punctuation or case, against the words
PyMuPDF finds on the page. The same match gives the rectangles to highlight, so
anything that passes validation can also be shown on the page.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pymupdf

SOFT_HYPHEN = "­"


def _norm_token(text: str) -> str:
    text = text.replace(SOFT_HYPHEN, "").replace("’", "'").lower()
    return re.sub(r"[^0-9a-z.%']", "", text).strip(".")


_NUMBER = re.compile(r"^[\d.,%]+$")


def _split(token: str) -> list[str]:
    """Split where letters meet digits, so "scope2" and "scope 2" compare equal."""
    return [p for p in re.findall(r"[0-9.,%]+|[^0-9.,%]+", token) if p.strip(".")]


def quote_tokens(quote: str) -> list[str]:
    quote = quote.replace(SOFT_HYPHEN, "")
    # Table extraction moves subscripts: "CO2eq" can come back as "CO eq 2".
    quote = re.sub(r"\bCO\s*(eq|e)?\s+2\b", r"CO2\1", quote)
    quote = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", quote)
    return [t for t in (_norm_token(w) for w in quote.split()) if t]


@dataclass
class Word:
    text: str
    rects: list[pymupdf.Rect]


@dataclass
class Page:
    number: int  # 1-based, as printed in the PDF viewer
    text: str
    words: list[Word]

    @cached_property
    def tokens(self) -> list[str]:
        return [w.text for w in self.words]

    @cached_property
    def _stream(self) -> tuple[str, list[int]]:
        # Words glued together with a non-breaking space ("Scope\u00a02") or split
        # oddly by the PDF are handled by comparing letters, not words.
        starts, pos = [], 0
        for t in self.tokens:
            starts.append(pos)
            pos += len(t)
        return "".join(self.tokens), starts

    @cached_property
    def _pieces(self) -> list[tuple[str, int]]:
        return [(p, i) for i, t in enumerate(self.tokens) for p in _split(t)]

    def find(self, quote: str) -> list[pymupdf.Rect] | None:
        """Rectangles of the quote on this page, or None if it is not there."""
        toks = quote_tokens(quote)
        q = "".join(toks)
        if len(q) < 12:
            return None
        stream, starts = self._stream
        at = stream.find(q)
        if at >= 0:
            end = at + len(q)
            return [r for w, s in zip(self.words, starts) if s < end and s + len(w.text) > at for r in w.rects]
        hit = self._find_skipping_columns([p for t in toks for p in _split(t)])
        return [r for i in hit for r in self.words[i].rects] if hit else None

    def _find_skipping_columns(self, q: list[str]) -> list[int] | None:
        """A table row quoted with only some of its columns: "Scope 1 ... 59.4
        million t" for a row that reads "Scope 1 ... 61.1 59.4 million t". Every
        word and number of the quote must appear in order; only numbers from the
        other columns may be skipped, at most a few in a row."""
        pieces = self._pieces
        for start, (p, _) in enumerate(pieces):
            if p != q[0]:
                continue
            i, j, skipped, used = start, 0, 0, []
            while i < len(pieces) and j < len(q):
                if pieces[i][0] == q[j]:
                    used.append(pieces[i][1])
                    i, j, skipped = i + 1, j + 1, 0
                elif _NUMBER.match(pieces[i][0]) and skipped < 4:
                    i, skipped = i + 1, skipped + 1
                else:
                    break
            if j == len(q):
                return sorted(set(used))
        return None


def _page_words(page: pymupdf.Page) -> list[Word]:
    raw = page.get_text("words")  # x0, y0, x1, y1, word, block, line, word_no
    words: list[Word] = []
    pending: Word | None = None
    for i, (x0, y0, x1, y1, text, block, line, _) in enumerate(raw):
        rect = pymupdf.Rect(x0, y0, x1, y1)
        clean = text.replace(SOFT_HYPHEN, "")
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        line_end = nxt is None or (nxt[5], nxt[6]) != (block, line)
        if pending is not None:
            pending.text += clean
            pending.rects.append(rect)
            clean, rect, word = "", None, pending
            pending = None
        else:
            word = Word(clean, [rect])
        # A word broken at the end of a line ("ecologi-" / "cal") is one word.
        if line_end and word.text.endswith("-") and len(word.text) > 2 and nxt is not None:
            word.text = word.text[:-1]
            pending = word
            continue
        word.text = _norm_token(word.text)
        if word.text:
            words.append(word)
    return words


class Report:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.doc = pymupdf.open(self.path)
        self.pages = [
            Page(i + 1, p.get_text().replace(SOFT_HYPHEN, ""), _page_words(p)) for i, p in enumerate(self.doc)
        ]
        self._df = Counter(t for p in self.pages for t in set(p.tokens))

    def page(self, number: int) -> Page:
        return self.pages[number - 1]

    def tables(self, number: int) -> list[str]:
        """Tables on a page as markdown, so column headers (years) stay attached
        to their numbers. Plain page text loses that for a row like '– – 61.3'."""
        try:
            found = self.doc[number - 1].find_tables()
        except Exception:  # noqa: BLE001 - table detection is best effort
            return []
        out = []
        for t in found.tables:
            rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()]
            keep = [i for i in range(len(rows[0]) if rows else 0) if any(r[i] for r in rows)]
            rows = [[r[i] for i in keep] for r in rows if any(r[i] for i in keep)]
            if len(rows) > 1:
                out.append("\n".join("| " + " | ".join(r) + " |" for r in rows))
        return out

    def search(self, query: str, k: int = 3) -> list[Page]:
        """BM25 over whole pages. Plain keyword search is enough for a report
        this structured, and every result can be explained by its terms."""
        terms = quote_tokens(query)
        n = len(self.pages)
        avg = sum(len(p.tokens) for p in self.pages) / n
        scored = []
        for p in self.pages:
            tf = Counter(p.tokens)
            s = 0.0
            for t in terms:
                if not tf[t]:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(p.tokens) / avg))
            scored.append((s, p.number))
        scored.sort(reverse=True)
        return [self.page(num) for s, num in scored[:k] if s > 0]

    def render(self, number: int, quotes: list[str], zoom: float = 1.6, focus: bool = False) -> bytes:
        """PNG of one page with each quote highlighted. With focus, only the
        area around the quote, larger, so it can be read on a screen recording."""
        page = self.doc[number - 1]
        shape = page.new_shape()
        found = [r for q in quotes for r in self.page(number).find(q) or []]
        for r in found:
            shape.draw_rect(r + (-1, -1, 1, 1))
        shape.finish(color=None, fill=(1.0, 0.82, 0.2), fill_opacity=0.38)
        shape.commit()
        clip = None
        if focus and found:
            box = pymupdf.Rect(found[0])
            for r in found[1:]:
                box |= r
            w = max(box.width + 120, 460)
            cx = (box.x0 + box.x1) / 2
            clip = pymupdf.Rect(cx - w / 2, box.y0 - 110, cx + w / 2, box.y1 + 110) & page.rect
            zoom = 2.6
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        # Draw on a copy only: reopen so later renders start clean.
        self.doc = pymupdf.open(self.path)
        return pix.tobytes("png")
