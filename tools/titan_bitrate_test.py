#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
titan_bitrate_test.py — prova di bitrate su SSD, da lanciare ALL'APERTO.

Le misure del 01/11 erano in una stanza in penombra: la scena decide quanti bit
chiede il codificatore, quindi la prova vera va fatta fuori, con luce, dettaglio
e movimento (idealmente in auto, alla velocità di ripresa normale).

Cosa fa, da solo:
  1. Controlli: adb collegato come root, elf_probe.py caricato sulla camera,
     memoria principale = SSD (/mnt/udisk1), spazio libero sufficiente.
  2. Per ogni tetto di bitrate (default 180, 140, 100, 60, 30 Mbit/s per lente)
     registra N secondi con tools/titan_record.py --ssd (storage_loc 0, file a
     piena risoluzione sull'SSD). Con --giri N ripete la serie N volte, così
     ogni tetto vede scene diverse.
  3. Dopo ogni ripresa analizza gli 8 origin_N.mp4 SULLA CAMERA (niente
     copie): fotogrammi, salti (fotogrammi persi), Mbit/s reali per lente,
     quante lenti sono al tetto, dati ricevuti dalla rete interna (da
     titan_record), temperatura massima della camera.
  4. Alla fine: tabella riassuntiva, richiesta "naturale" di ogni lente,
     raccomandazione (il tetto più alto senza fotogrammi persi) e autonomia
     dell'SSD. Salva un rapporto .txt e i dati grezzi .json in prove-bitrate/.

Prima di partire:
  - SSD collegato alla camera, SD0 TOLTA (così la memoria principale è l'SSD);
    le SD dei moduli non servono.
  - Chiudi l'app sul tablet: la camera accetta un solo client alla volta.
  - Il Mac deve raggiungere la camera in rete (stesso Wi-Fi/Ethernet).
  - Ctrl-C ferma la registrazione in corso, chiude la sessione e salva il
    rapporto parziale.

Le riprese di prova RESTANO sull'SSD (cartelle VID_... elencate nel rapporto),
così puoi confrontare a occhio la qualità; cancellale tu quando hai finito.

Uso:
    python3 tools/titan_bitrate_test.py 192.168.1.5
    python3 tools/titan_bitrate_test.py 192.168.1.5 --giri 2 --secondi 20
    python3 tools/titan_bitrate_test.py 192.168.1.5 --tetti 180,120,100
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RECORD = os.path.join(HERE, "titan_record.py")
PROBE = os.path.join(HERE, "elf_probe.py")
CAM_PYTHON = "/usr/local/bin/python3.6"
CAM_PROBE = "/tmp/elf_probe.py"
FPS = 5
LENSES = 8
AT_CAP = 0.9          # una lente è "al tetto" se usa almeno il 90% del tetto


def adb(*args, timeout=60):
    """(codice di uscita, output senza \\r). adb shell gira in un PTY: \\n -> \\r\\n."""
    try:
        p = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "adb non trovato"
    except subprocess.TimeoutExpired:
        return 124, "adb: tempo scaduto"
    return p.returncode, (p.stdout + p.stderr).replace("\r", "")


def die(msg):
    print(f"\nSTOP: {msg}")
    sys.exit(1)


def preflight(host, port, needed_mb):
    print("== Controlli")
    code, out = adb("connect", f"{host}:5555", timeout=20)
    if code != 0 or ("connected" not in out and "already" not in out):
        die(f"adb non si collega a {host}:5555 ({out.strip()})")
    # Tutte le chiamate adb, anche quelle di titan_record.py, vanno a questa camera.
    os.environ["ANDROID_SERIAL"] = f"{host}:5555"
    code, out = adb("shell", "id")
    if "uid=0" not in out:
        die(f"adb non è root sulla camera: {out.strip()}")
    print(f"   adb: {host}:5555 come root")
    code, out = adb("push", PROBE, CAM_PROBE)
    if code != 0:
        die(f"non riesco a caricare elf_probe.py sulla camera: {out.strip()}")
    print(f"   elf_probe.py caricato in {CAM_PROBE}")

    p = subprocess.run([sys.executable, RECORD, host, "--porta", str(port), "--stato"],
                       capture_output=True, text=True, timeout=90)
    m = re.search(r'"save_path":\s*"([^"]+)"', p.stdout)
    if not m:
        die("non riesco a leggere lo stato della camera (app del tablet chiusa?):\n"
            + (p.stdout + p.stderr)[-800:])
    save_path = m.group(1)
    if "udisk" not in save_path:
        die(f"la memoria principale è {save_path}, non l'SSD. Togli la SD0 a camera "
            "accesa (passa da sola a /mnt/udisk1) e rilancia.")
    print(f"   memoria principale: {save_path}")

    code, out = adb("shell", f"df -Pm {save_path}")
    free = None
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[3].isdigit():
            free = int(f[3])
    if free is None:
        die(f"non riesco a leggere lo spazio libero di {save_path}: {out.strip()}")
    print(f"   spazio libero sull'SSD: {free / 1024:.0f} GiB (servono al massimo ~{needed_mb / 1024:.0f} GiB)")
    if free < needed_mb * 1.2:
        die("spazio insufficiente sull'SSD per la serie di prove")
    return save_path, free


def camera_temp():
    """La temperatura più alta tra i sensori della camera, in °C (o None)."""
    code, out = adb("shell", "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null", timeout=20)
    vals = [int(x) for x in out.split() if x.lstrip("-").isdigit()]
    vals = [v / 1000 if v > 1000 else v for v in vals if -40000 < v < 200000]
    return max(vals) if vals else None


def record(host, port, cap_kbps, seconds):
    """Lancia titan_record.py e ne mostra l'output. -> (cartella, righe rete, ok)."""
    cmd = [sys.executable, RECORD, host, "--porta", str(port), "--ssd",
           "--bitrate", str(cap_kbps), "--secondi", str(seconds)]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)
    lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            print(f"   | {line}", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        # Ctrl-C arriva anche a titan_record.py, che ferma la registrazione e
        # chiude la sessione: aspettiamo che finisca prima di uscire.
        print("   (Ctrl-C: aspetto che titan_record.py fermi la registrazione...)")
        try:
            for line in proc.stdout:
                print(f"   | {line.rstrip()}")
            proc.wait(timeout=180)
        except Exception:
            pass
        raise
    text = "\n".join(lines)
    m = re.search(r"la camera scrive in: (/\S+)", text)
    folder = m.group(1) if m else None
    net = []
    for m in re.finditer(r"^\s+(\S+)\s+([\d.]+) MB\s+~([\d.]+) MB/s in \d+ s"
                         r"(?:, (\d+)% del collegamento da (\d+) Mb/s)?", text, re.M):
        net.append({"iface": m.group(1), "mb": float(m.group(2)), "mbps": float(m.group(3)),
                    "load_pct": int(m.group(4)) if m.group(4) else None,
                    "link_mbit": int(m.group(5)) if m.group(5) else None})
    usb = [l.split("[usb] ", 1)[1] for l in lines if "[usb] " in l]
    return folder, net, usb, proc.returncode == 0 and folder is not None


def usb_devices(usb_lines):
    """Dispositivi USB (esclusi hub e root hub) visti durante la ripresa: {(classe, velocità): n}."""
    found = {}
    for line in usb_lines:
        m = re.search(r"Class=([^,]+),.*?(\d+M)\s*$", line)
        if m and m.group(1) not in ("root_hub", "Hub"):
            key = (m.group(1), m.group(2))
            found[key] = found.get(key, 0) + 1
    return found


def analyze(folder):
    files = " ".join(f"{folder}/origin_{n}.mp4" for n in range(1, LENSES + 1))
    code, out = adb("shell", f"{CAM_PYTHON} {CAM_PROBE} mp4json {files}", timeout=180)
    lenses = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                lenses.append(json.loads(line))
            except ValueError:
                pass
    return lenses


def summarize_run(cap_kbps, lenses):
    """Aggiunge a ogni lente i Mbit/s reali e calcola i numeri della ripresa."""
    cap = cap_kbps / 1000
    ok_frames = [l["frames"] for l in lenses if "frames" in l]
    fmax = max(ok_frames) if ok_frames else 0
    for l in lenses:
        if "frames" in l and l["frames"]:
            secs = l["frames"] / FPS          # tempo vero: fotogrammi / 5 fps
            l["mbps"] = l["video_bytes"] * 8 / secs / 1e6
            l["at_cap"] = l["mbps"] >= AT_CAP * cap
    errors = [l for l in lenses if "error" in l or "frames" not in l]
    missing_files = LENSES - len(lenses)
    # Un fotogramma perso si vede come salto nei tempi E come fotogramma in meno
    # rispetto alle altre lenti: per lente si prende il maggiore dei due, non la somma.
    for l in lenses:
        if "frames" in l:
            l["lost"] = max(l.get("missing", 0), fmax - l["frames"])
    lost = sum(l.get("lost", 0) for l in lenses)
    total_bytes = sum(l.get("video_bytes", 0) for l in lenses)
    run = {
        "cap": cap,
        "frames_max": fmax,
        "frames_min": min(ok_frames) if ok_frames else 0,
        "lost_frames": lost,
        "errors": len(errors) + missing_files,
        "at_cap": sum(1 for l in lenses if l.get("at_cap")),
        "max_mbps": max((l.get("mbps", 0) for l in lenses), default=0),
        "total_MBps": total_bytes / (fmax / FPS) / 1e6 if fmax else 0,
        "last_ms_max": max((l.get("last_ms", 0) for l in lenses), default=0),
    }
    run["clean"] = run["lost_frames"] == 0 and run["errors"] == 0
    return run


def print_run(run, lenses, net, temp):
    print(f"\n   Analisi (tetto {run['cap']:.0f} Mbit/s):")
    print("   lente  Mbit/s  fotogr.  persi  chiave KB  altri KB  ultimo ms")
    for i, l in enumerate(lenses, 1):
        if "frames" not in l:
            print(f"   L{i}     ERRORE: {l.get('error', '?')}")
            continue
        flag = " al tetto" if l.get("at_cap") else ""
        ka = f"{l['key_avg'] / 1024:9.0f}" if l.get("key_avg") else "        -"
        oa = f"{l['other_avg'] / 1024:9.0f}" if l.get("other_avg") else "        -"
        print(f"   L{i}    {l['mbps']:6.1f}  {l['frames']:7d}  {l.get('lost', 0):5d}  {ka}  {oa}"
              f"  {l.get('last_ms', 0):9.0f}{flag}")
    state = "OK, nessun fotogramma perso" if run["clean"] else \
        f"ATTENZIONE: {run['lost_frames']} fotogrammi persi, {run['errors']} file con errori"
    print(f"   -> {state}; {run['at_cap']} lenti al tetto; totale ~{run['total_MBps']:.0f} MB/s")
    for n in net:
        load = f", {n['load_pct']}% di {n['link_mbit']} Mb/s" if n.get("load_pct") is not None else ""
        print(f"   -> rete {n['iface']}: ~{n['mbps']:.0f} MB/s{load}")
    devs = usb_devices(run.get("usb", []))
    if devs:
        desc = ", ".join(f"{n} x {cls} a {speed}" for (cls, speed), n in sorted(devs.items()))
        print(f"   -> USB durante la ripresa: {desc}")
    if temp is not None:
        print(f"   -> temperatura massima camera: {temp:.0f} °C")


def report(runs, free_mb, args):
    out = []
    w = out.append
    w("== Riepilogo")
    w(f"   {'tetto':>6}  {'riprese':>7}  {'persi':>5}  {'al tetto':>8}  {'lente max':>9}  "
      f"{'totale':>8}  {'rete':>12}  {'temp':>5}")
    caps = sorted({r["cap"] for r in runs}, reverse=True)
    for cap in caps:
        rs = [r for r in runs if r["cap"] == cap]
        lost = sum(r["lost_frames"] + r["errors"] for r in rs)
        atc = max(r["at_cap"] for r in rs)
        mx = max(r["max_mbps"] for r in rs)
        tot = max(r["total_MBps"] for r in rs)
        loads = [n["load_pct"] for r in rs for n in r["net"] if n.get("load_pct") is not None]
        net = f"{max(loads)}% link" if loads else "-"
        temps = [r["temp"] for r in rs if r.get("temp") is not None]
        temp = f"{max(temps):.0f}°C" if temps else "-"
        w(f"   {cap:>6.0f}  {len(rs):>7}  {lost:>5}  {atc:>6}/8  {mx:>7.0f} M  {tot:>5.0f} MB/s  {net:>12}  {temp:>5}")

    # Richiesta naturale di ogni lente: dalle riprese pulite al tetto più alto.
    clean_caps = [c for c in caps if all(r["clean"] for r in runs if r["cap"] == c)]
    w("")
    if clean_caps:
        top = clean_caps[0]
        w(f"== Richiesta di ogni lente (riprese pulite a tetto {top:.0f})")
        for i in range(LENSES):
            vals = [r["lenses"][i] for r in runs if r["cap"] == top and i < len(r["lenses"])]
            mb = [v["mbps"] for v in vals if "mbps" in v]
            if not mb:
                continue
            capped = any(v.get("at_cap") for v in vals)
            w(f"   L{i + 1}: max {max(mb):6.1f} Mbit/s" + ("  (al tetto: ne vorrebbe di più)" if capped else ""))
    w("")
    w("== Raccomandazione")
    if not runs:
        w("   Nessuna ripresa completata.")
    elif not clean_caps:
        w("   Tutti i tetti provati hanno perso fotogrammi o dato errori: prova tetti più bassi")
        w("   (es. --tetti 80,60,40) e controlla il rapporto lente per lente.")
    else:
        best = clean_caps[0]
        rs = [r for r in runs if r["cap"] == best]
        atc = max(r["at_cap"] for r in rs)
        tot = max(r["total_MBps"] for r in rs)
        higher_bad = [c for c in caps if c > best]
        w(f"   Tetto consigliato: {best:.0f} Mbit/s per lente  ->  --bitrate {int(best * 1000)}")
        if higher_bad:
            w(f"   (con {', '.join(f'{c:.0f}' for c in higher_bad)} si perdono fotogrammi: il collegamento non regge)")
        if atc == 0:
            w("   A questo tetto nessuna lente è limitata: qualità piena, come sulle SD dei moduli.")
        elif higher_bad:
            w(f"   A questo tetto {atc} lenti su 8 sono limitate: è il massimo che il trasferimento regge.")
        else:
            w(f"   A questo tetto {atc} lenti su 8 sono ancora limitate: si potrebbe provare un tetto più alto.")
        if tot:
            hours = free_mb * 1048576 / (tot * 1e6) / 3600
            w(f"   Totale misurato fino a ~{tot:.0f} MB/s: autonomia SSD ~{hours:.1f} h "
              f"con {free_mb / 1024:.0f} GiB liberi.")
        w("   Prima di decidere guarda anche a occhio le riprese (cartelle qui sotto).")
    w("")
    w("== USB durante le riprese (lsusb -t a metà registrazione)")
    seen = {}
    for r in runs:
        for k, n in usb_devices(r.get("usb", [])).items():
            seen[k] = max(seen.get(k, 0), n)
    if seen:
        for (cls, speed), n in sorted(seen.items()):
            w(f"   {n} x {cls} a {speed}")
        w("   (a camera ferma c'era solo l'SSD: Mass Storage a 5000M)")
    else:
        w("   nessun dato")
    w("")
    w("== Cartelle delle riprese di prova sull'SSD")
    for r in runs:
        w(f"   tetto {r['cap']:>4.0f}  giro {r['round']}  {r['folder']}" + ("" if r["clean"] else "  (fotogrammi persi)"))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", help="IP della camera, es. 192.168.1.5")
    ap.add_argument("--porta", type=int, default=20000)
    ap.add_argument("--tetti", default="180,140,100,60,30",
                    help="tetti da provare in Mbit/s per lente, separati da virgola (default 180,140,100,60,30)")
    ap.add_argument("--secondi", type=int, default=20, help="durata di ogni ripresa (default 20)")
    ap.add_argument("--giri", type=int, default=1, help="quante volte ripetere la serie (default 1)")
    ap.add_argument("--pausa", type=int, default=5, help="secondi di pausa tra le riprese (default 5)")
    ap.add_argument("--uscita", default="prove-bitrate", help="cartella del rapporto (default prove-bitrate)")
    args = ap.parse_args()

    try:
        caps = [float(x) for x in args.tetti.split(",") if x.strip()]
    except ValueError:
        die(f"--tetti non valido: {args.tetti}")
    needed_mb = sum(c * LENSES * args.secondi / 8 for c in caps) * args.giri  # caso peggiore
    save_path, free_mb = preflight(args.host, args.porta, needed_mb)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(args.uscita, exist_ok=True)
    base = os.path.join(args.uscita, f"titan-bitrate-{stamp}")
    total = len(caps) * args.giri
    print(f"\n{total} riprese da {args.secondi} s (tetti {', '.join(f'{c:.0f}' for c in caps)} Mbit/s"
          f", {args.giri} giro/i): circa {total * (args.secondi + 30) / 60:.0f} minuti.")
    print("Muoviti come in una ripresa vera. Ctrl-C ferma tutto e salva il rapporto parziale.\n")

    runs = []
    try:
        k = 0
        for rnd in range(1, args.giri + 1):
            for cap in caps:
                k += 1
                print(f"== Ripresa {k}/{total}: tetto {cap:.0f} Mbit/s per lente (giro {rnd})")
                folder, net, usb, ok = record(args.host, args.porta, int(cap * 1000), args.secondi)
                if not ok:
                    print("   ripresa non riuscita: passo alla successiva")
                    time.sleep(args.pausa + 10)
                    continue
                lenses = analyze(folder)
                run = summarize_run(cap * 1000, lenses)
                run.update({"round": rnd, "folder": folder, "lenses": lenses, "net": net, "usb": usb,
                            "temp": camera_temp(), "time": datetime.now().isoformat(timespec="seconds")})
                runs.append(run)
                print_run(run, lenses, net, run["temp"])
                print()
                time.sleep(args.pausa)
    except KeyboardInterrupt:
        print("\nInterrotto: salvo il rapporto con le riprese completate.")

    _, out = adb("shell", f"df -Pm {save_path}")
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[3].isdigit():
            free_mb = int(f[3])
    text = report(runs, free_mb, args)
    print("\n" + text)
    header = (f"Prova di bitrate Titan su SSD, {stamp}\ncamera {args.host}, {args.secondi} s per ripresa, "
              f"tetti {args.tetti}, giri {args.giri}\n\n")
    with open(base + ".txt", "w") as f:
        f.write(header + text + "\n")
    with open(base + ".json", "w") as f:
        json.dump({"args": vars(args), "save_path": save_path, "runs": runs}, f, indent=1)
    print(f"\nRapporto salvato: {base}.txt  (dati grezzi: {base}.json)")
    return 0 if runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
