"""bill2sheet command line interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from . import __version__
from .export import append_receipts, existing_hashes
from .ocr import (IMAGE_EXTENSIONS, OcrError, installed_languages, iter_images,
                  missing_languages, ocr_variants, tesseract_path)
from .parse import CATEGORIES, Receipt, parse_receipt


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _process(path: Path, args: argparse.Namespace) -> Receipt:
    texts = ocr_variants(path, lang=args.lang, fast=args.fast)
    receipt = parse_receipt(texts, source=path.name, sha256=_sha256(path),
                            default_currency=args.currency)
    if args.non_billable and receipt.category in args.non_billable:
        receipt.billable = "Non-billable"
    if args.ai:
        from .ai import AiError, refine
        try:
            receipt = refine(receipt, texts)
        except AiError as e:
            receipt.notes.append(f"AI skipped: {e}")
    return receipt


def _describe(r: Receipt) -> str:
    money = f"{r.total:.2f} {r.currency}" if r.total is not None else "NO TOTAL"
    parts = []
    if r.net is not None:
        parts.append(f"HT {r.net:.2f}")
    if r.vat is not None:
        rate = f" @{r.vat_rate:g}%" if r.vat_rate is not None else ""
        parts.append(f"TVA {r.vat:.2f}{rate}")
    detail = f" ({', '.join(parts)})" if parts else ""
    return (f"{r.source}  {r.date or '----------'}  {r.vendor or '?'}  "
            f"{r.category}  {money}{detail}  [{r.billable}]")


def _report(receipts: list[Receipt]) -> None:
    for r in receipts:
        print("  ✓", _describe(r), flush=True)
        for note in r.notes:
            print("      ·", note, flush=True)


def cmd_scan(args: argparse.Namespace) -> int:
    files = list(iter_images(args.paths))
    if not files:
        print(f"no images found (supported: {', '.join(sorted(IMAGE_EXTENSIONS))})", file=sys.stderr)
        return 1
    known = existing_hashes(args.output)
    receipts: list[Receipt] = []
    for f in files:
        if _sha256(f) in known:
            print(f"  = {f.name} already in {args.output.name}, skipped")
            continue
        print(f"… {f.name}", flush=True)
        receipts.append(_process(f, args))
    _report(receipts)
    added = append_receipts(args.output, receipts)
    print(f"\n{added} row(s) added to {args.output}")
    needs_review = [r for r in receipts if r.notes or r.total is None]
    if needs_review:
        print(f"{len(needs_review)} receipt(s) have notes: check the Notes column.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"{folder} is not a directory", file=sys.stderr)
        return 1
    done = set(existing_hashes(args.output))
    last_seen: dict[Path, tuple[int, int]] = {}
    print(f"watching {folder} every {args.interval}s → {args.output}  (Ctrl-C to stop)", flush=True)
    try:
        while True:
            for f in iter_images([folder]):
                st = f.stat()
                key = (st.st_size, st.st_mtime_ns)
                if not args.once and last_seen.get(f) != key:
                    last_seen[f] = key  # wait one more poll: the file may still be copying
                    continue
                digest = _sha256(f)
                if digest in done:
                    continue
                try:
                    print(f"… {f.name}", flush=True)
                    receipt = _process(f, args)
                except (OcrError, OSError) as e:
                    print(f"  ✗ {f.name}: {e}", file=sys.stderr, flush=True)
                    done.add(digest)
                    continue
                _report([receipt])
                append_receipts(args.output, [receipt])
                done.add(digest)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    texts = ocr_variants(args.image, lang=args.lang, fast=args.fast)
    if args.show_text:
        for i, t in enumerate(texts, 1):
            print(f"===== OCR pass {i} =====\n{t.strip()}\n")
    receipt = parse_receipt(texts, source=args.image.name, default_currency=args.currency)
    print(json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    ok = True
    try:
        print(f"tesseract: {tesseract_path()}")
        langs = installed_languages()
        print(f"languages: {', '.join(langs)}")
        if missing_languages("fra+eng"):
            ok = False
            print("missing: sudo apt install tesseract-ocr-fra tesseract-ocr-eng")
    except OcrError as e:
        ok = False
        print(e)
    try:
        import openpyxl, PIL  # noqa: E401
        print(f"openpyxl {openpyxl.__version__}, Pillow {PIL.__version__}")
    except ImportError as e:
        ok = False
        print(f"python dependency missing: {e}")
    print("ready" if ok else "not ready")
    return 0 if ok else 1


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lang", default="fra+eng", help="Tesseract languages (default: fra+eng)")
    p.add_argument("--fast", action="store_true", help="single OCR pass (faster, less robust)")
    p.add_argument("--currency", default="EUR", help="fallback currency code (default: EUR)")
    p.add_argument("--ai", action="store_true",
                   help="ask Claude to review each receipt (needs ANTHROPIC_API_KEY)")


def _non_billable(value: str) -> set[str]:
    cats = {c.strip().title() for c in value.split(",") if c.strip()}
    unknown = cats - set(CATEGORIES)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown categories {sorted(unknown)}; choose from {CATEGORIES}")
    return cats


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bill2sheet", description=__doc__)
    ap.add_argument("--version", action="version", version=f"bill2sheet {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="OCR images (or folders of images) into an .xlsx ledger")
    scan.add_argument("paths", nargs="+", type=Path)
    scan.add_argument("-o", "--output", type=Path, default=Path("bills.xlsx"))
    scan.add_argument("--non-billable", type=_non_billable, default=set(), metavar="CATS",
                      help="comma-separated categories to mark Non-billable, e.g. Meals,Medical")
    _common(scan)
    scan.set_defaults(func=cmd_scan)

    watch = sub.add_parser("watch", help="keep a folder under watch and append new photos automatically")
    watch.add_argument("folder", type=Path)
    watch.add_argument("-o", "--output", type=Path, default=Path("bills.xlsx"))
    watch.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    watch.add_argument("--once", action="store_true", help="process what is there now and exit")
    watch.add_argument("--non-billable", type=_non_billable, default=set(), metavar="CATS")
    _common(watch)
    watch.set_defaults(func=cmd_watch)

    inspect = sub.add_parser("inspect", help="debug one image: show OCR passes and the parsed fields")
    inspect.add_argument("image", type=Path)
    inspect.add_argument("--show-text", action="store_true", help="print raw OCR of every pass")
    _common(inspect)
    inspect.set_defaults(func=cmd_inspect)

    doctor = sub.add_parser("doctor", help="check tesseract, language data and Python deps")
    doctor.set_defaults(func=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OcrError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
