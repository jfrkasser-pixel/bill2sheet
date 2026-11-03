import json

import pytest

from bill2sheet import ai
from bill2sheet.parse import parse_receipt


def reply(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj)}]}


def base():
    return parse_receipt("Prix HT 45.50 EUR\nTVA 20.00% 9.10 EUR\nPrix TTC 54.60 EUR\n", source="a.jpg")


def test_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ai.AiError):
        ai.refine(base(), ["x"])


def test_applies_fields_and_does_not_mutate_input(monkeypatch):
    monkeypatch.setattr(ai, "_post", lambda body, key: reply(
        {"vendor": "APRR", "date": "2026-08-30", "description": "Motorway toll", "category": "Travel",
         "net": 45.5, "vat_rate": 20, "vat": 9.1, "total": 54.6, "currency": "eur"}))
    orig = base()
    out = ai.refine(orig, ["x"], api_key="k")
    assert out.vendor == "APRR" and out.description == "Motorway toll" and out.currency == "EUR"
    assert "fields reviewed by AI" in out.notes and "fields reviewed by AI" not in orig.notes


def test_flags_inconsistent_arithmetic(monkeypatch):
    monkeypatch.setattr(ai, "_post", lambda b, k: reply({"net": 45.5, "vat": 9.1, "total": 64.6}))
    out = ai.refine(base(), ["x"], api_key="k")
    assert any(n.startswith("CHECK") for n in out.notes)


def test_invalid_category_ignored_and_fences_tolerated(monkeypatch):
    txt = '```json\n{"category": "Nonsense", "total": 54.6}\n```'
    monkeypatch.setattr(ai, "_post", lambda b, k: {"content": [{"text": txt}]})
    orig = base()
    out = ai.refine(orig, ["x"], api_key="k")
    assert out.category == orig.category  # invalid value ignored
    assert out.total == 54.6


def test_garbage_reply_raises(monkeypatch):
    monkeypatch.setattr(ai, "_post", lambda b, k: {"content": [{"text": "sorry"}]})
    with pytest.raises(ai.AiError):
        ai.refine(base(), ["x"], api_key="k")
