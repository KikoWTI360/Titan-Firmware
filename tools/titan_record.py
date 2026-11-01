#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
titan_record.py — registra una ripresa di prova dalla Titan, da terminale.

Usa l'interfaccia di controllo della camera (OSC su HTTP, porta 20000) con gli
stessi comandi dell'app Insta360. I corpi vengono da street-view-suite,
profiles/titan.profile.json, dove sono stati catturati in rete il 27/09:

  1. camera._connect       apre la sessione (esclusiva) e imposta l'ora
  2. camera._startRecording h264 5280x2972, 180000 kbps, 5 fps, solo i file
                           originali delle lenti; la risposta contiene
                           _recordUrl, la cartella della ripresa
  3. per N secondi tiene viva la sessione: POST /osc/state ogni secondo (la
     camera chiude la sessione dopo 10 s di silenzio)
  4. camera._stopRecording, poi aspetta che la camera finisca di chiudere i
     file (bit 7 di _cam_state)
  5. camera._disconnect, così l'app può ricollegarsi

Se lo interrompi con Ctrl-C durante la registrazione, ferma la registrazione e
chiude la sessione prima di uscire.

--ssd aggiunge "storage_loc": 1 all'origine, come nel template GSV del firmware
custom (cfg/vid_customer.json): chiede di salvare gli originali sull'archivio
principale (SSD) invece che sulle SD dei moduli. Senza, la camera vuole le SD
nei moduli: senza schede risponde -310 "module no sdcard" (camera di test, 01/11).

Con --storage-loc N si prova un valore qualunque: sulla camera di test anche
storage_loc=1 è stato rifiutato con -310 (01/11), quindi il significato dei
valori è ancora da scoprire. --stato non registra: stampa lo stato della camera
(memorie elencate, save_path) e il percorso di salvataggio.

Uso:   python3 tools/titan_record.py 192.168.1.5 [--secondi 15] [--storage-loc N | --ssd] [--stato]
Prima chiudi l'app sul tablet: la camera accetta un solo client alla volta.
"""

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

START_BODY = {
    "name": "camera._startRecording",
    "parameters": {
        "audio": {"bitrate": 128, "channelLayout": "stereo", "mime": "aac",
                  "sampleFormat": "s16", "samplerate": 48000},
        "logMode": 0,
        "origin": {"bitDepth": 8, "bitrate": 180000, "framerate": 5, "height": 2972,
                   "logMode": 0, "mime": "h264", "saveOrigin": True, "width": 5280},
    },
}
CAM_BITS = {0: "registra", 3: "pronta", 6: "avvio registrazione",
            7: "chiusura file", 10: "foto", 13: "avvio camera"}


def stamp(t):
    """MMDDhhmmYYYY.ss, il formato dell'ora che vuole la camera."""
    return t.strftime("%m%d%H%M%Y.%S")


