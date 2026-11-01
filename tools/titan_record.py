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

--ssd = "storage_loc": 0 a 30000 kbit/s, esattamente come l'APK dedicato
(registro della camera, 01/11). Con questi valori la camera salva gli 8
origin_N.mp4 a piena risoluzione + pro.prj sulla MEMORIA PRINCIPALE (SD0 o SSD,
quella scelta nel menu) invece che sulle SD dei moduli: verificato il 01/11,
cartella VID_20261101_195955 sulla SD0 con origin_1..8 (50-57 MB ciascuno, ~15 s).
Perché vada sull'SSD, l'SSD deve essere la memoria principale: controlla con
--stato che save_path sia /mnt/udisk1.

Valori di storage_loc visti finora (camera di test, firmware v1.2.23, 01/11):
  assente  origini sulle SD dei moduli, lrv/preview/pro.prj sulla principale
  0        origini + pro.prj sulla memoria principale (APK dedicato, 30000 kbit/s)
  1        moduli 0 MB, principale ~31 MB: la piena risoluzione va persa
Senza SD nei moduli, senza storage_loc la camera risponde -310 "module no
sdcard"; con storage_loc 0 è ancora da provare.

Con adb misura anche i byte ricevuti da ogni interfaccia di rete della camera
durante la registrazione: i moduli non sono sull'USB (lsusb -t, 01/11), quindi i
flussi delle lenti arrivano da un'altra strada, probabilmente una rete interna.

--storage-loc N prova un valore qualunque. --bitrate N cambia il bitrate
dell'origine (kbit/s); con --ssd il default è 30000 come l'APK, altrimenti
180000 come l'app Insta360. --stato non registra: stampa lo stato della camera
(memorie elencate, save_path) e il percorso di salvataggio.

Uso:   python3 tools/titan_record.py 192.168.1.5 [--secondi 15] [--storage-loc N | --ssd] [--bitrate N] [--stato]
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
        self.last_doc = {}
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
                    self.last_doc = doc
                    if st is not None and st != self.cam_state:
                        print(f"   [stato camera {st}: {bits(st)}]", flush=True)
                        self.cam_state = st
            except Exception as ex:  # una battuta persa non è la fine: la prossima riprova
                print(f"   [heartbeat fallito: {ex}]", flush=True)
            self.stop_beat.wait(1)

    def silence(self):
        with self.lock:
            return time.monotonic() - self.last_ok


def card_space(doc):
    """{nome: (tipo, MiB liberi)} per le memorie che la camera elenca in _external_dev."""
    dev = find_key(doc, "_external_dev") or {}
    return {e["name"]: (e.get("mounttype", "?"), e.get("free"))
            for e in dev.get("entries") or [] if isinstance(e, dict) and "name" in e}


