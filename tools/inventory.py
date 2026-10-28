#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inventory.py — inventario dello strato applicativo del firmware Insta360 Titan.

Produce la "verita' di terra" partendo dall'albero estratto (extracted/titan_update):

  A. Elenco dei file del web server Python  (bin/web_server/)
  B. Elenco dei config di ripresa in cfg/   (*.json, *.rc, default.prop)
  C. Localizzazione (file:riga) di PAROLE CHIAVE che controllano
     bitrate / codec / parametri delle modalita' di cattura, nei JSON di cfg/
     e nel codice Python del web server.
  D. Mappa dei comandi OSC esposti dal web server (euristica).

Uso:
    python3 tools/inventory.py extracted/titan_update
    python3 tools/inventory.py <tree_root> [--json out.json]

Nessuna dipendenza esterna. Sola lettura: non modifica nulla.
"""

import argparse
import json
import os
import re
import sys

# Parole chiave che tipicamente governano bitrate / codec / modalita'.
# Raggruppate per tema cosi' l'output e' leggibile.
KEYWORDS = {
    "bitrate": [
        r"\bbit[_\-]?rate\b", r"\bbitrate\b", r"\bmax[_\-]?bitrate\b",
        r"\bvideo[_\-]?bitrate\b", r"\bkbps\b", r"\bmbps\b", r"\brc[_\-]?mode\b",
    ],
    "codec": [
        r"\bcodec\b", r"\bh\.?264\b", r"\bh\.?265\b", r"\bhevc\b", r"\bavc\b",
        r"\bencoder?\b", r"\bprofile\b", r"\bentropy\b", r"\bcabac\b",
    ],
    "gop/frame": [
        r"\bgop\b", r"\bi[_\-]?frame\b", r"\bidr\b", r"\bframe[_\-]?rate\b",
        r"\bfps\b", r"\bframerate\b",
    ],
    "resolution": [
        r"\bresolution\b", r"\bwidth\b", r"\bheight\b", r"\b\d{3,5}x\d{3,5}\b",
        r"\b11k\b", r"\b8k\b", r"\b5\.?7k\b",
    ],
    "mode/template": [
        r"\bmode\b", r"\btemplate\b", r"\bgoogle\b", r"\bstreet[_\-]?view\b",
        r"\bgsv\b", r"\btimelapse\b", r"\btime[_\-]?lapse\b", r"\bhdr\b",
        r"\binterval\b", r"\bburst\b",
    ],
}

# File/dir di config di ripresa noti (dalla descrizione del firmware).
KNOWN_CFG = [
    "takevideo_template.json", "takepicture_template.json",
    "takelive_template.json", "vid_customer.json", "def_cfg.json",
    "preview.json",
]

TEXT_EXT = {".json", ".py", ".sh", ".rc", ".prop", ".txt", ".cfg", ".ini", ".conf"}


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def is_text_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return True
    base = os.path.basename(path)
    if base in ("bill.list", "default.prop", "init.rc"):
        return True
    return False


def read_lines(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def rel(root, path):
    return os.path.relpath(path, root)


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def inventory_webserver(tree_root, report):
    section("A. Web server Python — bin/web_server/")
    ws = os.path.join(tree_root, "bin", "web_server")
    report["web_server"] = {"root": None, "files": []}
    if not os.path.isdir(ws):
        # prova a cercarlo ovunque
        found = None
        for r, d, _ in os.walk(tree_root):
            if os.path.basename(r) == "web_server":
                found = r
                break
        if not found:
            print("  (cartella web_server non trovata nell'albero)")
            return
        ws = found
    report["web_server"]["root"] = rel(tree_root, ws)
    print(f"  radice: {rel(tree_root, ws)}")
    py_files = []
    for r, _, files in os.walk(ws):
        for fn in sorted(files):
            if fn.endswith(".py"):
                p = os.path.join(r, fn)
                nlines = len(read_lines(p))
                rp = rel(tree_root, p)
                py_files.append((rp, nlines))
    if not py_files:
        print("  (nessun .py trovato — potrebbe essere bytecode .pyc)")
    for rp, n in py_files:
        print(f"    {rp:<55} {n:>5} righe")
        report["web_server"]["files"].append({"path": rp, "lines": n})
    print(f"\n  Totale file Python: {len(py_files)}")


def inventory_cfg(tree_root, report):
    section("B. Config di ripresa — cfg/")
    cfg = os.path.join(tree_root, "cfg")
    report["cfg_files"] = []
    if not os.path.isdir(cfg):
        print("  (cartella cfg non trovata)")
        return
    seen = set()
    # prima i file noti, poi tutti gli altri json/rc/prop
    all_cfg = []
    for r, _, files in os.walk(cfg):
        for fn in sorted(files):
            p = os.path.join(r, fn)
            if is_text_file(p):
                all_cfg.append(p)
    # ordina mettendo i KNOWN_CFG per primi
    def sort_key(p):
        b = os.path.basename(p)
        return (0 if b in KNOWN_CFG else 1, rel(tree_root, p))
    for p in sorted(all_cfg, key=sort_key):
        rp = rel(tree_root, p)
        if rp in seen:
            continue
        seen.add(rp)
        size = os.path.getsize(p)
        tag = "  <-- noto" if os.path.basename(p) in KNOWN_CFG else ""
        print(f"    {rp:<55} {size:>7} B{tag}")
        report["cfg_files"].append({"path": rp, "bytes": size,
                                     "known": os.path.basename(p) in KNOWN_CFG})
    print(f"\n  Totale file di config: {len(report['cfg_files'])}")


def grep_keywords(tree_root, search_dirs, report):
    section("C. Dove si toccano BITRATE / CODEC / MODALITA' (file:riga)")
    compiled = {grp: [re.compile(p, re.IGNORECASE) for p in pats]
                for grp, pats in KEYWORDS.items()}
    report["hits"] = {grp: [] for grp in KEYWORDS}

    targets = []
    for d in search_dirs:
        base = os.path.join(tree_root, d)
        if not os.path.isdir(base):
            continue
        for r, _, files in os.walk(base):
            for fn in sorted(files):
                p = os.path.join(r, fn)
                if is_text_file(p):
                    targets.append(p)

    # Conteggio per gruppo e stampa raggruppata.
    for grp in KEYWORDS:
        hits = []
        regexes = compiled[grp]
        for p in targets:
            for i, line in enumerate(read_lines(p), 1):
                if any(rx.search(line) for rx in regexes):
                    hits.append((rel(tree_root, p), i, line.strip()[:120]))
        report["hits"][grp] = [{"file": f, "line": ln, "text": t} for f, ln, t in hits]
        print(f"\n  --- {grp.upper()} ({len(hits)} occorrenze) ---")
        if not hits:
            print("      (nessuna occorrenza)")
        # Raggruppa per file per leggibilita'; limita a 40 righe per gruppo.
        shown = 0
        last_file = None
        for f, ln, t in hits:
            if shown >= 40:
                print(f"      ... (+{len(hits)-shown} altre, vedi --json)")
                break
            if f != last_file:
                print(f"      {f}:")
                last_file = f
            print(f"        {ln:>5}: {t}")
            shown += 1


def inventory_osc(tree_root, report):
    section("D. Comandi OSC esposti (euristica dal codice Python)")
    ws = os.path.join(tree_root, "bin", "web_server")
    report["osc_commands"] = []
    if not os.path.isdir(ws):
        print("  (web_server non trovato)")
        return
    # Cerca riferimenti tipo "camera.xxx", "/osc/commands/execute", route, ecc.
    osc_re = re.compile(r"(camera\.[a-zA-Z_][\w\.]+|/osc/[\w/]+|_osc\.[\w]+)")
    found = {}
    for r, _, files in os.walk(ws):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(r, fn)
            for i, line in enumerate(read_lines(p), 1):
                for m in osc_re.findall(line):
                    found.setdefault(m, (rel(tree_root, p), i))
    for name in sorted(found):
        f, ln = found[name]
        print(f"    {name:<40} {f}:{ln}")
        report["osc_commands"].append({"name": name, "file": f, "line": ln})
    if not found:
        print("  (nessun pattern OSC riconosciuto — ispeziona osc_protocol/ e pro_osc/)")
    print(f"\n  Totale pattern OSC: {len(found)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inventario app-layer del firmware Titan.")
    ap.add_argument("tree_root", help="radice dell'albero estratto (es. extracted/titan_update)")
    ap.add_argument("--json", default=None, help="salva il report in formato JSON")
    args = ap.parse_args(argv)

    tree_root = os.path.abspath(args.tree_root)
    if not os.path.isdir(tree_root):
        eprint(f"ERRORE: directory non trovata: {tree_root}")
        return 2

    print(f"Inventario di: {tree_root}")
    report = {"tree_root": tree_root}

    inventory_webserver(tree_root, report)
    inventory_cfg(tree_root, report)
    grep_keywords(tree_root, search_dirs=["cfg", os.path.join("bin", "web_server")], report=report)
    inventory_osc(tree_root, report)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nReport JSON salvato in: {args.json}")

    print("\nFatto. Vedi docs/capture-config-and-webserver-map.md per la guida alle modifiche.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
