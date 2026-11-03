"""Integration tests: need the tesseract binary (skipped otherwise)."""
import shutil

import pytest
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont

from bill2sheet.cli import main

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]


def draw_receipt(path, lines):
    font = None
    for f in FONTS:
        try:
            font = ImageFont.truetype(f, 34)
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no TrueType font available")
    img = Image.new("RGB", (900, 120 + 56 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 40 + 56 * i), line, fill="black", font=font)
    img.save(path)


LINES = ["ACME HARDWARE", "Subtotal 10.00", "VAT 20% 2.00", "Total EUR 12.00", "03/11/2026"]


def test_scan_end_to_end_and_dedupe(tmp_path, capsys):
    img = tmp_path / "r.png"
    draw_receipt(img, LINES)
    out = tmp_path / "b.xlsx"
    assert main(["scan", str(img), "-o", str(out), "--lang", "eng"]) == 0
    ws = load_workbook(out)["Bills"]
    assert ws["H2"].value == 12.0
    assert main(["scan", str(img), "-o", str(out), "--lang", "eng"]) == 0
    assert load_workbook(out)["Bills"].max_row == 2
    assert "already in" in capsys.readouterr().out


def test_watch_once(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    draw_receipt(inbox / "r.png", LINES)
    out = tmp_path / "b.xlsx"
    assert main(["watch", str(inbox), "-o", str(out), "--once", "--lang", "eng"]) == 0
    assert load_workbook(out)["Bills"].max_row == 2


def test_doctor_runs():
    assert main(["doctor"]) in (0, 1)
