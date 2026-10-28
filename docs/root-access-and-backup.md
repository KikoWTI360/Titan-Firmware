# Accesso root (UART/SSH) e strategia di backup anti-brick

La Titan è un **NVIDIA Jetson (Tegra, ARM64)** con Linux completo. L'obiettivo
è ottenere una shell root sul dispositivo e poi, **prima di toccare qualunque
cosa**, fare un backup completo e verificabile. Nessuna modifica va fatta su una
camera di cui non esiste un backup.

> Regola d'oro: **una sola unità "sacrificabile"** per tutta la fase di scoperta.
> La flotta si tocca solo con una procedura già validata e con backup in mano.

---

## Parte 1 — Ottenere root

Tre vie. **La A (adb) è la più comoda ed è confermata possibile** (il servizio
`adbd` è negli script di avvio, `init.rc`). La UART resta la rete di sicurezza
per il recupero da brick.

### Via A — adb (confermata: `adbd` è nei servizi di avvio)

Il firmware avvia `service adbd /usr/local/bin/adbd` (classe `base`, `critical`),
quindi la camera espone un'interfaccia adb su USB.

1. Installa adb sul Mac: `brew install android-platform-tools`.
2. Collega la camera (corpo principale) al Mac via USB e accendila.
3. Verifica accesso e privilegi:
   ```bash
   adb devices
   adb shell id          # se esce uid=0(root) abbiamo già root
   ```
4. Se `adb devices` non mostra nulla: controlla il cavo/porta, prova
   `adb kill-server && adb devices`, verifica che la camera non sia in una
   modalità USB diversa (c'è `camera._change_udisk_mode` per la memoria di
   massa; per adb serve la modalità normale). In ultima istanza, UART.

Recon iniziale sul device (sola lettura, sicuro):
```bash
adb shell 'mount; echo ---; cat /proc/mounts; echo ---; ls -la /mnt/udisk1 /mnt/udisk2 2>/dev/null; echo ---; cat /home/nvidia/insta360/etc/vid_customer.json; echo ---; ls -la /usr/local/bin/monitor'
```

### Via B — UART (console seriale) — rete di sicurezza anti-brick

La console seriale è quasi sempre esposta sui Jetson e dà accesso sia al
bootloader sia al login Linux. È **read anche quando la rete è giù** ed è la tua
rete di sicurezza per il recupero.

**Materiale:**
- Adattatore **USB↔UART a 3.3V** (es. CP2102/FTDI). **Mai 5V** sui pin del
  Tegra: rischi di friggere la SoC.
- 3 jumper (TX, RX, GND). Non collegare VCC.
- Terminale seriale: `minicom`, `picocom`, o `screen`.

**Procedura:**
1. A camera **spenta**, individua il connettore/pad di debug UART sulla scheda
   (header a 3–4 pin o test-pad vicino alla SoC). Documenta la posizione con una
   foto in `docs/` (aggiungi una foto o uno schizzo quando l'hai individuato).
2. Collega **incrociato**: adattatore TX → camera RX, adattatore RX → camera TX,
   GND → GND. VCC scollegato.
3. Apri il terminale a **115200 8N1** (impostazione tipica Tegra; se esce
   garbage prova 921600, poi altri baud):
   ```bash
   picocom -b 115200 /dev/ttyUSB0
   # oppure
   screen /dev/ttyUSB0 115200
   ```
4. Accendi la camera e guarda il boot log. Possibili esiti:
   - **Prompt di login Linux**: prova utenti tipici delle immagini Jetson
     (`nvidia` / `nvidia`, `root`, `ubuntu`). Se c'è un account applicativo
     Insta360, cerca indizi in `cfg/` (`default.prop`, script `.sh`).
   - **Shell già root** (alcune build danno console root diretta).
   - **Solo bootloader (U-Boot/CBoot)**: puoi interrompere il boot e, se non
     bloccato, aggiungere `init=/bin/sh` o `single` ai boot args per entrare in
     single-user come root. Questa è anche la base per il recupero da brick.
5. Una volta dentro, verifica: `id`, `uname -a`, `cat /etc/os-release`,
   `mount` (quali partizioni sono RW/RO), `ls -la /usr/local/bin`,
   `cat /home/nvidia/insta360/etc/default.prop`.

### Via C — SSH (se la Jetson lo espone)

Più comodo per il lavoro quotidiano, ma dipende dal fatto che il firmware abbia
un server SSH attivo e una via di rete.

1. **Rete:** molti Jetson espongono un gadget USB (RNDIS) con IP fisso: collega
   la camera via USB al PC e cerca un'interfaccia nuova (tipicamente la camera a
   `192.168.55.1`). In alternativa via Ethernet/Wi-Fi se la camera ne ha.
2. Scopri servizi:
   ```bash
   ip addr                      # trova la subnet del gadget USB
   nmap -p 22,23 192.168.55.1   # SSH/telnet aperti?
   ```
3. Se SSH è aperto: `ssh nvidia@192.168.55.1` (prova le credenziali tipiche).
   Se non conosci la password, **usa prima la UART** per leggerla/azzerarla
   (`passwd`) o per abilitare la tua chiave in `~/.ssh/authorized_keys`.