def disk_used_adb():
    """{punto di mount: MiB usati} per SSD e SD0, letti con adb (vuoto senza adb)."""
    if not shutil.which("adb"):
        return {}
    try:
        out = subprocess.run(["adb", "shell", "df -Pm /mnt/udisk1 /mnt/SD0 2>/dev/null"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return {}
    used = {}
    for line in out.replace("\r", "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[2].isdigit():
            used[f[5]] = int(f[2])
    return used


def net_rx_adb():
    """{interfaccia: (velocità Mb/s o None, byte ricevuti)} dalla camera, via adb.

    Serve a vedere da dove arrivano i flussi delle lenti: i moduli non sono
    sull'USB (lsusb -t, 01/11), probabilmente sono su una rete interna.
    """
    if not shutil.which("adb"):
        return {}
    cmd = ('for i in /sys/class/net/*; do '
           'echo "${i##*/} $(cat $i/speed 2>/dev/null || echo -) $(cat $i/statistics/rx_bytes)"; done')
    try:
        out = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return {}
    rx = {}
    for line in out.replace("\r", "").splitlines():
        f = line.split()
        if len(f) == 3 and f[2].isdigit():
            speed = int(f[1]) if f[1].lstrip("-").isdigit() and int(f[1]) > 0 else None
            rx[f[0]] = (speed, int(f[2]))
    return rx


def net_report(before, after, seconds):
    rows = []
    for name in sorted(set(before) & set(after)):
        delta = after[name][1] - before[name][1]
        if delta >= 1_000_000:
            rows.append((name, after[name][0], delta))
    if not rows:
        return
    print("\n== Dati ricevuti dalle interfacce di rete della camera (via adb)")
    for name, speed, delta in rows:
        rate = delta / seconds / 1e6 if seconds else 0
        load = f", {rate * 8 / speed * 100:.0f}% del collegamento da {speed} Mb/s" if speed else ""
        print(f"   {name:<10} {delta / 1e6:>9.1f} MB   ~{rate:.1f} MB/s in {seconds} s{load}")

def space_report(cards_before, cards_after, disk_before, disk_after):
    print("\n== Dove ha scritto la camera (spazio prima → dopo)")
    for name in sorted(set(cards_before) | set(cards_after)):
        kind, b = cards_before.get(name, ("?", None))
        a = cards_after.get(name, (kind, None))[1]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            print(f"   {name:<5} ({kind:<6}) libero {b:>10.1f} → {a:>10.1f} MiB   scritti {b - a:>8.1f} MiB")
        else:
            print(f"   {name:<5} ({kind:<6}) libero {'?' if b is None else b} → {'?' if a is None else a}")
    for mnt in sorted(set(disk_before) | set(disk_after)):
        b, a = disk_before.get(mnt), disk_after.get(mnt)
        if b is not None and a is not None:
            print(f"   {mnt:<12} (adb)      usato  {b:>10} → {a:>10} MiB   scritti {a - b:>8} MiB")
    if not (cards_before or disk_before):
        print("   (la camera non ha riportato lo spazio delle memorie e adb non è disponibile)")


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
    ap.add_argument("--ssd", action="store_true", help="piena risoluzione sulla memoria principale: storage_loc 0 a 30000 kbit/s, come l'APK dedicato")
    ap.add_argument("--storage-loc", type=int, default=None,
                    help="aggiunge storage_loc=N all'origine (0 = origini sulla memoria principale, 1 = origini perse)")
    ap.add_argument("--bitrate", type=int, default=None,
                    help="bitrate dell'origine in kbit/s (default 180000, come l'app Insta360; l'APK dedicato usa 30000)")
    ap.add_argument("--stato", action="store_true",
                    help="non registra: stampa lo stato della camera e il percorso di salvataggio")
    args = ap.parse_args(argv)
    start_body = json.loads(json.dumps(START_BODY))
    if args.ssd and args.bitrate is None:
        args.bitrate = 30000
    if args.bitrate is not None:
        start_body["parameters"]["origin"]["bitrate"] = args.bitrate
    loc = 0 if args.ssd and args.storage_loc is None else args.storage_loc
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
    # c_v = firmware dei moduli come lo riporta _indSyncState nel registro della camera
    modules = find_key(reply, "c_v") or find_key(reply, "moduleVersion")
    print(f"   sessione aperta. Seriale {info.get('sn', '?')}, firmware {find_key(reply, 'version') or '?'}"
          + (f", moduli {modules}" if modules else ""))

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

    # Spazio di ogni memoria prima della registrazione, per vedere dopo dove ha scritto.
    try:
        state_before = cam.post("/osc/state", {}, timeout=5)
    except Exception:
        state_before = {}
    cards_before = card_space(state_before)
    save_path = (find_key(state_before, "_external_dev") or {}).get("save_path")
    if save_path:
        print(f"   memoria principale (save_path): {save_path}")
        if args.ssd and "udisk" not in save_path:
            print("   ATTENZIONE: la memoria principale non è l'SSD, gli origin_N.mp4 andranno in "
                  f"{save_path}.\n   Per l'SSD: togli la SD0 (la camera passa da sola a /mnt/udisk1) e ricontrolla con --stato.")
    disk_before = disk_used_adb()
    net_before = net_rx_adb()
    cards_after, disk_after, net_after = {}, {}, {}

    beat = threading.Thread(target=cam.heartbeat, daemon=True)
    beat.start()
    started = stopped = False
    record_url = None
    try:
        where = f"storage_loc={loc}" if loc is not None else "senza storage_loc, come l'app"
        where += f", bitrate {start_body['parameters']['origin']['bitrate']} kbit/s"
        print(f"== 2/5 Avvio registrazione ({args.secondi} s, {where})")
        try:
            reply = cam.command(start_body, timeout=15)
        except RuntimeError as ex:
            if "no sdcard" in str(ex) and loc is None:
                raise RuntimeError(f"{ex}\n   i moduli non hanno le SD: prova --ssd (storage_loc 0, origini sulla memoria principale)")
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
        time.sleep(3)  # la camera aggiorna lo spazio libero dopo aver chiuso i file
        with cam.lock:
            cards_after = card_space(cam.last_doc)
        disk_after = disk_used_adb()
        net_after = net_rx_adb()
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

    if stopped:
        space_report(cards_before, cards_after, disk_before, disk_after)
        net_report(net_before, net_after, args.secondi)
    list_with_adb(record_url)
    return 0 if stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
