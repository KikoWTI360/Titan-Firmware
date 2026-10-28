#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
elf_probe.py — sonda ELF/file per capire dove (e se) il firmware cifra.

Legge gli ELF senza dipendere da readelf/objdump (parsing diretto, cosi' gira
anche su macOS). Sottocomandi:

  deps <tree_root>
      Per ogni ELF in bin/ e lib/: elenca le librerie richieste (DT_NEEDED) e
      marca chi dipende da libcrypto/libssl. Risponde: "chi usa la crypto?".

  imports <elf> [regex]
      Simboli importati (dynsym non definiti). Con regex filtra (default:
      crypto). Risponde: "camerad chiama davvero funzioni di cifratura?".

  strings <elf> [regex]
      Stringhe-dato stampabili (esclude i simboli C++ mangled), filtrate per
      regex (default: chiavi/percorsi/crypto). Trova chiavi JSON tipo "encrypt",
      percorsi SSD, estensioni file.

  file <path>
      Impronta di un file registrato: primi byte, presenza di box MP4 in chiaro
      (ftyp/moov/mdat), entropia a blocchi (per capire se e' cifrato tutto o
      solo una parte).

Uso tipico:
    python3 tools/elf_probe.py deps extracted/titan_update
    python3 tools/elf_probe.py imports extracted/titan_update/bin/camerad
    python3 tools/elf_probe.py strings extracted/titan_update/bin/camerad
    python3 tools/elf_probe.py file /Volumes/SSD/qualche_file