4. Se trovi telnet (porta 23) invece di SSH, è comunque una via di root — ma
   configura SSH appena dentro e disattiva telnet dopo.

> Se né adb (A) né SSH (C) rispondono: la UART (B) resta la strada maestra. Un
> ultimo fallback è lo smontaggio dell'eMMC/SD per leggerla da fuori, ma è
> invasivo — tienilo come extrema ratio.

---

## Parte 2 — Backup completo (rete anti-brick) PRIMA di qualsiasi modifica

Due livelli di backup. Fai **entrambi** sulla unità sacrificabile; per la flotta
basta il livello "file" se la procedura è già validata.

Tieni tutti i backup in `device-backups/` (è **gitignored**: sono pesanti e
contengono binari proprietari — non vanno su GitHub).

### Livello 1 — Dump a blocchi delle partizioni (recupero totale)

È il backup che ti salva da un brick: ti permette di riscrivere tutto com'era.

1. Elenca i dispositivi a blocchi e lo schema partizioni:
   ```bash
   lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
   cat /proc/partitions
   ls -l /dev/mmcblk*           # eMMC
   ```
2. Copia **ogni** partizione in un file, calcolando subito l'hash. Esempio per
   una eMMC `mmcblk0` con partizioni `mmcblk0p1..pN`:
   ```bash
   mkdir -p /mnt/usb/titan-backup   # monta prima una chiavetta/SSD con spazio
   for p in /dev/mmcblk0p*; do
     name=$(basename "$p")
     dd if="$p" of="/mnt/usb/titan-backup/${name}.img" bs=4M conv=noerror,sync status=progress
     md5sum "/mnt/usb/titan-backup/${name}.img" >> /mnt/usb/titan-backup/MD5SUMS.txt
   done
   # (opzionale, se c'è spazio) dump dell'intero device:
   # dd if=/dev/mmcblk0 of=/mnt/usb/titan-backup/mmcblk0.full.img bs=4M status=progress
   ```
   > Scrivi i dump su **storage esterno** (USB/SSD), non sulla eMMC della camera.
   > Fai il dump da single-user o con le partizioni smontate/RO quando possibile,
   > per avere immagini consistenti.
3. Salva anche lo **schema** (ti serve per ricostruire): output di
   `lsblk`, `cat /proc/partitions`, e la tabella GPT se presente
   (`sgdisk --backup=gpt.bin /dev/mmcblk0` o `sfdisk -d /dev/mmcblk0 > ptable.txt`).

### Livello 2 — Backup a file dello strato applicativo (ciò che modifichi)

Più leggero e mirato a quello che effettivamente cambi. Dalla mappa `bill.list`:

```bash
cd /
tar czf /mnt/usb/titan-backup/applayer-$(date +%Y%m%d).tar.gz \
    usr/local/bin \
    usr/local/lib \
    usr/local/lib/firmware \
    home/nvidia/insta360
md5sum /mnt/usb/titan-backup/applayer-*.tar.gz >> /mnt/usb/titan-backup/MD5SUMS.txt
```

Per ogni singolo file che stai per modificare, tieni anche una copia 1:1:
```bash
cp -a /home/nvidia/insta360/etc/takevideo_template.json \
      /mnt/usb/titan-backup/takevideo_template.json.orig
```

### Verifica del backup (non è un backup finché non l'hai verificato)

- Controlla gli hash: `md5sum -c /mnt/usb/titan-backup/MD5SUMS.txt`.
- Monta in loopback un paio di immagini per confermare che siano leggibili:
  ```bash
  sudo losetup -fP /mnt/usb/titan-backup/mmcblk0p1.img
  lsblk; sudo mount /dev/loopXpY /mnt/check; ls /mnt/check; sudo umount /mnt/check
  ```
- Copia i backup in **almeno due posti** (es. SSD locale + Drive/NAS).

---

## Parte 3 — Modifica sicura e rollback

1. **Un cambiamento alla volta**, minimo possibile.
2. Prima di sovrascrivere un file sul device, fai la copia `.orig` (sopra).
3. Rimonta RW solo se serve (`mount -o remount,rw /`), e rimetti RO dopo se
   la partizione era RO.
4. Applica, **riavvia il servizio** interessato (o la camera) e raccogli i log
   (`dmesg`, i log del web server, l'output di `camerad`).
5. **Test funzionale reale**: una ripresa/foto/live effettiva, non solo "si
   accende". Verifica il file prodotto (bitrate/codec reali con `ffprobe`).
6. **Rollback** se qualcosa va storto:
   - file singolo: ripristina il `.orig`;
   - problema serio: riscrivi la partizione dal dump di Livello 1
     (`dd if=backup.img of=/dev/mmcblk0pX`);
   - se non boota: entra da **UART** in single-user / bootloader e ripristina.
7. Annota tutto in `mods/<mod>/README.md`: cosa, dove, come applicato, esito,
   come fare rollback.

---

## Riepilogo ordine operativo

```
UART pronta  ──►  root ottenuto  ──►  BACKUP L1 (partizioni) + L2 (file) ──►
verifica backup  ──►  1 mod minima  ──►  test reale  ──►  ok? flotta : rollback
```

Finché non hai **root + backup verificato** su una unità sacrificabile, non si
modifica nulla.
