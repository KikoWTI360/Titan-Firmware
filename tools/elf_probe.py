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

  symbols <elf> [regex]
      Simboli della tabella dei simboli (.symtab/.dynsym) con tipo, dimensione,
      sezione e offset nel file. Serve a trovare dove sta un dato (es. il bitmap
      del logo d'avvio in bootanimation) per patcharlo byte per byte.

  file <path>
      Impronta di un file registrato: primi byte, presenza di box MP4 in chiaro
      (ftyp/moov/mdat), entropia a blocchi (per capire se e' cifrato tutto o
      solo una parte).

  mp4json <file> [<file> ...]
      Una riga JSON per file con i numeri della codifica: dimensione,
      risoluzione, fotogrammi, fotogrammi chiave, dimensioni medie, intervallo
      nominale tra fotogrammi, salti (fotogrammi persi) e durata dell'ultimo
      fotogramma. La usa tools/titan_bitrate_test.py, sulla camera.

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
SYM_TYPES = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE", 6: "TLS"}
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
            secs.append(dict(name=name, type=stype, addr=addr, offset=soff, size=size,
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

    def symbols(self):
        """Voci di .symtab e .dynsym con indirizzo, dimensione, sezione e offset nel file."""
        d, e = self.data, self.e
        secs = self.sections()
        out = []
        for symsec in secs:
            if symsec["type"] not in (2, 11):  # SHT_SYMTAB, SHT_DYNSYM
                continue
            sbase = secs[symsec["link"]]["offset"]
            entsize = symsec["entsize"] or (24 if self.is64 else 16)
            for i in range(symsec["size"] // entsize):
                off = symsec["offset"] + i * entsize
                if self.is64:
                    st_name, st_info, _, st_shndx, st_value, st_size = \
                        struct.unpack_from(e + "IBBHQQ", d, off)
                else:
                    st_name, st_value, st_size, st_info, _, st_shndx = \
                        struct.unpack_from(e + "IIIBBH", d, off)
                if not st_name:
                    continue
                sec = secs[st_shndx] if 0 < st_shndx < len(secs) else None
                file_off = None
                if sec and sec["type"] != 8:  # SHT_NOBITS (.bss) non ha byte nel file
                    file_off = st_value - sec["addr"] + sec["offset"]
                out.append(dict(name=self._cstr(sbase + st_name), value=st_value,
                                size=st_size, type=SYM_TYPES.get(st_info & 0xF, "?"),
                                section=sec.get("name_str", "?") if sec else "-",
                                file_offset=file_off))
        return out


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


def cmd_symbols(elf, regex=None):
    rx = re.compile(regex or ".", re.I)
    seen = set()
    rows = []
    for s in Elf(elf).symbols():
        key = (s["name"], s["value"])
        if key in seen or not rx.search(s["name"]):
            continue
        seen.add(key)
        rows.append(s)
    for s in sorted(rows, key=lambda s: s["name"]):
        off = f"0x{s['file_offset']:x}" if s["file_offset"] is not None else "-"
        print(f"  {s['name']:<40} {s['type']:<7} size={s['size']:<7} "
              f"sezione={s['section']:<10} offset_file={off}")
    print(f"\n{len(rows)} simboli per /{rx.pattern}/")


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


# Strutture che compaiono solo in un MP4 cifrato con lo standard CENC/CBCS: il
# contenitore resta valido, ma la traccia diventa 'encv'/'enca' con lo schema in
# 'sinf'/'schm'/'tenc', e le chiavi o i loro riferimenti in 'pssh'/'senc'.
CRYPTO_BOXES = (b"encv", b"enca", b"sinf", b"schm", b"tenc", b"pssh", b"senc")
CODEC_TAGS = (b"avc1", b"avc3", b"hvc1", b"hev1", b"mp4v", b"mp4a")


def mp4_top_boxes(f, size):
    """Le strutture di primo livello di un MP4: [(tipo, offset, dimensione)].

    Si salta da una struttura all'altra leggendo solo le intestazioni, quindi va
    bene anche per file da molti GB. Si ferma su un'intestazione che non è
    quella di un MP4.
    """
    boxes, pos = [], 0
    while pos + 8 <= size and len(boxes) < 1000:
        f.seek(pos)
        hdr = f.read(16)
        bsize, btype = struct.unpack(">I4s", hdr[:8])
        if bsize == 1 and len(hdr) >= 16:
            bsize = struct.unpack(">Q", hdr[8:16])[0]
        elif bsize == 0:
            bsize = size - pos
        if bsize < 8 or not all(32 <= c < 127 for c in btype):
            break
        boxes.append((btype.decode("ascii"), pos, bsize))
        pos += bsize
    return boxes


def mp4_video_info(moov_data, mdat_size):
    """(larghezza, altezza, secondi, Mbit/s) dall'indice di un MP4, o None.

    Risoluzione dal 'tkhd' della traccia video (gli ultimi 8 byte, in 16.16),
    durata da 'mvhd', bitrate medio come dimensione di 'mdat' / durata.
    """
    def u32(off):
        return struct.unpack(">I", moov_data[off:off + 4])[0]
    w = h = 0
    pos = moov_data.find(b"tkhd")
    while pos >= 4:
        end = pos - 4 + u32(pos - 4)
        if pos - 4 < end <= len(moov_data):
            tw, th = u32(end - 8) >> 16, u32(end - 4) >> 16
            if tw and th:
                w, h = tw, th
                break
        pos = moov_data.find(b"tkhd", pos + 4)
    seconds = None
    m = moov_data.find(b"mvhd")
    if m >= 0:
        if moov_data[m + 4] == 1:
            timescale = u32(m + 24)
            duration = struct.unpack(">Q", moov_data[m + 28:m + 36])[0]
        else:
            timescale, duration = u32(m + 16), u32(m + 20)
        if timescale:
            seconds = duration / timescale
    if not (w or seconds):
        return None
    mbps = mdat_size * 8 / seconds / 1e6 if seconds else None
    return w, h, seconds, mbps


def mp4_children(data, start, end):
    """(tipo, inizio contenuto, fine) per le strutture figlie in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8].decode("latin-1")
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield kind, pos + hdr, pos + size
        pos += size


H264_PROFILES = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High",
                 110: "High 10", 122: "High 4:2:2", 244: "High 4:4:4"}


def mp4_avc_info(moov_data):
    """(profilo, livello) H.264 dal box avcC, es. ("High", "5.1"), o None.

    Il livello dice quanto è grande il video che un decoder deve reggere:
    i decoder hardware dei Mac e la maggior parte dei lettori arrivano a 5.2
    (circa 9,4 milioni di pixel per fotogramma); 5280x2972 sono 15,7 milioni.
    """
    i = moov_data.find(b"avcC")
    if i < 0 or i + 8 > len(moov_data):
        return None
    prof, level = moov_data[i + 5], moov_data[i + 7]
    return H264_PROFILES.get(prof, str(prof)), "%d.%d" % (level // 10, level % 10)


def mp4_frame_stats(moov_data):
    """Fotogrammi della traccia video: (n, n chiave, [dimensioni], {indici chiave}) o None.

    Legge 'stsz' (dimensione di ogni fotogramma) e 'stss' (quali sono fotogrammi
    chiave) nella traccia con handler 'vide'. Senza 'stss' sono tutti chiave.
    """
    if moov_data[4:8] != b"moov":
        return None
    def child(start, end, name):
        return next(((s, e) for k, s, e in mp4_children(moov_data, start, end) if k == name), None)
    for kind, s, e in mp4_children(moov_data, 8, len(moov_data)):
        if kind != "trak":
            continue
        mdia = child(s, e, "mdia")
        hdlr = mdia and child(mdia[0], mdia[1], "hdlr")
        if not hdlr or moov_data[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        minf = child(mdia[0], mdia[1], "minf")
        stbl = minf and child(minf[0], minf[1], "stbl")
        stsz = stbl and child(stbl[0], stbl[1], "stsz")
        if not stsz:
            return None
        fixed, count = struct.unpack(">II", moov_data[stsz[0] + 4:stsz[0] + 12])
        if fixed:
            sizes = [fixed] * count
        else:
            sizes = list(struct.unpack(">%dI" % count, moov_data[stsz[0] + 12:stsz[0] + 12 + 4 * count]))
        stss = child(stbl[0], stbl[1], "stss")
        if stss:
            n = struct.unpack(">I", moov_data[stss[0] + 4:stss[0] + 8])[0]
            keys = set(i - 1 for i in struct.unpack(">%dI" % n, moov_data[stss[0] + 8:stss[0] + 8 + 4 * n]))
        else:
            keys = set(range(count))
        # Tempi: 'stts' = coppie (quanti fotogrammi, durata di ciascuno) nella
        # scala di 'mdhd'; 'elst' = eventuale ritardo/taglio all'inizio della traccia.
        timing = {"timescale": None, "stts": [], "elst": []}
        mdhd = child(mdia[0], mdia[1], "mdhd")
        if mdhd:
            off = mdhd[0] + (20 if moov_data[mdhd[0]] == 1 else 12)
            timing["timescale"] = struct.unpack(">I", moov_data[off:off + 4])[0]
        stts = child(stbl[0], stbl[1], "stts")
        if stts:
            n = struct.unpack(">I", moov_data[stts[0] + 4:stts[0] + 8])[0]
            vals = struct.unpack(">%dI" % (2 * n), moov_data[stts[0] + 8:stts[0] + 8 + 8 * n])
            timing["stts"] = list(zip(vals[0::2], vals[1::2]))
        edts = child(s, e, "edts")
        elst = edts and child(edts[0], edts[1], "elst")
        if elst:
            v1 = moov_data[elst[0]] == 1
            n = struct.unpack(">I", moov_data[elst[0] + 4:elst[0] + 8])[0]
            pos = elst[0] + 8
            for _ in range(n):
                if v1:
                    seg, mt = struct.unpack(">Qq", moov_data[pos:pos + 16]); pos += 20
                else:
                    seg, mt = struct.unpack(">Ii", moov_data[pos:pos + 8]); pos += 12
                timing["elst"].append((seg, mt))
        return count, len(keys), sizes, keys, timing
    return None


def mp4_summary(path):
    """I numeri della codifica di un MP4, come dizionario (per mp4json)."""
    out = {"path": path}
    try:
        size = os.path.getsize(path)
        out["size"] = size
        with open(path, "rb") as f:
            boxes = mp4_top_boxes(f, size)
            moov = next((b for b in boxes if b[0] == "moov"), None)
            if not moov or moov[2] > 64 * 1024 * 1024:
                out["error"] = "moov assente: file non chiuso?"
                return out
            f.seek(moov[1])
            moov_data = f.read(moov[2])
        mdat = sum(b[2] for b in boxes if b[0] == "mdat")
        info = mp4_video_info(moov_data, mdat)
        if info:
            out["w"], out["h"], out["seconds"] = info[0], info[1], info[2]
        avc = mp4_avc_info(moov_data)
        if avc:
            out["h264_profile"], out["h264_level"] = avc
        stats = mp4_frame_stats(moov_data)
        if not stats:
            out["error"] = "traccia video non trovata"
            return out
        n, nk, sizes, keys, timing = stats
        out["frames"], out["keys"] = n, nk
        out["video_bytes"] = sum(sizes)
        key_sizes = [sizes[i] for i in keys if i < n]
        other = [sizes[i] for i in range(n) if i not in keys]
        out["key_avg"] = sum(key_sizes) / len(key_sizes) if key_sizes else None
        out["other_avg"] = sum(other) / len(other) if other else None
        out["max_frame"] = max(sizes) if sizes else None
        ts, runs = timing["timescale"], timing["stts"]
        if ts and runs:
            nominal = max(runs, key=lambda r: r[0])[1]
            out["nominal_ms"] = nominal * 1000 / ts
            out["last_ms"] = runs[-1][1] * 1000 / ts
            # Fotogrammi che durano piu' di 1,5 volte il nominale = mancano
            # fotogrammi prima del successivo. L'ultimo non conta (dura fino allo stop).
            gaps = missing = 0
            for i, (count, delta) in enumerate(runs):
                if i == len(runs) - 1:
                    count -= 1
                if count > 0 and delta > 1.5 * nominal:
                    gaps += count
                    missing += count * (round(delta / nominal) - 1)
            out["gaps"], out["missing"] = gaps, missing
    except Exception as ex:
        out["error"] = "%s: %s" % (type(ex).__name__, ex)
    return out


def cmd_mp4json(paths):
    import json
    for path in paths:
        print(json.dumps(mp4_summary(path)), flush=True)


def cmd_file(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(64)
        f.seek(0)
        first = f.read(4096)
        f.seek(size // 2)
        mid = f.read(4096)
        boxes = mp4_top_boxes(f, size) if first[4:8] == b"ftyp" else []
        moov = next((b for b in boxes if b[0] == "moov"), None)
        moov_data = b""
        if moov and moov[2] <= 64 * 1024 * 1024:
            f.seek(moov[1])
            moov_data = f.read(moov[2])
    print(f"File: {path}  ({size} byte)")
    print("Primi 64 byte (hex):")
    for i in range(0, len(head), 16):
        chunk = head[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}  {hexs:<48}  {asc}")
    print(f"\nEntropia primo blocco: {shannon(first):.2f}/8   meta' file: {shannon(mid):.2f}/8")

    if not boxes:
        print("Non è un MP4 (nessuna struttura 'ftyp' all'inizio).")
        print("  (entropia vicina a 8.0 = dati casuali, cifrati o compressi)")
        return
    print("Strutture MP4 di primo livello:")
    for btype, off, bsize in boxes:
        print(f"  {btype}  offset {off:>12}  {bsize:>14} byte")
    if boxes[-1][1] + boxes[-1][2] < size:
        print(f"  (dopo l'ultima struttura restano {size - boxes[-1][1] - boxes[-1][2]} byte non interpretabili)")
    if not moov:
        print("Verdetto: MP4 SENZA 'moov' (indice dei fotogrammi): file incompleto o "
              "registrazione non chiusa, non leggibile così com'è.")
        return
    codecs = [t.decode() for t in CODEC_TAGS if t in moov_data]
    crypto = [t.decode() for t in CRYPTO_BOXES if t in moov_data]
    print(f"Codec dichiarati nell'indice: {', '.join(codecs) or 'nessuno riconosciuto'}")
    mdat_size = sum(b[2] for b in boxes if b[0] == "mdat")
    vinfo = mp4_video_info(moov_data, mdat_size) if moov_data else None
    if vinfo:
        w, h, secs, mbps = vinfo
        parts = []
        if w:
            parts.append(f"{w}x{h}")
        if secs:
            parts.append(f"durata {secs:.1f} s")
        if mbps:
            rate = f"~{mbps:.1f} Mbit/s" if mbps >= 1 else f"~{mbps * 1000:.0f} kbit/s"
            parts.append(f"{rate} (video+audio)")
        print(f"Video: {', '.join(parts)}")
        avc = mp4_avc_info(moov_data)
        if avc:
            print(f"H.264: profilo {avc[0]}, livello {avc[1]}")
        stats = mp4_frame_stats(moov_data)
        if stats:
            n, nk, sizes, keys, timing = stats
            line = f"Fotogrammi: {n}"
            if secs:
                line += f" ({n / secs:.2f} al secondo)"
            line += f", chiave {nk}" + (f" (uno ogni {n / nk:.1f})" if nk else "")
            print(line)
            px = w * h if w else 0
            def desc(label, group):
                if not group:
                    return
                avg = sum(group) / len(group)
                bpp = f", {avg * 8 / px:.2f} bit/pixel" if px else ""
                print(f"  {label:<9} medio {avg / 1024:>8.0f} KB, max {max(group) / 1024:>8.0f} KB{bpp}")
            desc("chiave", [sizes[i] for i in sorted(keys) if i < n])
            desc("altri", [sizes[i] for i in range(n) if i not in keys])
            desc("tutti", sizes)
            ts = timing["timescale"]
            if ts and timing["stts"]:
                runs = ", ".join(f"{c} x {d * 1000 / ts:.0f} ms" for c, d in timing["stts"][:6])
                if len(timing["stts"]) > 6:
                    runs += f", ... ({len(timing['stts'])} gruppi)"
                print(f"Tempi dei fotogrammi: {runs}")
            for seg, mt in timing["elst"]:
                if mt == -1:
                    print(f"  elst: vuoto iniziale (ritardo) di {seg} unità del filmato")
                elif mt and ts:
                    print(f"  elst: la presentazione parte da {mt * 1000 / ts:.0f} ms del tempo interno (normale con i B-frame)")
    print(f"Strutture di cifratura (CENC): {', '.join(crypto) or 'nessuna'}")
    if crypto:
        print("Verdetto: MP4 CIFRATO con lo standard CENC: il contenitore è leggibile, "
              "i fotogrammi no senza la chiave.")
    else:
        print("Verdetto: MP4 standard, nessun segno di cifratura. L'entropia alta dei dati "
              "è quella del video compresso. Prova definitiva: aprirlo con un lettore.")


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
        elif cmd == "symbols":
            cmd_symbols(argv[2], argv[3] if len(argv) > 3 else None)
        elif cmd == "file":
            cmd_file(argv[2])
        elif cmd == "mp4json":
            if len(argv) < 3:
                raise IndexError
            cmd_mp4json(argv[2:])
        else:
            print(__doc__)
            return 1
    except IndexError:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    # Sulla camera "adb shell" non imposta la lingua: Python 3.6 scrive in ASCII
    # e si ferma alla prima lettera accentata. Forziamo UTF-8.
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main(sys.argv))
