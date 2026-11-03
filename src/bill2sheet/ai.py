"""Optional second opinion from Claude (``--ai``).

Runs on the machine's own terminal, so the key is read from the ANTHROPIC_API_KEY
environment variable and never written anywhere. Only the OCR *text* is sent, not the photo.
The model sees the parser's result so it can sanity-check the arithmetic.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date

from .parse import CATEGORIES, Receipt

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = f"""You correct structured data extracted from noisy OCR text of a receipt or invoice.
The OCR text may contain several passes over the same image; digits can be misread
(watermarks are a common cause). A deterministic parser already produced a first answer.
Verify it, using arithmetic (net + VAT = total) to resolve conflicting digits.
Reply with ONLY a JSON object, no prose and no code fences, with exactly these keys:
vendor (string), date (YYYY-MM-DD or ""), description (3-8 words), category (one of {CATEGORIES}),
net (number|null), vat_rate (number in percent|null), vat (number|null), total (number|null),
currency (ISO code like EUR). Use null when a value is genuinely not on the receipt."""


class AiError(RuntimeError):
    pass


def _post(body: dict, api_key: str) -> dict:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise AiError(f"Anthropic API error {e.code}: {e.read().decode()[:300]}") from e
    except urllib.error.URLError as e:
        raise AiError(f"could not reach the Anthropic API: {e.reason}") from e


def refine(receipt: Receipt, texts: list[str], model: str | None = None,
           api_key: str | None = None) -> Receipt:
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AiError("ANTHROPIC_API_KEY is not set")
    prompt = (
        "OCR passes:\n" + "\n\n".join(f"--- pass {i + 1} ---\n{t.strip()}" for i, t in enumerate(texts))
        + "\n\nParser result:\n" + json.dumps(receipt.to_dict(), ensure_ascii=False)
    )
    data = _post(
        {
            "model": model or os.environ.get("BILL2SHEET_MODEL", DEFAULT_MODEL),
            "max_tokens": 800,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
        api_key,
    )
    text = "".join(b.get("text", "") for b in data.get("content", []))
    try:
        out = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError as e:
        raise AiError(f"unparseable AI reply: {text[:200]!r}") from e

    r = replace(receipt, notes=list(receipt.notes))
    if out.get("vendor"):
        r.vendor = out["vendor"]
    if out.get("date"):
        try:
            r.date = date.fromisoformat(out["date"])
        except ValueError:
            pass
    if out.get("description"):
        r.description = out["description"]
    if out.get("category") in CATEGORIES:
        r.category = out["category"]
    for key, attr in (("net", "net"), ("vat_rate", "vat_rate"), ("vat", "vat"), ("total", "total")):
        if isinstance(out.get(key), (int, float)):
            setattr(r, attr, round(float(out[key]), 2))
    if out.get("currency"):
        r.currency = str(out["currency"]).upper()[:3]
    r.notes.append("fields reviewed by AI")
    if None not in (r.net, r.vat, r.total) and abs(r.net + r.vat - r.total) > 0.02:
        r.notes.append(f"CHECK: net {r.net:.2f} + VAT {r.vat:.2f} != total {r.total:.2f}")
    return r
