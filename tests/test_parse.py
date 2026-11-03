from datetime import date
from pathlib import Path

import pytest

from bill2sheet.parse import parse_receipt

FIX = Path(__file__).parent / "fixtures"


def toll_passes() -> list[str]:
    return [(FIX / n).read_text() for n in
            ("toll_pass_raw.txt", "toll_pass_thr90.txt", "toll_pass_thr130.txt")]


def test_toll_receipt_with_watermark_damage():
    """Real-world case: watermark garbles the TVA/TTC lines in every OCR pass."""
    r = parse_receipt(toll_passes(), source="toll.jpg")
    assert (r.net, r.vat_rate, r.vat, r.total) == (45.50, 20.0, 9.10, 54.60)
    assert r.vendor == "APRR"
    assert r.date == date(2026, 8, 30)
    assert r.description == "Toll VIRY → FLEURY 1"
    assert r.category == "Travel"
    assert r.currency == "EUR"


def test_toll_receipt_total_misread_in_all_passes_is_flagged():
    """Drop the one pass that read 54.60 cleanly: total must come from HT+TVA, with a note."""
    r = parse_receipt(toll_passes()[:2], source="toll.jpg")
    assert (r.net, r.vat, r.total) == (45.50, 9.10, 54.60)
    assert r.notes  # the correction is explained, never silent


def test_ttc_misread_in_every_pass_is_derived_from_ht_and_tva():
    text = "Prix HT 45.50 EUR\nTVA 20.00% 9.10 EUR\nPrix TTC 64.60 EUR\n"
    r = parse_receipt(text)
    assert r.total == 54.60


def test_total_line_missing_entirely():
    r = parse_receipt("Prix HT 45.50 EUR\nTVA 20.00% 9.10 EUR\n")
    assert r.total == 54.60
    assert any("derived" in n for n in r.notes)


def test_vat_amount_unreadable_is_derived_from_rate():
    r = parse_receipt("Prix HT 45.50 EUR\nTVA 20.00% ???\nPrix TTC 54.60 EUR\n")
    assert (r.vat, r.total) == (9.10, 54.60)


def test_clean_receipt_has_no_notes():
    r = parse_receipt("Prix HT 45.50 EUR\nTVA 20.00% 9.10 EUR\nPrix TTC 54.60 EUR\n")
    assert r.total == 54.60 and r.notes == []


@pytest.mark.parametrize("label", ["Prix T.T.C.", "Prix TTC", "TOTAL TTC", "Prix T.T.C", "MONTANT"])
def test_total_label_variants(label):
    r = parse_receipt(f"Prix H.T. 45.50 EUR\nTVA 20.00% 9.10 EUR\n{label} 54.60 EUR\n")
    assert r.total == 54.60
    assert r.notes == []


def test_ht_line_is_never_mistaken_for_the_total():
    r = parse_receipt("Prix H.T. 45.50 EUR\n")
    assert r.total != 45.50 or r.net == 45.50  # net recognised; total not silently the HT figure
    assert r.net == 45.50


def test_total_only_receipt_backs_out_net_when_rate_known():
    r = parse_receipt("TVA 20%\nTOTAL 12,00 €\n")
    assert r.total == 12.00
    assert (r.net, r.vat) == (10.00, 2.00)
    assert r.currency == "EUR"


def test_comma_decimals_and_thousands():
    r = parse_receipt("Total 1 234,56 €\n")
    assert r.total == 1234.56


def test_english_receipt():
    r = parse_receipt("ACME Hardware\nSubtotal 10.00\nTax 8% 0.80\nTotal $10.80\n03/11/2026\n")
    assert (r.net, r.vat_rate, r.vat, r.total) == (10.00, 8.0, 0.80, 10.80)
    assert r.currency == "USD"


def test_us_style_date_is_swapped_when_unambiguous():
    assert parse_receipt("Total 1.00\n12/25/2026\n").date == date(2026, 12, 25)


def test_vat_registration_number_line_is_not_parsed_as_vat():
    r = parse_receipt("N° TVA FR12345678901\nPrix HT 10.00 EUR\nTVA 20% 2.00 EUR\nPrix TTC 12.00 EUR\n")
    assert (r.vat, r.total) == (2.00, 12.00)


def test_known_vendor_keeps_clean_full_line_but_collapses_junk():
    assert parse_receipt("Station Intermarché Talence\nTotal 10.00\n").vendor == "Station Intermarché Talence"
    assert parse_receipt("=JAPRR\nTotal 10.00\n").vendor == "APRR"


def test_fuel_category():
    assert parse_receipt("Station Intermarché Talence\nGazole 51.11 L\nTotal 106.97\n").category == "Fuel"


def test_no_amounts_asks_for_manual_entry():
    r = parse_receipt("blurry nonsense\n")
    assert r.total is None and any("by hand" in n for n in r.notes)
