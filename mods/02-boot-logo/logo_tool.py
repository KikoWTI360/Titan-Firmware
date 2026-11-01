#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logo_tool.py — sostituisce il logo d'avvio sul piccolo schermo della Titan.

Il logo non è un file esterno: è un bitmap incorporato nel binario
`bootanimation`, nel simbolo `prologo_17_16_94_32` (94x32 pixel, disegnato a
x=17, y=16 su uno schermo monocromatico 128x64). Lo strumento lo trova tramite
la tabella dei simboli e ne riscrive solo i byte, lasciando il resto del file
identico.

Sottocomandi:

  show <bootanimation> [--symbol NOME] [--size WxH] [--skip N] [--encoding ENC]
      Disegna in ASCII il bitmap del simbolo in tutte le codifiche compatibili
      con la sua dimensione. Quella in cui si legge il logo Insta360 è la
      codifica giusta da passare a `patch`.

  patch <bootanimation> <logo.png> --encoding ENC --out FILE [--invert] [--threshold N]
      Converte l'immagine in un bitmap 1-bit della stessa misura, lo codifica
      come l'originale e scrive una COPIA del binario con solo quei byte cambiati.
      Non modifica mai il file di input.

Uso tipico:
    python3 mods/02-boot-logo/logo_tool.py show extracted/titan_update/bin/bootanimation
    python3 mods/02-boot-logo/logo_tool.py patch extracted/titan_update/bin/bootanimation \
        logo_wti360.png --encoding page-lsb --out mods/02-boot-logo/work/bootanimation

