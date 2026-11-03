from datetime import date

from openpyxl import load_workbook

from bill2sheet.export import HEADERS, append_receipts, existing_hashes
from bill2sheet.parse import Receipt


def make(sha="a" * 64, total=54.60, cur="EUR", billable="Billable"):
    return Receipt(source="x.jpg", sha256=sha, date=date(2026, 8, 30), vendor="APRR",
                   category="Travel", description="Toll", net=45.5, vat_rate=20.0, vat=9.1,
                   total=total, currency=cur, billable=billable, notes=["n"])


def test_create_and_append(tmp_path):
    x = tmp_path / "b.xlsx"
    assert append_receipts(x, [make()]) == 1
    wb = load_workbook(x)
    ws = wb["Bills"]
    assert [c.value for c in ws[1]] == HEADERS
    assert ws["H2"].value == 54.60 and ws["F2"].value == 0.2
    assert wb["Summary"]["A2"].value == "EUR"
    assert len(ws.data_validations.dataValidation) == 2


def test_dedupe_by_hash(tmp_path):
    x = tmp_path / "b.xlsx"
    append_receipts(x, [make()])
    assert append_receipts(x, [make()]) == 0
    assert append_receipts(x, [make(sha="b" * 64)]) == 1
    assert existing_hashes(x) == {"a" * 64, "b" * 64}


def test_existing_manual_edits_survive_append(tmp_path):
    x = tmp_path / "b.xlsx"
    append_receipts(x, [make()])
    wb = load_workbook(x)
    wb["Bills"]["J2"] = "Non-billable"
    wb.save(x)
    append_receipts(x, [make(sha="c" * 64)])
    assert load_workbook(x)["Bills"]["J2"].value == "Non-billable"


def test_one_summary_row_per_currency(tmp_path):
    x = tmp_path / "b.xlsx"
    append_receipts(x, [make(), make(sha="d" * 64, cur="USD")])
    ws = load_workbook(x)["Summary"]
    assert [ws["A2"].value, ws["A3"].value] == ["EUR", "USD"]
    assert str(ws["B2"].value).startswith("=SUMIFS")
