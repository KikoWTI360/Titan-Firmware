#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
set_bitrate.py — cambia il bitrate VIDEO nei JSON di ripresa del Titan.

Funziona su qualunque JSON di cfg/ (vid_customer.json, takevideo_template.json,
takelive_template.json, ...): percorre tutto l'albero e modifica solo i nodi che
sono stream VIDEO (hanno "bitrate" e non sono audio). I nodi audio (mime aac,
oppure con samplerate/channelLayout, es. "bitrate": 128) non vengono toccati.

Unita': i JSON del Titan usano kbps con base 1024 (184320 = 180 * 1024 = 180 Mbps).

Uso:
    # anteprima, non scrive nulla
    python3 set_bitrate.py cfg/vid_customer.json --mbps 200 --dry-run

    # scrive una copia modificata accanto all'originale
    python3 set_bitrate.py cfg/vid_customer.json --mbps 200 --out vid_customer.patched.json

    # modifica in place (crea prima <file>.orig se non esiste)
    python3 set_bitrate.py cfg/vid_customer.json --mbps 200 --inplace

    # solo gli stream che oggi hanno un certo valore (es. solo i 180 Mbps)
    python3 set_bitrate.py cfg/takevideo_template.json --mbps 200 --only-current 184320 --dry-run

Nota: il file viene riserializzato (indentazione di 4 spazi), quindi cambia la
formattazione ma non il contenuto. La camera legge JSON standard.
"""

import argparse
import json
import os
import shutil
import sys
from collections import OrderedDict

AUDIO_MIME = {"aac", "pcm", "mp3", "opus"}


def is_audio(node):
    return ("channelLayout" in node or "samplerate" in node
            or str(node.get("mime", "")).lower() in AUDIO_MIME)


def walk(node, path, visit):
    if isinstance(node, dict):
        visit(node, path)
        for k, v in node.items():
            walk(v, f"{path}.{k}" if path else k, visit)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", visit)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cambia il bitrate video nei JSON del Titan.")
    ap.add_argument("input", help="file JSON di cfg/")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mbps", type=float, help="nuovo bitrate in Mbps (convertito in kbps*1024)")
    g.add_argument("--kbps", type=int, help="nuovo bitrate grezzo, nell'unita' del file")
    g.add_argument("--factor", type=float, help="moltiplica il bitrate attuale (es. 1.2)")
    ap.add_argument("--only-current", type=int, default=None,
                    help="tocca solo gli stream il cui bitrate attuale e' questo valore")
    o = ap.add_mutually_exclusive_group()
    o.add_argument("--out", help="scrivi il risultato in questo file")
    o.add_argument("--inplace", action="store_true", help="sovrascrivi l'input (con backup .orig)")
    ap.add_argument("--dry-run", action="store_true", help="mostra le modifiche senza scrivere")
    args = ap.parse_args(argv)

    with open(args.input, "r") as f:
        data = json.load(f, object_pairs_hook=OrderedDict)

    changes = []

    def visit(node, path):
        if "bitrate" not in node or is_audio(node):
            return
        old = node["bitrate"]
        if not isinstance(old, (int, float)):
            return
        if args.only_current is not None and old != args.only_current:
            return
        if args.mbps is not None:
            new = int(round(args.mbps * 1024))
        elif args.kbps is not None:
            new = args.kbps
        else:
            new = int(round(old * args.factor))
        if new != old:
            node["bitrate"] = new
            changes.append((path or "<root>", node.get("mime", "?"), old, new))

    walk(data, "", visit)

    if not changes:
        print("Nessuno stream video da modificare (controlla --only-current).")
        return 1

    print(f"{len(changes)} stream video modificati in {args.input}:")
    for path, mime, old, new in changes:
        print(f"  {path:<45} {mime:<5} {old:>7} -> {new:>7}  "
              f"({old / 1024:.0f} -> {new / 1024:.0f} Mbps)")

    if args.dry_run:
        print("(dry-run: nessun file scritto)")
        return 0

    if args.inplace:
        backup = args.input + ".orig"
        if not os.path.exists(backup):
            shutil.copy2(args.input, backup)
            print(f"Backup: {backup}")
        else:
            print(f"Backup gia' presente, non sovrascritto: {backup}")
        dest = args.input
    elif args.out:
        dest = args.out
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    else:
        print("Specifica --out, --inplace oppure --dry-run.", file=sys.stderr)
        return 2

    with open(dest, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(f"Scritto: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
