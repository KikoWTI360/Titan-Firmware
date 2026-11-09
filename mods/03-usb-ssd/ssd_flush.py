#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ssd_flush.py — scrive sull'SSD i dati che la camera tiene ancora in memoria, e
dice quando una ripresa è COMPLETA sul disco.

Gira SULLA CAMERA con il Python di Insta360 (/usr/local/bin/python3.6),
avviato da wti-titan.service (mod 03). Solo libreria standard, Python 3.6.

Perché: la camera chiude bene i file a ogni Stop, ma Linux li riversa sull'SSD
in ritardo, e l'indice di ogni MP4 (il 'moov') è l'ultima cosa scritta:
staccando l'SSD troppo presto i file non si aprono.
  09/11 VID_20261109_193727: senza scrittura periodica, 8 file su 8 senza indice;
  09/11 VID_20261109_202301: con 'sync' ogni 5 s e SSD staccato 10 s dopo lo
        Stop, 7 su 8 completi: un file viene chiuso più tardi degli altri.
Aspettare "qualche secondo di silenzio" non basta (i file non si chiudono tutti
insieme), quindi qui il criterio è strutturale: una ripresa è pronta quando
TUTTI gli origin_1..8.mp4 sono MP4 chiusi (strutture di primo livello che
arrivano esattamente alla fine del file, 'moov' compreso). Allora 'sync' finale
e nel registro "SSD PRONTO" con l'ora: da lì l'SSD si può staccare.

Inoltre, ogni INTERVAL secondi mentre l'SSD è montato, 'sync' dell'SSD: durante
la registrazione non aggiunge dati, anticipa scritture che Linux farebbe
comunque.

Se una ripresa resta incompleta e ferma per GIVE_UP secondi (una lente non ha
scritto, o non ha chiuso), lo scrive nel registro con i file mancanti.

Registro: journalctl -u wti-titan   (oppure mods/03-usb-ssd/install.sh --stato)
"""

import glob
import os
import struct
import subprocess
import sys
import time

MNT = os.environ.get("MNT", "/mnt/udisk1")
INTERVAL = int(os.environ.get("INTERVAL", "5"))
GIVE_UP = int(os.environ.get("GIVE_UP", "60"))
LENSES = int(os.environ.get("LENSES", "8"))


def say(msg):
    print("ssd_flush: " + msg, flush=True)


def _sync_f_works():
    try:
        return subprocess.call(["sync", "-f", "/"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


SYNC_F = _sync_f_works()


def flush():
    """'sync -f' sull'SSD (solo quel filesystem), oppure 'sync' di tutto."""
    if SYNC_F:
        subprocess.call(["sync", "-f", MNT])
    else:
        os.sync()


def mounted():
    try:
        with open("/proc/mounts") as f:
            return any(line.split()[1] == MNT for line in f if len(line.split()) > 1)
    except OSError:
        return False


def latest():
    dirs = [d for d in glob.glob(os.path.join(MNT, "VID_*")) if os.path.isdir(d)]
    if not dirs:
        return None
    try:
        return max(dirs, key=os.path.getmtime)
    except OSError:
        return None


def signature(d):
    """Nomi e dimensioni dei file della ripresa: cambia finché la camera scrive."""
    if not d:
        return None
    out = []
    try:
        for name in sorted(os.listdir(d)):
            try:
                out.append((name, os.path.getsize(os.path.join(d, name))))
            except OSError:
                pass
    except OSError:
        return None
    return tuple(out)


def mp4_closed(path):
    """True se il file è un MP4 chiuso: catena di strutture di primo livello
    coerente fino alla fine esatta del file, con 'moov'. Durante la
    registrazione la struttura dei dati è aperta e la catena non torna."""
    try:
        size = os.path.getsize(path)
        types = set()
        pos = 0
        with open(path, "rb") as f:
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return False
                bsize, btype = struct.unpack(">I4s", hdr[:8])
                if bsize == 1:
                    if len(hdr) < 16:
                        return False
                    bsize = struct.unpack(">Q", hdr[8:16])[0]
                elif bsize == 0:
                    return False            # "fino alla fine": ancora aperto
                if bsize < 8 or pos + bsize > size:
                    return False
                types.add(btype)
                pos += bsize
        return pos == size and b"moov" in types
    except OSError:
        return False


def missing(d):
    """I file della ripresa che non sono ancora MP4 chiusi."""
    out = []
    for n in range(1, LENSES + 1):
        p = os.path.join(d, "origin_%d.mp4" % n)
        if not os.path.exists(p):
            out.append("origin_%d.mp4 assente" % n)
        elif not mp4_closed(p):
            out.append("origin_%d.mp4 senza indice" % n)
    return out


def main():
    say("sync ogni %d s (%s), 'SSD PRONTO' quando gli %d origin_N.mp4 della ripresa "
        "sono chiusi, su %s" % (INTERVAL, "sync -f" if SYNC_F else "sync", LENSES, MNT))
    last = signature(latest()) if mounted() else None
    writing = False
    started = last_change = time.time()
    last_flush = 0.0
    folder = None
    while True:
        now = time.time()
        if not mounted():
            writing = False
            last = None
            time.sleep(1)
            continue
        d = latest()
        sig = signature(d)
        if sig != last:
            last = sig
            last_change = now
            if not writing or d != folder:
                writing, folder, started = True, d, now
                say("scrittura in corso: %s (%s)" % (os.path.basename(d or "?"),
                                                    time.strftime("%H:%M:%S")))
        if now - last_flush >= INTERVAL:
            flush()
            last_flush = time.time()
        # Si guarda la struttura solo quando i file sono fermi da 1 s: durante
        # la registrazione sarebbe lavoro inutile.
        if writing and folder and now - last_change >= 1:
            todo = missing(folder)
            if not todo:
                flush()
                last_flush = time.time()
                say("SSD PRONTO: %s completa sul disco (%s, %d s dall'inizio delle "
                    "scritture)" % (os.path.basename(folder), time.strftime("%H:%M:%S"),
                                    int(time.time() - started)))
                writing = False
            elif now - last_change >= GIVE_UP:
                flush()
                last_flush = time.time()
                say("ATTENZIONE: %s ferma da %d s ma incompleta: %s" % (
                    os.path.basename(folder), GIVE_UP, ", ".join(todo)))
                writing = False
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
