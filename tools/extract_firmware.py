#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_firmware.py — estrazione ripetibile del pacchetto di update Insta360 Titan.

Formato del pacchetto (da reverse engineering):
    [header testuale: "insta_titan" + versione + lunghezza] + [archivio ZIP non cifrato]

Lo script:
  1. legge il file di input (.bin o .zip);
  2. riconosce l'header "insta_titan" e ne estrae versione/lunghezza (se presente);
  3. trova l'inizio dello ZIP (firma locale "PK\\x03\\x04") e lo isola;
  4. verifica l'integrità (MD5) e la coerenza della lunghezza dichiarata;
  5. estrae l'albero `titan_update/` in una directory di output pulita;
  6. stampa il manifest `bill.list` (cartella -> percorso di destinazione sul device).

Progettato per essere IDEMPOTENTE: la directory di output viene ricostruita da zero
a ogni esecuzione (a meno di --keep).

Uso:
    python3 tools/extract_firmware.py original-firmware/Insta360_Titan_Update.bin
    python3 tools/extract_firmware.py <input> --out extracted [--md5 <atteso>] [--keep]

Nota sicurezza: il pacchetto è dato non fidato. Lo ZIP viene estratto con
controllo anti path-traversal (niente percorsi assoluti o "..").
"""

import argparse
import hashlib
import io
import os
import shutil
import sys
import zipfile

ZIP_LOCAL_SIG = b"PK\x03\x04"      # inizio di un entry ZIP locale
ZIP_EOCD_SIG = b"PK\x05\x06"       # End Of Central Directory
HEADER_MAGIC = b"insta_titan"
MAX_HEADER_SCAN = 4096             # l'header testuale sta nei primi byte


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def human_size(n):
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.2f}{u}" if u != "B" else f"{int(f)}B"
        f /= 1024
    return f"{n}B"


def parse_header(blob):
    """
    Cerca il magic 'insta_titan' nei primi byte e prova a leggere i campi testuali
    che lo seguono (versione, lunghezza). Il layout esatto puo' variare tra build,
    quindi siamo tolleranti: restituiamo quello che riusciamo a riconoscere.

    Ritorna (header_info: dict | None).
    """
    head = blob[:MAX_HEADER_SCAN]
    idx = head.find(HEADER_MAGIC)
    if idx < 0:
        return None

    info = {"magic_offset": idx}
    # Prova a decodificare la porzione testuale dopo il magic, fino al primo
    # byte non stampabile o fino all'inizio dello ZIP.
    zip_at = blob.find(ZIP_LOCAL_SIG)
    end = zip_at if 0 <= zip_at < MAX_HEADER_SCAN else MAX_HEADER_SCAN
    raw = blob[idx:end]
    # Spezza su byte NUL e whitespace multipli, tieni i token stampabili.
    text = raw.decode("latin-1", errors="replace")
    tokens = [t for t in text.replace("\x00", " ").split() if t]
    info["raw_tokens"] = tokens[:12]

    # Euristiche: la versione e' tipo X.Y.Z; la lunghezza e' un intero "grande".
    import re
    for t in tokens[1:]:
        if re.fullmatch(r"v?\d+\.\d+(\.\d+)*", t) and "version" not in info:
            info["version"] = t.lstrip("v")
        elif re.fullmatch(r"\d{4,}", t) and "declared_length" not in info:
            info["declared_length"] = int(t)
    return info


def locate_zip(blob):
    """
    Restituisce l'offset di inizio dello ZIP. Se il file e' gia' uno ZIP puro,
    l'offset e' 0. Altrimenti e' la posizione della prima firma PK\\x03\\x04.
    """
    if blob[:4] == ZIP_LOCAL_SIG:
        return 0
    idx = blob.find(ZIP_LOCAL_SIG)
    if idx < 0:
        raise ValueError("Nessuna firma ZIP (PK\\x03\\x04) trovata: input non riconosciuto.")
    return idx


def safe_extract(zf, out_dir):
    """Estrazione con protezione anti path-traversal."""
    out_dir = os.path.realpath(out_dir)
    members = zf.infolist()
    for m in members:
        # Normalizza e verifica che la destinazione resti dentro out_dir.
        target = os.path.realpath(os.path.join(out_dir, m.filename))
        if not (target == out_dir or target.startswith(out_dir + os.sep)):
            raise ValueError(f"Entry ZIP sospetta (path traversal): {m.filename!r}")
    zf.extractall(out_dir)
    return members


def find_tree_root(out_dir):
    """
    Trova la radice dell'albero del pacchetto. Normalmente c'e' una cartella
    'titan_update/' al top level; altrimenti usa out_dir stesso.
    """
    cand = os.path.join(out_dir, "titan_update")
    if os.path.isdir(cand):
        return cand
    # Se lo ZIP ha un'unica cartella al top level, usala.
    entries = [e for e in os.listdir(out_dir) if not e.startswith(".")]
    dirs = [e for e in entries if os.path.isdir(os.path.join(out_dir, e))]
    if len(dirs) == 1 and len(entries) == 1:
        return os.path.join(out_dir, dirs[0])
    return out_dir


def print_bill_list(tree_root):
    """Stampa il manifest bill.list se presente (mappa cartella -> percorso device)."""
    bill = os.path.join(tree_root, "bill.list")
    if not os.path.isfile(bill):
        eprint("  (bill.list non trovato nell'albero estratto)")
        return
    print("\n== bill.list (manifest cartella -> destinazione sul device) ==")
    with open(bill, "r", errors="replace") as f:
        content = f.read()
    print(content.rstrip())


def summarize_tree(tree_root):
    """Breve riepilogo: quante entry per cartella di primo livello e peso."""
    print("\n== Riepilogo albero estratto ==")
    print(f"  radice: {tree_root}")
    for name in sorted(os.listdir(tree_root)):
        p = os.path.join(tree_root, name)
        if os.path.isdir(p):
            nfiles = sum(len(fs) for _, _, fs in os.walk(p))
            size = 0
            for r, _, fs in os.walk(p):
                for fn in fs:
                    try:
                        size += os.path.getsize(os.path.join(r, fn))
                    except OSError:
                        pass
            print(f"  {name+'/':<14} {nfiles:>5} file   {human_size(size):>10}")
        else:
            try:
                print(f"  {name:<14} {'':>5}        {human_size(os.path.getsize(p)):>10}")
            except OSError:
                print(f"  {name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Estrae il pacchetto di update Insta360 Titan.")
    ap.add_argument("input", help="pacchetto di input (.bin o .zip)")
    ap.add_argument("--out", default="extracted",
                    help="directory di output (default: extracted)")
    ap.add_argument("--md5", default=None,
                    help="MD5 atteso dello ZIP (opzionale, per verifica integrita')")
    ap.add_argument("--keep", action="store_true",
                    help="non svuotare la directory di output prima di estrarre")
    ap.add_argument("--save-zip", action="store_true",
                    help="salva anche lo ZIP isolato in <out>/_payload.zip")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        eprint(f"ERRORE: input non trovato: {args.input}")
        return 2

    with open(args.input, "rb") as f:
        blob = f.read()

    print(f"== Input ==")
    print(f"  file:  {args.input}")
    print(f"  dimensione: {human_size(len(blob))} ({len(blob)} byte)")
    print(f"  MD5 file:   {hashlib.md5(blob).hexdigest()}")

    # --- Header ---
    header = parse_header(blob)
    if header:
        print("\n== Header 'insta_titan' riconosciuto ==")
        print(f"  offset magic:      {header['magic_offset']}")
        if "version" in header:
            print(f"  versione:          {header['version']}")
        if "declared_length" in header:
            print(f"  lunghezza dichiar.: {header['declared_length']}")
        print(f"  token header:      {header.get('raw_tokens')}")
    else:
        print("\n== Header 'insta_titan' NON trovato: tratto l'input come ZIP puro. ==")

    # --- Isola lo ZIP ---
    try:
        zip_off = locate_zip(blob)
    except ValueError as e:
        eprint(f"ERRORE: {e}")
        return 3
    print(f"\n== Payload ZIP ==")
    print(f"  offset ZIP:  {zip_off}")
    zip_bytes = blob[zip_off:]
    print(f"  dimensione ZIP: {human_size(len(zip_bytes))} ({len(zip_bytes)} byte)")
    zip_md5 = hashlib.md5(zip_bytes).hexdigest()
    print(f"  MD5 ZIP:     {zip_md5}")

    # Coerenza con la lunghezza dichiarata nell'header (se presente).
    if header and "declared_length" in header:
        dl = header["declared_length"]
        if dl == len(zip_bytes):
            print(f"  lunghezza:   OK (coincide con header: {dl})")
        else:
            print(f"  lunghezza:   ATTENZIONE header dichiara {dl}, ZIP e' {len(zip_bytes)} "
                  f"(diff {len(zip_bytes)-dl:+d}) — il campo lunghezza potrebbe riferirsi "
                  f"ad altro; verifica manualmente.")

    # Verifica MD5 atteso se fornito.
    if args.md5:
        if args.md5.lower().strip() == zip_md5:
            print(f"  verifica MD5: OK (coincide con --md5)")
        else:
            eprint(f"  verifica MD5: FALLITA — atteso {args.md5}, ottenuto {zip_md5}")
            return 4

    # --- Validita' ZIP ---
    bio = io.BytesIO(zip_bytes)
    if not zipfile.is_zipfile(bio):
        eprint("ERRORE: il payload non e' uno ZIP valido.")
        return 5

    # --- Prepara output ---
    out_dir = os.path.abspath(args.out)
    if os.path.exists(out_dir) and not args.keep:
        # Svuota SOLO il contenuto estratto, preservando README.md/.gitkeep.
        for name in os.listdir(out_dir):
            if name in ("README.md", ".gitkeep"):
                continue
            p = os.path.join(out_dir, name)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    os.makedirs(out_dir, exist_ok=True)

    if args.save_zip:
        with open(os.path.join(out_dir, "_payload.zip"), "wb") as zf_out:
            zf_out.write(zip_bytes)
        print(f"  ZIP salvato: {os.path.join(out_dir, '_payload.zip')}")

    # --- Estrai ---
    bio.seek(0)
    with zipfile.ZipFile(bio) as zf:
        bad = zf.testzip()
        if bad is not None:
            eprint(f"ERRORE: CRC fallito per {bad}")
            return 6
        members = safe_extract(zf, out_dir)
    print(f"\n== Estratto {len(members)} entry in {out_dir} ==")

    tree_root = find_tree_root(out_dir)
    summarize_tree(tree_root)
    print_bill_list(tree_root)

    print("\nFatto. Prossimo passo:")
    rel = os.path.relpath(tree_root)
    print(f"  python3 tools/inventory.py {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
