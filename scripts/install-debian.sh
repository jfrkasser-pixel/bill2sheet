#!/usr/bin/env bash
# Install bill2sheet on Debian 13 (trixie) into ~/.local/share/bill2sheet, command in ~/.local/bin.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
repo="$(cd "$(dirname "$0")/.." && pwd)"
venv="$HOME/.local/share/bill2sheet/venv"
python3 -m venv "$venv"
"$venv/bin/pip" install --upgrade pip
"$venv/bin/pip" install "$repo"
mkdir -p "$HOME/.local/bin"
ln -sf "$venv/bin/bill2sheet" "$HOME/.local/bin/bill2sheet"
echo "Installed. Make sure ~/.local/bin is on your PATH, then run: bill2sheet doctor"
