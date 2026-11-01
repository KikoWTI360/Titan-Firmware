# Ricognizione della camera di test (01/11)

Prima ricognizione con accesso root via adb in rete, sulla camera di test dopo il
flash del firmware custom v1.2.23. Solo letture, nessuna modifica.

## Accesso

- In rete la camera risponde su **5555 (adb)** e **22 (SSH)**; 23 (telnet) chiusa.
  Via USB adb non la vede. `adb connect <IP>:5555` → `uid=0(root)`.
- IP sulla rete dell'ufficio: `192.168.1.5` (DHCP, può cambiare). Si trova
  cercando la porta OSC 20000 (vedi `root-access-and-backup.md`).
- `/osc/info` senza sessione risponde `disabledCommand / camera not connected`:
  è normale, serve `camera._connect`.
- Sulla console seriale `ttyS0` c'è un **login root automatico**
  (`/bin/login -f`): via UART si ha subito una shell root.

## Sistema

- Linux `4.4.38` aarch64 (L4T, Ubuntu), build del 20/07/2019. 8 GB di RAM.
- `ro.version=V0.2.0` in `/home/nvidia/insta360/etc/default.prop`: numero
  interno, non la versione commerciale.
- **Il firmware custom è installato**: i binari in `/usr/local/bin` hanno le
  stesse dimensioni di quelli del pacchetto v1.2.23 (`camerad` 12.329.064 byte,
  `system_server` 7.883.432, `crtmpserver`, `module_tool`, `msgclient`,
  `upgrade`, `bootanimation`, `nginx`, `update_check`).

## Memoria

| Dispositivo | Dimensione | Cosa |
|---|---|---|
| `mmcblk0` | 29,1 GiB | eMMC interna |
| `mmcblk0p1` | 28 GiB | sistema (`/`, ext4, 13 GB usati) |
| `mmcblk0p2`…`p29` | da 512 KB a 400 MB | partizioni di avvio NVIDIA (bootloader, kernel, DTB, ...) |
| `mmcblk0boot0`, `boot1` | 4 MB ciascuna | primo stadio di avvio |
| `mmcblk0rpmb` | 4 MB | area protetta, non si legge |
| `sda1` | 119 GB, exFAT | `/mnt/SD0`: la scheda **SD0** della camera (slot della scheda madre, dietro un lettore USB interno), usata anche per caricare il firmware |

Durante la prima ricognizione **l'SSD non era collegato** (nessun `/mnt/udisk*`).

### SSD esterno

- SanDisk Extreme SSD 1 TB, exFAT: la camera lo vede come `sdb1` e lo monta
  da sola in **`/mnt/udisk1`**.
- La porta dell'SSD è la porta 2 dello stesso controller USB della SD0
  (`xhci_hcd`, 2 porte; Bus 03 = lato USB 2.0, Bus 04 = lato USB 3.0).
- **Primo inserimento a USB 2.0** (`usb 3-2: new high-speed`, `480M`: circa
  35-40 MB/s reali). **Cambiando cavo e verso del connettore è passato a USB
  3.0** (`usb 4-2: new SuperSpeed`, `5000M`). La porta supporta l'USB 3: se
  l'SSD va a 480M la causa è il cavo o il connettore.
- Da controllare su tutta la flotta: a USB 2.0 la registrazione su SSD ha poca
  banda (rischio di frame persi, impossibile alzare il bitrate). Verifica:
  `adb shell 'lsusb -t'` → l'SSD deve stare su un bus a `5000M`.
- Idea: il firmware ha già un test di velocità del disco
  (`camera._storageSpeedTest`), che Street View Suite potrebbe lanciare nei
  controlli prima della ripresa.

### Porta USB OTG della scheda NVIDIA

Nel registro di sistema la porta OTG passa in modalità dispositivo
(`tegra-xudc-new ... device mode on`) quando è collegata a un host, ma vede solo
un "non-standard charger": adb via USB non c'è perché nessuna funzione è
configurata su quella porta. L'hardware per far apparire la camera come
periferica USB (per esempio scheda di rete USB verso il tablet) quindi c'è.

## Processi

- `/usr/local/bin/monitor` (PID 1279) è l'orchestratore che legge `init.rc`; i
  suoi figli visibili: `logd`, `adbd`, `mongoose`, `kern_log`.
- L'elenco è stato tagliato a 60 righe: `camerad`, `system_server`, web server,
  `nginx` partono dopo e non sono comparsi. Da verificare con
  `adb shell 'ps -ef | grep -E "local/bin|python" | grep -v grep'`.
- Attivi anche `sshd`, **Samba** (`smbd`/`nmbd`: file accessibili con
  `smb://<IP>`), `argus_daemon` (NVIDIA), `avahi`, `cron`.
- Gira un **desktop Ubuntu completo** in background (`lightdm`, Xorg, Unity,
  GNOME, ibus). Consuma CPU e RAM e scalda: candidato per un mod futuro
  (disattivarlo, se nessuna funzione della camera lo usa).

## Programmi presenti solo sulla camera

Questi sono in `/usr/local/bin` ma non nel pacchetto di update (installati in
fabbrica, date novembre 2018):

- `monitor` — l'orchestratore dell'avvio
- `GPSInfo` — utile per i guasti GPS della flotta
- `mkexfatfs`, `exfatfsck`, `exfatlabel`, `dumpexfat`, `mount.exfat-fuse` —
  utili per il guasto "non formatta tutte le SD"
- `udisk_test`, `http_server`, `batteryInfo`, `pwr_ctl`, `time_tz`, `vm_clean`
- `getprop`/`setprop`, `logcat`, `logd`, `mongoose`, `python3.6`, `tegrastats`
- script: `start_wifi.sh`, `power_on.sh`/`power_off.sh`, `install_samba.sh`,
  `extern_mic_record.sh`, `titan_httpRequest.py`

Il backup (`tools/backup_titan.sh`) ne salva una copia in `applayer.tar`, così
si possono analizzare sul Mac con `tools/elf_probe.py`.