def zone():
    off = datetime.now().astimezone().utcoffset().total_seconds() // 60
    sign = "+" if off >= 0 else "-"
    off = abs(int(off))
    return "GMT%s%02d:%02d" % (sign, off // 60, off % 60)


def find_key(obj, key):
    """Il primo valore di `key` in un documento JSON annidato."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def bits(state):
    return ", ".join(name for b, name in CAM_BITS.items() if state >> b & 1) or "-"


class Titan:
    def __init__(self, host, port):
        self.base = f"http://{host}:{port}"
        self.fingerprint = ""
        self.cam_state = None
        self.last_ok = time.monotonic()
        self.stop_beat = threading.Event()
        self.lock = threading.Lock()

    def post(self, path, body, timeout):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Fingerprint", self.fingerprint)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def command(self, body, timeout=15):
        reply = self.post("/osc/commands/execute", body, timeout)
        if reply.get("state") != "done":
            err = reply.get("error") or reply
            raise RuntimeError(f"{body['name']} rifiutato: {json.dumps(err, ensure_ascii=False)}")
        return reply

    def connect(self):
        now = datetime.now()
        reply = self.command({"name": "camera._connect", "parameters": {
            "date_time": stamp(now),
            "hw_time": stamp(now.astimezone(timezone.utc)),
            "time_zone": zone()}})
        self.fingerprint = find_key(reply, "Fingerprint") or ""
        if not self.fingerprint:
            raise RuntimeError(f"nessun Fingerprint nella risposta: {reply}")
        return reply

    def heartbeat(self):
        """Un /osc/state al secondo, uno alla volta: è quello che tiene la sessione."""
        while not self.stop_beat.is_set():
            try:
                doc = self.post("/osc/state", {}, timeout=3)
                st = find_key(doc, "_cam_state")
                with self.lock:
                    self.last_ok = time.monotonic()
                    if st is not None and st != self.cam_state:
                        print(f"   [stato camera {st}: {bits(st)}]", flush=True)
                        self.cam_state = st
            except Exception as ex:  # una battuta persa non è la fine: la prossima riprova
                print(f"   [heartbeat fallito: {ex}]", flush=True)
            self.stop_beat.wait(1)

    def silence(self):
        with self.lock:
            return time.monotonic() - self.last_ok


def list_with_adb(path):
    if not path or not shutil.which("adb"):
        print(f"\nPer vedere i file:  adb shell 'ls -la {path or '/mnt/udisk1'}'")
        return
    print(f"\n== Contenuto di {path} (via adb)")
    out = subprocess.run(["adb", "shell", f"ls -la {path}; echo; du -sh {path}"],
                         capture_output=True, text=True, timeout=30)
    print((out.stdout + out.stderr).replace("\r", "").rstrip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Registra una ripresa di prova dalla Titan.")
    ap.add_argument("host", help="IP della camera, es. 192.168.1.5")
    ap.add_argument("--secondi", type=int, default=15, help="durata (default 15)")
    ap.add_argument("--porta", type=int, default=20000)
    ap.add_argument("--ssd", action="store_true", help="scorciatoia per --storage-loc 1")
    ap.add_argument("--storage-loc", type=int, default=None,
                    help="aggiunge storage_loc=N all'origine (il significato dei valori è da scoprire)")
    ap.add_argument("--stato", action="store_true",
                    help="non registra: stampa lo stato della camera e il percorso di salvataggio")
    args = ap.parse_args(argv)
    start_body = json.loads(json.dumps(START_BODY))
    loc = 1 if args.ssd and args.storage_loc is None else args.storage_loc
    if loc is not None:
        start_body["parameters"]["origin"]["storage_loc"] = loc

    cam = Titan(args.host, args.porta)
    print(f"== 1/5 Connessione a {cam.base}")
    # Una sessione rimasta aperta (per esempio da un giro precedente) si libera
    # da sola dopo 10 s di silenzio: riprova per 15 s prima di arrendersi.
    deadline = time.monotonic() + 15
    waiting = False
    while True:
        try:
            reply = cam.connect()
            break
        except urllib.error.URLError as ex:
            print(f"ERRORE: camera non raggiungibile: {ex}")
            return 1
        except RuntimeError as ex:
            if "another client" in str(ex) and time.monotonic() < deadline:
                if not waiting:
                    print("   camera occupata da un'altra sessione, riprovo per 15 s...", flush=True)
                    waiting = True
                time.sleep(2)
                continue
            print(f"ERRORE: {ex}\n(se l'app sul tablet è collegata, chiudila: la camera accetta un solo client)")
            return 1
    info = find_key(reply, "sys_info") or {}
    print(f"   sessione aperta. Seriale {info.get('sn', '?')}, firmware {find_key(reply, 'version') or '?'}")

    if args.stato:
        try:
            print("== Stato della camera (/osc/state)")
            print(json.dumps(cam.post("/osc/state", {}, timeout=5), indent=2, ensure_ascii=False))
            print("== Percorso di salvataggio (camera._getStoragePath)")
            try:
                print(json.dumps(cam.command({"name": "camera._getStoragePath"}, timeout=10),
                                 indent=2, ensure_ascii=False))
            except Exception as ex:
                print(f"   {ex}")
        finally:
            try:
                cam.command({"name": "camera._disconnect"}, timeout=5)
            except Exception:
                print("   (sessione non chiusa esplicitamente: si libera da sola entro 10 s)")
        return 0

    beat = threading.Thread(target=cam.heartbeat, daemon=True)
    beat.start()
    started = stopped = False
    record_url = None
    try:
        where = f"storage_loc={loc}" if loc is not None else "senza storage_loc, come l'app"
        print(f"== 2/5 Avvio registrazione ({args.secondi} s, {where})")
        try:
            reply = cam.command(start_body, timeout=15)
        except RuntimeError as ex:
            if "no sdcard" in str(ex) and loc is None:
                raise RuntimeError(f"{ex}\n   i moduli non hanno le SD: prova a indicare la destinazione con --storage-loc N")
            raise
        started = True
        record_url = find_key(reply, "_recordUrl")
        print(f"   la camera scrive in: {record_url or '(non indicato nella risposta)'}")

        print("== 3/5 Registrazione in corso")
        end = time.monotonic() + args.secondi
        while time.monotonic() < end:
            if cam.silence() > 8:
                print("ATTENZIONE: nessuna risposta allo stato da oltre 8 s, la sessione rischia di cadere")
            time.sleep(1)

        print("== 4/5 Stop (la camera chiude i file: di solito ~6 s, a volte di più)")
        reply = cam.command({"name": "camera._stopRecording"}, timeout=120)
        stopped = True
        record_url = record_url or find_key(reply, "_recordUrl")
        deadline = time.monotonic() + 120
        time.sleep(2)
        while time.monotonic() < deadline:
            with cam.lock:
                st = cam.cam_state
            if st is not None and not (st >> 7 & 1) and not (st & 1):
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrotto.")
    except Exception as ex:
        print(f"ERRORE: {ex}")
    finally:
        if started and not stopped:
            print("   fermo la registrazione prima di uscire...")
            try:
                cam.command({"name": "camera._stopRecording"}, timeout=120)
            except Exception as ex:
                print(f"   ATTENZIONE: stop non confermato ({ex}): controlla la camera")
        print("== 5/5 Chiudo la sessione")
        cam.stop_beat.set()
        beat.join(timeout=5)
        try:
            cam.command({"name": "camera._disconnect"}, timeout=5)
        except Exception:
            print("   (la camera non ha confermato la chiusura: la sessione si libera da sola entro 10 s)")

    list_with_adb(record_url)
    return 0 if stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