"""

import math
import os
import re
import struct
import sys

CRYPTO_RE = r"(encrypt|decrypt|crypt|cipher|\baes\b|evp_|rsa|\bdes\b|rc4|blowfish|chacha|\bxor\b|scramb|obfus|secret|secure|\bkey\b)"
STR_RE = r"(encrypt|decrypt|crypt|cipher|aes|evp_|rsa|secret|secure|\bkey\b|scramb|obfus|/mnt|/media|/udisk|udisk|/storage|ssd|\.mp4|\.insv|\.lrv|\.dat|\.enc|origin|storage_loc|save)"
MANGLED_RE = re.compile(r"(^_Z|_GLOBAL_|^N\d|St\d|^_ZN|^_ZL|Eigen|ceres|tinyxml|^std)")


# ----------------------------- ELF parsing -----------------------------------

class Elf:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:4] != b"\x7fELF":
            raise ValueError("non e' un ELF")
        self.is64 = d[4] == 2
        self.le = d[5] == 1
        self.path = path
        e = "<" if self.le else ">"
        if self.is64:
            # e_shoff@0x28 (Q), e_shentsize@0x3a (H), e_shnum@0x3c (H), e_shstrndx@0x3e (H)
            self.shoff = struct.unpack_from(e + "Q", d, 0x28)[0]
            self.shentsize = struct.unpack_from(e + "H", d, 0x3A)[0]
            self.shnum = struct.unpack_from(e + "H", d, 0x3C)[0]
            self.shstrndx = struct.unpack_from(e + "H", d, 0x3E)[0]
        else:
            self.shoff = struct.unpack_from(e + "I", d, 0x20)[0]
            self.shentsize = struct.unpack_from(e + "H", d, 0x2E)[0]
            self.shnum = struct.unpack_from(e + "H", d, 0x30)[0]
            self.shstrndx = struct.unpack_from(e + "H", d, 0x32)[0]
        self.e = e
        self._sections = None

    def sections(self):
        if self._sections is not None:
            return self._sections
        d, e = self.data, self.e
        secs = []
        for i in range(self.shnum):
            off = self.shoff + i * self.shentsize
            if self.is64:
                (name, stype, flags, addr, soff, size, link, info, align, entsize) = \
                    struct.unpack_from(e + "IIQQQQIIQQ", d, off)
            else:
                (name, stype, flags, addr, soff, size, link, info, align, entsize) = \
                    struct.unpack_from(e + "IIIIIIIIII", d, off)
            secs.append(dict(name=name, type=stype, offset=soff, size=size,
                             link=link, entsize=entsize))
        # risolvi i nomi di sezione
        if self.shstrndx < len(secs):
            strtab = secs[self.shstrndx]
            base = strtab["offset"]
            for s in secs:
                s["name_str"] = self._cstr(base + s["name"])
        self._sections = secs
        return secs

    def _cstr(self, off):
        d = self.data
        end = d.find(b"\x00", off)
        return d[off:end].decode("latin-1", "replace") if end >= 0 else ""

    def _find(self, stype=None, name=None):
        for s in self.sections():
            if stype is not None and s["type"] == stype:
                return s
            if name is not None and s.get("name_str") == name:
                return s
        return None

    def needed(self):
        """Elenco DT_NEEDED + eventuale SONAME."""
        dyn = self._find(stype=6)  # SHT_DYNAMIC
        if not dyn:
            return [], None
        strsec = self.sections()[dyn["link"]]
        sbase = strsec["offset"]
        d, e = self.data, self.e
        entries = []
        soname = None
        step = 16 if self.is64 else 8
        off = dyn["offset"]
        for _ in range(dyn["size"] // step):
            if self.is64:
                tag, val = struct.unpack_from(e + "qQ", d, off)
            else:
                tag, val = struct.unpack_from(e + "iI", d, off)
            off += step
            if tag == 0:  # DT_NULL
                break
            if tag == 1:  # DT_NEEDED
                entries.append(self._cstr(sbase + val))
            elif tag == 14:  # DT_SONAME
                soname = self._cstr(sbase + val)
        return entries, soname

    def imported_symbols(self):
        """Simboli dynsym non definiti (SHN_UNDEF) = importati da altre lib."""
        dynsym = self._find(name=".dynsym") or self._find(stype=11)
        if not dynsym:
            return []
        strsec = self.sections()[dynsym["link"]]
        sbase = strsec["offset"]
        d, e = self.data, self.e
        syms = []
        entsize = dynsym["entsize"] or (24 if self.is64 else 16)
        n = dynsym["size"] // entsize
        for i in range(n):
            off = dynsym["offset"] + i * entsize
            if self.is64:
                st_name = struct.unpack_from(e + "I", d, off)[0]
                st_shndx = struct.unpack_from(e + "H", d, off + 6)[0]
            else:
                st_name = struct.unpack_from(e + "I", d, off)[0]
                st_shndx = struct.unpack_from(e + "H", d, off + 14)[0]
            if st_shndx == 0 and st_name:  # SHN_UNDEF, con nome
                syms.append(self._cstr(sbase + st_name))
        return syms


# ----------------------------- subcommands ------------------------------------

def iter_elfs(root):
    for sub in ("bin", "lib"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for r, _, files in os.walk(base):
            for fn in sorted(files):
                p = os.path.join(r, fn)
                try:
                    with open(p, "rb") as f:
                        if f.read(4) == b"\x7fELF":
                            yield p
                except OSError:
                    pass


def cmd_deps(root):
    print(f"Dipendenze dei binari in {root} (bin/ e lib/):\n")
    hits = []
    for p in iter_elfs(root):
        try:
            needed, soname = Elf(p).needed()
        except Exception as ex:
            print(f"  {os.path.relpath(p, root):<28} ERRORE: {ex}")
            continue
        crypto = [n for n in needed if re.search(r"crypto|ssl", n, re.I)]
        mark = "  <<< CRYPTO" if crypto else ""
        print(f"  {os.path.relpath(p, root):<28} {', '.join(needed) if needed else '(nessuna)'}{mark}")
        if crypto:
            hits.append(os.path.relpath(p, root))
    print("\nBinari che dipendono da libcrypto/libssl:")
    for h in hits:
        print(f"  - {h}")
    if not hits:
        print("  (nessuno)")
    print("\nNota: libcrypto/libssl servono anche a HTTPS (nginx, update, rtmp).")
    print("La dipendenza va incrociata col percorso di registrazione (vedi 'imports').")


def cmd_imports(elf, regex=None):
    rx = re.compile(regex or CRYPTO_RE, re.I)
    syms = sorted(set(Elf(elf).imported_symbols()))
    hit = [s for s in syms if rx.search(s)]
    print(f"{elf}: {len(syms)} simboli importati, {len(hit)} corrispondono a /{rx.pattern}/\n")
    for s in hit:
        print(f"  {s}")
    if not hit:
        print("  (nessuna funzione di cifratura importata)")


def cmd_strings(elf, regex=None):
    rx = re.compile(regex or STR_RE, re.I)
    data = open(elf, "rb").read()
    found = set()
    cur = bytearray()
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= 4:
                s = cur.decode("ascii")
                if rx.search(s) and not MANGLED_RE.search(s):
                    found.add(s)
            cur = bytearray()
    for s in sorted(found):
        print(f"  {s}")
    print(f"\n{len(found)} stringhe-dato (simboli C++ esclusi) per /{rx.pattern}/")


def shannon(block):
    if not block:
        return 0.0
    counts = [0] * 256
    for b in block:
        counts[b] += 1
    n = len(block)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def cmd_file(path):
    with open(path, "rb") as f:
        head = f.read(64)
        f.seek(0)
        first = f.read(4096)
        f.seek(os.path.getsize(path) // 2)
        mid = f.read(4096)
    print(f"File: {path}  ({os.path.getsize(path)} byte)")
    print("Primi 64 byte (hex):")
    for i in range(0, len(head), 16):
        chunk = head[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}  {hexs:<48}  {asc}")
    # box MP4 in chiaro?
    boxes = [b"ftyp", b"moov", b"mdat", b"free", b"mvhd", b"trak"]
    present = [b.decode() for b in boxes if b in first or b in mid]
    print(f"\nBox MP4 in chiaro trovati: {present if present else 'NESSUNO'}")
    print(f"Entropia primo blocco: {shannon(first):.2f}/8   meta' file: {shannon(mid):.2f}/8")
    print("  (vicino a 8.0 = dati casuali/cifrati o gia' compressi; un MP4 in chiaro")
    print("   mostra comunque 'ftyp'/'moov' leggibili all'inizio)")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    try:
        if cmd == "deps":
            cmd_deps(argv[2])
        elif cmd == "imports":
            cmd_imports(argv[2], argv[3] if len(argv) > 3 else None)
        elif cmd == "strings":
            cmd_strings(argv[2], argv[3] if len(argv) > 3 else None)
        elif cmd == "file":
            cmd_file(argv[2])
        else:
            print(__doc__)
            return 1
    except IndexError:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
