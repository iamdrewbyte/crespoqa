#!/usr/bin/env bash
# No-root font setup for a dev machine: Word documents use Arial/Calibri/Times New Roman/Courier New, which LibreOffice
# only reproduces page-for-page with metric-compatible fonts. Without them page numbers drift.
set -euo pipefail
OUT="$HOME/.local/share/fonts/crespoqa"; TMP="$(mktemp -d)"; mkdir -p "$OUT" "$HOME/.config/fontconfig"
curl -fsSL -o "$TMP/lib.tgz" https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz
curl -fsSL -o "$TMP/car.zip" https://github.com/googlefonts/carlito/archive/refs/heads/main.zip
python3 -I - "$TMP" "$OUT" <<'PY'
import sys, tarfile, zipfile, pathlib
tmp, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
with tarfile.open(tmp/"lib.tgz") as t:
    for m in t.getmembers():
        if m.isfile() and m.name.lower().endswith(".ttf") and ".." not in m.name:
            (out/pathlib.Path(m.name).name).write_bytes(t.extractfile(m).read())
with zipfile.ZipFile(tmp/"car.zip") as z:
    for n in z.namelist():
        if n.lower().endswith(".ttf") and "carlito" in n.lower() and ".." not in n:
            (out/pathlib.Path(n).name).write_bytes(z.read(n))
PY
cp "$(dirname "$0")/fonts.conf" "$HOME/.config/fontconfig/fonts.conf"
fc-cache -f
rm -rf "$TMP"
echo "Arial -> $(fc-match Arial family) | Calibri -> $(fc-match Calibri family) | Tahoma -> $(fc-match Tahoma family)"
