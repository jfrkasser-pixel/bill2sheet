"""xlsx ledger writer.

* ``Bills`` sheet: one row per receipt. Appending never touches existing rows, so manual
  edits made in Excel/LibreOffice survive later runs (including ``watch``).
* ``Summary`` sheet: SUMIFS formulas per currency, so changing a Billable cell updates totals.
* The SHA-256 of each source file is stored in the last column and used to skip duplicates.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .parse import CATEGORIES, Receipt

HEADERS = [
    "Date", "Vendor", "Category", "Description", "Net", "VAT rate", "VAT",
    "Total", "Currency", "Billable", "Source file", "Notes", "Hash",
]
COL = {name: get_column_letter(i + 1) for i, name in enumerate(HEADERS)}
WIDTHS = [12, 22, 14, 34, 10, 9, 10, 10, 9, 13, 24, 48, 14]
MAX_ROWS = 5000  # range covered by dropdowns


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bills"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="3F6B4E")
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    cat = DataValidation(type="list", formula1='"' + ",".join(CATEGORIES) + '"', allow_blank=True)
    bill = DataValidation(type="list", formula1='"Billable,Non-billable"', allow_blank=False)
    cat.add(f"{COL['Category']}2:{COL['Category']}{MAX_ROWS}")
    bill.add(f"{COL['Billable']}2:{COL['Billable']}{MAX_ROWS}")
    ws.add_data_validation(cat)
    ws.add_data_validation(bill)
    return wb


def existing_hashes(xlsx: Path) -> set[str]:
    xlsx = Path(xlsx)
    if not xlsx.exists():
        return set()
    wb = load_workbook(xlsx, read_only=True)
    try:
        ws = wb["Bills"]
        idx = HEADERS.index("Hash")
        return {
            str(row[idx]) for row in ws.iter_rows(min_row=2, values_only=True)
            if len(row) > idx and row[idx]
        }
    finally:
        wb.close()


def _rebuild_summary(wb: Workbook) -> None:
    if "Summary" in wb.sheetnames:
        del wb["Summary"]
    ws_bills = wb["Bills"]
    cur_idx = HEADERS.index("Currency")
    currencies = sorted({
        row[cur_idx] for row in ws_bills.iter_rows(min_row=2, values_only=True)
        if len(row) > cur_idx and row[cur_idx]
    })
    ws = wb.create_sheet("Summary")
    ws.append(["Currency", "Billable", "Non-billable", "All", "VAT included"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="3F6B4E")
    total, cur, bill, vat = COL["Total"], COL["Currency"], COL["Billable"], COL["VAT"]
    for i, c in enumerate(currencies, start=2):
        rng = lambda col: f"Bills!${col}$2:${col}${MAX_ROWS}"  # noqa: E731
        ws.append([
            c,
            f'=SUMIFS({rng(total)},{rng(cur)},$A{i},{rng(bill)},"Billable")',
            f'=SUMIFS({rng(total)},{rng(cur)},$A{i},{rng(bill)},"Non-billable")',
            f"=B{i}+C{i}",
            f"=SUMIFS({rng(vat)},{rng(cur)},$A{i})",
        ])
        for col in "BCDE":
            ws[f"{col}{i}"].number_format = "#,##0.00"
    for letter, w in zip("ABCDE", (10, 14, 14, 14, 14)):
        ws.column_dimensions[letter].width = w


def append_receipts(xlsx: Path, receipts: list[Receipt]) -> int:
    """Append receipts (skipping duplicates by hash). Returns the number of rows added."""
    xlsx = Path(xlsx)
    wb = load_workbook(xlsx) if xlsx.exists() else _new_workbook()
    ws = wb["Bills"]
    seen = existing_hashes(xlsx) if xlsx.exists() else set()
    added = 0
    for r in receipts:
        if r.sha256 and r.sha256 in seen:
            continue
        ws.append([
            r.date, r.vendor, r.category, r.description, r.net,
            (r.vat_rate / 100) if r.vat_rate is not None else None,
            r.vat, r.total, r.currency, r.billable, r.source,
            "; ".join(r.notes), r.sha256,
        ])
        row = ws.max_row
        ws[f"{COL['Date']}{row}"].number_format = "yyyy-mm-dd"
        ws[f"{COL['VAT rate']}{row}"].number_format = "0.0%"
        for name in ("Net", "VAT", "Total"):
            ws[f"{COL[name]}{row}"].number_format = "#,##0.00"
        ws[f"{COL['Notes']}{row}"].alignment = Alignment(wrap_text=True, vertical="top")
        seen.add(r.sha256)
        added += 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(ws.max_row, 2)}"
    _rebuild_summary(wb)
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return added
