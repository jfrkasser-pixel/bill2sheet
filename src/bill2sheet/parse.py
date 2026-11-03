"""Turn noisy OCR text (several variants of one receipt) into a structured Receipt.

Key idea: never trust a single OCR line for money. Collect *candidates* for net (HT),
VAT rate, VAT amount and total (TTC) from every OCR variant, then keep the combination
that is arithmetically consistent (net + VAT = total). If the total line is unreadable
but net and VAT are, the total is derived and the receipt gets a note saying so.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date

CATEGORIES = [
    "Other", "Meals", "Equipment", "Medical", "Fuel",
    "Parking", "Supplies", "Travel", "Accommodation",
]
STANDARD_RATES = {2.1, 5.5, 7.0, 8.5, 10.0, 19.0, 20.0, 21.0, 25.0}
CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}

AMOUNT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[ .,]\d{3})*[.,]\d{2}(?!\d)")
DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b")

HT_RE = re.compile(r"\bh\.?\s?t\.?(?![a-z])|hors\s+tax|sub-?total|sous-?total|net amount", re.I)
TTC_RE = re.compile(r"\bt\.?\s?t\.?\s?c\.?(?![a-z])", re.I)
TOTAL_RE = re.compile(
    r"\b(total|montant|[àa] payer|amount due|balance due|grand total)\b", re.I
)
VAT_RE = re.compile(r"\b(tva|vat|iva|mwst|tax)\b", re.I)
VAT_ID_RE = re.compile(r"n[°o]\s*tva|vat\s*(no|id|number)|siret|intracom", re.I)

KNOWN_VENDORS = {
    # toll operators
    "APRR": "APRR", "SANEF": "SANEF", "COFIROUTE": "Cofiroute", "ESCOTA": "Escota",
    "VINCI AUTOROUTES": "Vinci Autoroutes", "SAPN": "SAPN",
    # retail / fuel / pharmacy
    "INTERMARCHE": "Intermarché", "CARREFOUR": "Carrefour", "LECLERC": "E.Leclerc",
    "AUCHAN": "Auchan", "MONOPRIX": "Monoprix", "LIDL": "Lidl", "CASINO": "Casino",
    "TOTALENERGIES": "TotalEnergies", "SHELL": "Shell", "PHARMACIE": "Pharmacie",
    "WALMART": "Walmart", "COSTCO": "Costco",
}

CATEGORY_RULES = [
    ("Medical", r"PHARMAC|MEDIC|DOCTOR|CLINIC"),
    ("Fuel", r"STATION|FUEL|GAZOLE|ESSENCE|SHELL|TOTALENERGIES|CARBURANT"),
    ("Parking", r"PARKING|GARAGE"),
    ("Accommodation", r"HOTEL|LODG|\bINN\b"),
    ("Meals", r"RESTAURANT|CAFE|BISTRO|BRASSERIE|BOULANGERIE"),
    ("Travel", r"TOLL|PEAGE|AUTOROUTE|APRR|SANEF|COFIROUTE|ESCOTA|TAXI|UBER|TRAIN|SNCF|FLIGHT|AIRLINE"),
    ("Supplies", r"INTERMARCHE|CARREFOUR|CASINO|LECLERC|AUCHAN|MONOPRIX|LIDL|WALMART|COSTCO|SUPERMARKET"),
]


@dataclass
class Receipt:
    source: str = ""
    sha256: str = ""
    date: date | None = None
    vendor: str = ""
    category: str = "Other"
    description: str = ""
    net: float | None = None
    vat_rate: float | None = None
    vat: float | None = None
    total: float | None = None
    currency: str = "EUR"
    billable: str = "Billable"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        return d


# ---------------------------------------------------------------- helpers

def _fold(s: str) -> str:
    """Upper-case and strip accents so INTERMARCHÉ == INTERMARCHE."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.upper()) if unicodedata.category(c) != "Mn"
    )


