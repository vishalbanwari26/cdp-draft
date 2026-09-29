"""One JSON-returning call to a hosted model, with retries and a disk cache.

The cache matters for a demo: every answer shown was produced by a real model
call, and replaying it from disk costs nothing and gives the same result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

DEFAULT_MODEL = os.environ.get("CDP_MODEL", "openai/gpt-oss-120b")
REASONING = os.environ.get("CDP_REASONING", "medium")
CACHE = Path(os.environ.get("CDP_CACHE", "cache"))


def _wait_hint(exc: Exception) -> float:
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(exc))
    if not m:
        return 0.0
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + 60 * float(m.group(1) or 0)


class LLM:
    def __init__(self, model: str = DEFAULT_MODEL, cache: Path = CACHE) -> None:
        self.model = model
        self.cache = cache
        self.calls = 0
        self.cached = 0
        self.tokens = 0
        self._client = None

    def _client_(self):
        if self._client is None:
            from groq import Groq  # imported only when a live call is needed

            if not os.environ.get("GROQ_API_KEY"):
                raise RuntimeError("GROQ_API_KEY is not set")
            self._client = Groq()
        return self._client

    def json(self, system: str, user: str) -> dict:
        key = hashlib.sha256(f"{self.model}\n{REASONING}\n{system}\n{user}".encode()).hexdigest()[:24]
        path = self.cache / f"{key}.json"
        if path.exists():
            self.cached += 1
            return json.loads(path.read_text())["output"]
        out = self._call(system, user)
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "system": system, "user": user, "output": out}, indent=1))
        return out

    def _call(self, system: str, user: str) -> dict:
        json_mode = True
        for attempt in range(8):
            try:
                extra = {"reasoning_effort": REASONING} if "gpt-oss" in self.model else {}
                resp = self._client_().chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"} if json_mode else None,
                    temperature=0,
                    max_completion_tokens=8192,
                    **extra,
                )
                self.calls += 1
                self.tokens += resp.usage.total_tokens if resp.usage else 0
                content = resp.choices[0].message.content or ""
                m = re.search(r"\{.*\}", content, re.S)
                return json.loads(m.group(0) if m else content)
            except json.JSONDecodeError:
                continue
            except Exception as exc:  # noqa: BLE001 - the SDK raises its own types
                text = str(exc)
                if "json_validate_failed" in text:
                    # Groq's JSON mode sometimes rejects long outputs outright;
                    # plain mode plus parsing the object out works instead.
                    json_mode = False
                    continue
                if "429" in text or "rate_limit" in text:
                    if ("tokens per day" in text or "TPD" in text) and _wait_hint(exc) > float(os.environ.get("CDP_MAX_WAIT", "900")):
                        raise  # the daily budget is gone; waiting minutes will not help
                    time.sleep(max(_wait_hint(exc), 5.0 * (attempt + 1)) + 0.5)
                    continue
                raise
        raise RuntimeError("model call failed after retries")
