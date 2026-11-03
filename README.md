# bill2sheet

Turn photos of receipts, bills and toll tickets into an Excel ledger, from a Debian terminal.

- OCR with Tesseract (French + English by default), several preprocessing passes per photo
- Extracts date, vendor, category, description, net, VAT rate, VAT and total
- Appends to an `.xlsx` with **Billable / Non-billable** dropdowns and a per-currency **Summary** sheet (live formulas)
- `watch` a folder: drop a photo in, a row appears
- Skips duplicates (SHA-256 of the file); never overwrites rows you edited
- Optional `--ai` second opinion from Claude (text only, never the photo)
- A browser version lives in [`web/`](web/) (published with GitHub Pages)

## Install (Debian 13 "trixie")

```bash
git clone https://github.com/<you>/bill2sheet && cd bill2sheet
./scripts/install-debian.sh      # apt: tesseract + French data, then a venv
bill2sheet doctor
```

Manual: `sudo apt install python3-venv tesseract-ocr tesseract-ocr-fra`, then
`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
(trixie enforces PEP 668, so use a venv rather than system pip).

Docker: `docker build -t bill2sheet . && docker run --rm -v "$PWD:/data" bill2sheet scan receipt.jpg`

## Use

```bash
bill2sheet scan photos/ -o bills.xlsx                 # files or folders
bill2sheet scan ticket.jpg --non-billable Meals,Medical
bill2sheet watch ~/Receipts/inbox -o ~/Receipts/bills.xlsx
bill2sheet inspect ticket.jpg --show-text             # debug one image
bill2sheet scan ticket.jpg --ai                       # needs ANTHROPIC_API_KEY
```

Options: `--lang fra+eng`, `--fast` (one OCR pass), `--currency EUR` (fallback when none is printed).
Formats: jpg, png, webp, tif, bmp. **HEIC is not read**: convert first (`heif-convert`, from `libheif-examples`).

### Automatic folder pipeline

`watch` polls every 5 s and waits until a file stops changing before reading it. Run it as a service:
edit the paths in [`contrib/bill2sheet-watch.service`](contrib/bill2sheet-watch.service), copy it to
`~/.config/systemd/user/`, then `systemctl --user enable --now bill2sheet-watch`.
To feed the inbox from your phone, sync the camera folder to the machine with Syncthing (or similar).

## How it reads a bad photo

Watermarks and stamps often corrupt the digits themselves (a real toll ticket read `54.60` as `64.60`).
So the tool does not trust one pass:

1. Each image is OCR'd raw and with two contrast thresholds that drop light watermarks, in parallel.
2. Candidates from all passes are voted per field.
3. Net + VAT = total is used to reconcile: if the printed total disagrees, the total is set to net + VAT and
   the **Notes** column says so. If the total line is unreadable it is derived and flagged the same way.

Anything the tool changed or couldn't read appears in Notes. Review those rows.

## Tests and CI

`pytest` runs parser, export, AI (mocked) and OCR end-to-end tests (the OCR ones skip without Tesseract).
GitHub Actions runs them inside a `debian:trixie` container; `web/` deploys to Pages on push to `main`.

## Publish to GitHub

```bash
git init && git add . && git commit -m "Initial commit"
gh repo create bill2sheet --private --source . --push     # use --public if you prefer
```
Then Settings → Pages → Source: GitHub Actions. Add a LICENSE of your choice.

## Privacy

OCR is local. Receipts, ledgers and images are git-ignored: don't commit them. `--ai` sends only the OCR
text and parsed fields to the Anthropic API, using the key from your environment; it is never stored.
The web app keeps any API key in memory only.

## Limitations

Tested on a handful of receipts (one real toll ticket plus synthetic ones). Vendor and category rules are
heuristic; handwriting and very blurry photos won't work. Dates default to DD/MM unless one is impossible.