Pixel "accesi" = chiari (sull'OLED sono i punti bianchi). Un logo scuro su
sfondo chiaro va passato con --invert.
"""

import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from elf_probe import Elf  # noqa: E402

DEFAULT_SYMBOL = "prologo_17_16_94_32"
ENCODINGS = ["page-lsb", "page-msb", "col-lsb", "col-msb", "row-msb", "row-lsb", "byte"]
MAX_HEADER = 16  # byte di intestazione tollerati prima dei pixel


# ----------------------------- codifiche --------------------------------------
# Ogni codifica mappa il pixel (x, y) su (indice del byte, bit). "page" è il
# formato nativo dei controller OLED tipo SSD1306: bande orizzontali da 8 righe,
# un byte per colonna. "col" è lo stesso ma colonna per colonna. "row" è un
# bitmap riga per riga. "byte" è un byte per pixel.

def enc_size(enc, w, h):
    if enc.startswith("page") or enc.startswith("col"):
        return w * ((h + 7) // 8)
    if enc.startswith("row"):
        return ((w + 7) // 8) * h
    return w * h


def locate(enc, x, y, w, h):
    pages = (h + 7) // 8
    if enc.startswith("page"):
        idx, bit = (y // 8) * w + x, y % 8
    elif enc.startswith("col"):
        idx, bit = x * pages + y // 8, y % 8
    elif enc.startswith("row"):
        idx, bit = y * ((w + 7) // 8) + x // 8, x % 8
    else:
        return y * w + x, None
    return idx, (7 - bit) if enc.endswith("msb") else bit


def decode(enc, data, w, h):
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            idx, bit = locate(enc, x, y, w, h)
            row.append(data[idx] != 0 if bit is None else bool(data[idx] >> bit & 1))
        rows.append(row)
    return rows


def encode(enc, rows, w, h, on_value=0xFF):
    out = bytearray(enc_size(enc, w, h))
    for y in range(h):
        for x in range(w):
            if not rows[y][x]:
                continue
            idx, bit = locate(enc, x, y, w, h)
            if bit is None:
                out[idx] = on_value
            else:
                out[idx] |= 1 << bit
    return bytes(out)


def ascii_art(rows):
    return "\n".join("    " + "".join("#" if p else "." for p in row) for row in rows)


# ----------------------------- simbolo ----------------------------------------

def find_symbol(path, name):
    elf = Elf(path)
    syms = elf.symbols()
    # Le variabili `static` del C++ hanno il nome codificato: _ZL<lunghezza><nome>
    wanted = {name, f"_ZL{len(name)}{name}"}
    for s in syms:
        if s["name"] in wanted and s["file_offset"] is not None:
            return elf, s
    near = sorted({s["name"] for s in syms if re.search(r"logo|icon|boot", s["name"], re.I)})
    msg = f"simbolo '{name}' non trovato in {path}."
    if near:
        msg += " Simboli simili: " + ", ".join(near[:20])
    raise SystemExit(msg)


def symbol_dims(name, size_opt):
    if size_opt:
        w, h = size_opt.lower().split("x")
        return int(w), int(h)
    m = re.search(r"_(\d+)_(\d+)_(\d+)_(\d+)$", name)
    if not m:
        raise SystemExit(f"non riesco a ricavare la misura da '{name}': usa --size WxH")
    return int(m.group(3)), int(m.group(4))


def header_len(enc, symsize, w, h, skip_opt):
    """Byte da saltare prima dei pixel, o None se la codifica non è compatibile."""
    need = enc_size(enc, w, h)
    if skip_opt is not None:
        return skip_opt if skip_opt + need <= symsize else None
    if symsize == need:
        return 0
    if need < symsize <= need + MAX_HEADER:
        return symsize - need
    return None


# ----------------------------- comandi ----------------------------------------

def cmd_show(args):
    elf, s = find_symbol(args.binary, args.symbol)
    w, h = symbol_dims(s["name"], args.size)
    data = elf.data[s["file_offset"]:s["file_offset"] + s["size"]]
    print(f"Simbolo {s['name']}: tipo {s['type']}, sezione {s['section']}, "
          f"offset nel file 0x{s['file_offset']:x}, {s['size']} byte, bitmap {w}x{h}\n")
    encs = [args.encoding] if args.encoding else ENCODINGS
    shown = 0
    for enc in encs:
        skip = header_len(enc, s["size"], w, h, args.skip)
        if skip is None:
            continue
        note = f" (saltati {skip} byte di intestazione)" if skip else ""
        print(f"=== codifica {enc}{note} ===")
        print(ascii_art(decode(enc, data[skip:], w, h)))
        print()
        shown += 1
    if not shown:
        sizes = ", ".join(f"{e}={enc_size(e, w, h)}" for e in ENCODINGS)
        print(f"Nessuna codifica corrisponde a {s['size']} byte (attese: {sizes}).")
        print("Prova --size WxH o --skip N, oppure mandami questo output.")
        return 1
    print("La codifica giusta è quella in cui si legge il logo: passala a `patch --encoding`.")
    return 0


def load_logo(path, w, h, threshold, invert):
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("serve Pillow per leggere l'immagine: pip install pillow")
    img = Image.open(path).convert("L")
    if img.size != (w, h):
        print(f"Avviso: l'immagine è {img.size[0]}x{img.size[1]}, la ridimensiono per stare "
              f"in {w}x{h} mantenendo le proporzioni (meglio partire già da {w}x{h}).")
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img.thumbnail((w, h), resample)
        canvas = Image.new("L", (w, h), 255 if invert else 0)
        canvas.paste(img, ((w - img.size[0]) // 2, (h - img.size[1]) // 2))
        img = canvas
    px = img.load()
    return [[(px[x, y] >= threshold) != invert for x in range(w)] for y in range(h)]


def cmd_patch(args):
    if os.path.abspath(args.out) == os.path.abspath(args.binary):
        raise SystemExit("--out deve essere un file diverso dall'input: l'originale non si tocca.")
    elf, s = find_symbol(args.binary, args.symbol)
    w, h = symbol_dims(s["name"], args.size)
    skip = header_len(args.encoding, s["size"], w, h, args.skip)
    if skip is None:
        raise SystemExit(f"la codifica {args.encoding} non è compatibile con {s['size']} byte "
                         f"per {w}x{h}: controlla con `show`.")
    start = s["file_offset"] + skip
    need = enc_size(args.encoding, w, h)
    old = elf.data[start:start + need]
    on_value = (max(old) or 0xFF) if args.encoding == "byte" else 0xFF

    rows = load_logo(args.logo, w, h, args.threshold, args.invert)
    new = encode(args.encoding, rows, w, h, on_value)
    assert len(new) == need

    patched = bytearray(elf.data)
    patched[start:start + need] = new
    assert len(patched) == len(elf.data)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(patched)
    os.chmod(args.out, 0o755)

    changed = sum(1 for a, b in zip(old, new) if a != b)
    print(f"Logo attuale ({args.encoding}):\n{ascii_art(decode(args.encoding, old, w, h))}\n")
    print(f"Nuovo logo:\n{ascii_art(rows)}\n")
    print(f"Byte riscritti: {need} a partire da 0x{start:x} ({changed} effettivamente diversi).")
    print(f"Resto del file invariato: {len(patched)} byte in totale, come l'originale.")
    print(f"SHA-256 originale: {hashlib.sha256(elf.data).hexdigest()}")
    print(f"SHA-256 patchato:  {hashlib.sha256(patched).hexdigest()}")
    print(f"Scritto: {args.out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sostituisce il logo d'avvio della Titan.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("binary", help="il file bootanimation")
        p.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"default: {DEFAULT_SYMBOL}")
        p.add_argument("--size", help="misura WxH, se non ricavabile dal nome del simbolo")
        p.add_argument("--skip", type=int, help="byte di intestazione prima dei pixel")

    p = sub.add_parser("show", help="disegna il logo attuale nelle codifiche possibili")
    common(p)
    p.add_argument("--encoding", choices=ENCODINGS, help="mostra solo questa codifica")

    p = sub.add_parser("patch", help="scrive una copia di bootanimation col nuovo logo")
    common(p)
    p.add_argument("logo", help="immagine del nuovo logo (PNG consigliato, già alla misura giusta)")
    p.add_argument("--encoding", choices=ENCODINGS, required=True,
                   help="la codifica in cui `show` mostra il logo leggibile")
    p.add_argument("--out", required=True, help="file di output (copia patchata)")
    p.add_argument("--threshold", type=int, default=128, help="soglia 0-255 per pixel acceso")
    p.add_argument("--invert", action="store_true", help="logo scuro su sfondo chiaro")

    args = ap.parse_args(argv)
    return cmd_show(args) if args.cmd == "show" else cmd_patch(args)


if __name__ == "__main__":
    raise SystemExit(main())