def parse_amount(text: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def amounts_in(line: str) -> list[float]:
    return [a for a in (parse_amount(m) for m in AMOUNT_RE.findall(line)) if a is not None]


def _close(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


def _mode(values: list):
    return Counter(values).most_common(1)[0][0] if values else None


def _lines(texts: list[str]) -> list[str]:
    return [ln.strip() for t in texts for ln in t.splitlines() if ln.strip()]


# ---------------------------------------------------------------- field extractors

def extract_date(lines: list[str]) -> date | None:
    found = []
    for ln in lines:
        for m in DATE_RE.finditer(ln):
            d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
            if len(y) == 2:
                y = "20" + y
            if mo > 12 >= d:  # MM/DD/YYYY
                d, mo = mo, d
            try:
                found.append(date(int(y), mo, d))
            except ValueError:
                continue
    return _mode(found)  # most frequent survives single-variant OCR errors


def extract_currency(lines: list[str], default: str) -> str:
    blob = "\n".join(lines)
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in blob:
            return code
    m = re.search(r"\b(EUR|USD|GBP|CHF|CAD|AUD|JPY)\b", blob)
    return m.group(1) if m else default


def extract_vendor(lines: list[str]) -> str:
    for ln in lines:
        folded = _fold(ln)
        for key, canonical in KNOWN_VENDORS.items():
            if key in folded:
                # keep the full line ("Station Intermarché Talence") only when it is clean
                # text; OCR junk around a brand ("APRR D", "=JAPRR") collapses to the brand
                clean = re.sub(r"[^\w\s'’.&-]", "", ln).strip()
                words = clean.split()
                if len(words) >= 2 and len(clean) <= 40 and all(len(w) >= 3 for w in words):
                    return clean
                return canonical
    # a short all-caps word repeated across the receipt/variants is usually the brand
    repeated = Counter(
        _fold(ln) for ln in lines if re.fullmatch(r"[A-Za-zÀ-ÿ&' -]{3,20}", ln)
        and ln.isupper()
    )
    if repeated:
        word, count = repeated.most_common(1)[0]
        if count >= 3:
            return word.title()
    skip = re.compile(r"^(sortie|entr[ée]e|ref|classe|carte|voie|ticket|re[cç]u|total|date)\b", re.I)
    for ln in lines:
        letters = sum(c.isalpha() for c in ln)
        if len(ln) >= 4 and letters / len(ln) >= 0.7 and not skip.match(ln):
            return ln[:60]
    return ""


def extract_description(lines: list[str], vendor: str) -> str:
    entries = [m.group(1).strip() for ln in lines if (m := re.match(r"entr[ée]e\s+(.+)", ln, re.I))]
    exits = [m.group(1).strip() for ln in lines if (m := re.match(r"sortie\s+(.+)", ln, re.I))]
    entry, exit_ = _mode(entries), _mode(exits)
    if entry and exit_:
        return f"Toll {entry} → {exit_}"
    skip = re.compile(
        r"total|montant|payer|tva|vat|\bttc\b|\bh\.?t\b|carte|ticket|client|merci|siret|sous-?total|subtotal|\bcb\b",
        re.I,
    )
    candidates = [
        ln for ln in lines
        if len(ln) >= 6 and ln != vendor and not skip.search(ln)
        and sum(c.isalpha() for c in ln) / len(ln) >= 0.6
    ]
    return max(candidates, key=len)[:80] if candidates else ""


def guess_category(vendor: str, description: str) -> str:
    text = _fold(f"{vendor} {description}")
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return "Other"


# ---------------------------------------------------------------- money reconciliation

def _vat_line_values(line: str) -> tuple[list[float], list[float], list[float]]:
    """From a TVA line return (explicit % rates, guessed rates, amounts)."""
    m = VAT_RE.search(line)
    tail = line[m.end():] if m else line
    explicit, guessed, amts = [], [], []
    for idx, tok in enumerate(re.finditer(r"(\d+(?:[.,]\d+)?)\s*(%)?", tail)):
        raw, pct = tok.group(1), bool(tok.group(2))
        v = parse_amount(raw) if re.search(r"[.,]\d", raw) else float(raw)
        if v is None:
            continue
        if pct:
            if v > 100 and float(v).is_integer() and v % 100 == 0:  # "2000%" -> 20.00%
                v = v / 100
            if 0 < v <= 30:
                explicit.append(round(v, 2))
        elif idx == 0 and round(v, 2) in STANDARD_RATES:
            guessed.append(round(v, 2))
            if re.search(r"[.,]\d{2}$", raw):
                amts.append(round(v, 2))  # could also be the amount; reconcile decides
        elif re.search(r"[.,]\d{2}$", raw):
            amts.append(round(v, 2))
    return explicit, guessed, amts


def reconcile_money(lines: list[str]) -> dict:
    notes: list[str] = []
    nets, totals, vats, rates_x, rates_g = [], [], [], [], []

    for ln in lines:
        is_vat = bool(VAT_RE.search(ln)) and not VAT_ID_RE.search(ln)
        if is_vat:
            x, g, a = _vat_line_values(ln)
            rates_x += x; rates_g += g; vats += a
        elif HT_RE.search(ln) and not TTC_RE.search(ln):
            nets += amounts_in(ln)
        elif TTC_RE.search(ln) or TOTAL_RE.search(ln):
            totals += amounts_in(ln)

    net = _mode(nets)
    rate = vat = total = None

    # 1. a (rate, vat) pair that agrees with the net amount
    if net is not None:
        pool = Counter(rates_x) + Counter(rates_g)
        for r, _ in pool.most_common():
            match = next((v for v in Counter(vats).most_common() if _close(net * r / 100, v[0])), None)
            if match:
                rate, vat = r, match[0]
                break
    # 2. otherwise take the most common explicit rate / vat amount
    if rate is None:
        rate = _mode(rates_x) or _mode(rates_g)
    if vat is None and vats and net is None:
        vat = _mode(vats)
    if net is not None and vat is None and rate is not None:
        vat = round(net * rate / 100, 2)
        notes.append(f"VAT amount derived from HT {net:.2f} × {rate:g}% (line unreadable)")

    # total
    if net is not None and vat is not None:
        expected = round(net + vat, 2)
        total = expected
        if not any(_close(t, expected, 0.01) for t in totals):
            if totals:
                seen = ", ".join(f"{t:.2f}" for t in sorted(set(totals)))
                notes.append(f"total set to HT+TVA {expected:.2f}; OCR misread the total line as {seen}")
            else:
                notes.append(f"total derived from HT {net:.2f} + TVA {vat:.2f} (total line unreadable)")
    elif totals:
        total = _mode(totals)
        if rate is not None:  # back out net/vat from a VAT-inclusive total
            net = round(total / (1 + rate / 100), 2)
            vat = round(total - net, 2)
    else:
        loose = [a for ln in lines if not HT_RE.search(ln) and "%" not in ln for a in amounts_in(ln)]
        if loose:
            total = max(loose)
            notes.append("no total line found; used the largest amount on the receipt")

    return {"net": net, "vat_rate": rate, "vat": vat, "total": total, "notes": notes}


# ---------------------------------------------------------------- entry point

def parse_receipt(texts: list[str] | str, *, source: str = "", sha256: str = "",
                  default_currency: str = "EUR") -> Receipt:
    if isinstance(texts, str):
        texts = [texts]
    lines = _lines(texts)
    r = Receipt(source=source, sha256=sha256)
    r.date = extract_date(lines)
    r.vendor = extract_vendor(lines)
    r.description = extract_description(lines, r.vendor)
    r.category = guess_category(r.vendor, r.description)
    r.currency = extract_currency(lines, default_currency)
    money = reconcile_money(lines)
    r.net, r.vat_rate, r.vat, r.total = money["net"], money["vat_rate"], money["vat"], money["total"]
    r.notes = money["notes"]
    if r.total is None:
        r.notes.append("no amount found: fill the total in by hand")
    return r
