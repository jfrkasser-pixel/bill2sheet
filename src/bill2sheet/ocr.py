"""Tesseract wrapper.

Receipts photographed on a phone often have watermarks, shadows or thermal-paper
fade that make a single OCR pass unreliable (a grey logo across the TVA/TTC lines
is enough to destroy the amounts). We therefore OCR a few preprocessed variants of
the same image; ``parse.py`` merges them and cross-checks the arithmetic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

# Grey levels (0-255, after auto-contrast) used to drop light watermarks.
THRESHOLDS = (90, 130)


class OcrError(RuntimeError):
    pass


def tesseract_path() -> str:
    path = shutil.which("tesseract")
    if not path:
        raise OcrError(
            "tesseract not found. On Debian: sudo apt install tesseract-ocr tesseract-ocr-fra"
        )
    return path


def installed_languages() -> list[str]:
    out = subprocess.run(
        [tesseract_path(), "--list-langs"], capture_output=True, text=True, check=True
    )
    lines = (out.stdout or out.stderr).splitlines()
    return [ln.strip() for ln in lines[1:] if ln.strip()]


def missing_languages(lang: str) -> list[str]:
    have = set(installed_languages())
    return [code for code in lang.split("+") if code not in have]


def _run_tesseract(image: Path, lang: str, psm: int = 6) -> str:
    env = dict(os.environ, OMP_THREAD_LIMIT="1")  # we parallelise across variants instead
    proc = subprocess.run(
        [tesseract_path(), str(image), "stdout", "-l", lang, "--psm", str(psm)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if proc.returncode != 0:
        raise OcrError(f"tesseract failed on {image.name}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _preprocessed_variants(image: Path, workdir: Path, thresholds: tuple[int, ...]) -> list[Path]:
    try:
        from PIL import Image, ImageOps
    except ImportError:  # Pillow is a hard dependency, but stay usable without it
        return []
    with Image.open(image) as im:
        gray = ImageOps.exif_transpose(im).convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if gray.width < 2000:  # small photos OCR better when upscaled
        gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    paths = []
    for thr in thresholds:
        out = workdir / f"{image.stem}.thr{thr}.png"
        gray.point(lambda p, t=thr: 255 if p > t else 0).save(out)
        paths.append(out)
    return paths


def ocr_variants(image: Path, lang: str = "fra+eng", fast: bool = False) -> list[str]:
    """Return OCR text for several variants of ``image`` (raw first)."""
    image = Path(image)
    missing = missing_languages(lang)
    if missing:
        raise OcrError(
            f"Tesseract language data missing: {', '.join(missing)}. "
            f"On Debian: sudo apt install {' '.join('tesseract-ocr-' + m for m in missing)}"
        )
    with tempfile.TemporaryDirectory(prefix="bill2sheet-") as tmp:
        variants = _preprocessed_variants(image, Path(tmp), (THRESHOLDS[-1],) if fast else THRESHOLDS)
        targets = ([] if fast and variants else [image]) + variants
        if not targets:
            targets = [image]
        with ThreadPoolExecutor(max_workers=min(len(targets), os.cpu_count() or 1)) as pool:
            return list(pool.map(lambda p: _run_tesseract(p, lang), targets))


def iter_images(paths: list[Path]):
    """Yield image files from a mix of files and directories (non-recursive for dirs)."""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                    yield child
        elif p.is_file():
            yield p
