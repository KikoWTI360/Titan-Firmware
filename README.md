# Titan-Firmware — Reverse engineering & app-layer mods (WTI 360)

Lavoro di **right-to-repair** sul firmware delle camere **Insta360 Titan** di
proprietà di WorldTravelIn360 (WTI 360), usate per la produzione Google Street
View. Le camere sono **fuori produzione** e il produttore non rilascia più
aggiornamenti né bug fix. Obiettivo: aggiungere funzioni mancanti e correggere
bug **sullo strato applicativo** del firmware, con modifiche **in place** sul
dispositivo e, dove possibile, lato app (OSC) senza toccare il firmware.

> **Repo privato.** Contiene note di reverse engineering su hardware proprietario.
> Nessun binario proprietario Insta360 viene versionato qui (vedi `.gitignore`).

---

## Cosa sappiamo del firmware

Pacchetto di update: `Insta360_Titan_Update.bin` (anche distribuito come
`Insta360_Titan_Update_vX.Y.Z.zip`).

- **Formato**: header testuale (`insta_titan` + versione + lunghezza) seguito da
  un **archivio ZIP non cifrato**. Nessuna cifratura del contenuto.
- **Integrità**: gestita con **MD5** (checksum), **non** con firma
  crittografica RSA/ECDSA. Niente secure boot sull'update.
- **Sistema**: Linux completo su **NVIDIA Jetson (Tegra, ARM64/aarch64)**.
- **Albero del pacchetto**: `titan_update/` con sottocartelle e un manifest
  `bill.list` che mappa ogni cartella al percorso di destinazione sul device:

  | Cartella pacchetto | Destinazione sul device        |
  |--------------------|--------------------------------|
  | `bin/`             | `/usr/local/bin`               |
  | `cfg/`             | `/home/nvidia/insta360/etc`    |
  | `data/`            | `/home/nvidia/insta360`        |
  | `lib/`             | `/usr/local/lib`               |
  | `firmware/`        | `/usr/local/lib/firmware`      |

- **Binari ELF ARM64** non cifrati e non offuscati (alcuni **non** stripped, con
  simboli C++): `camerad`, `system_server`, `power_manager`, `nginx`,
  `crtmpserver`, `update_app`, `update_check`, `factory_test`, `module_tool`,
  `msgclient`, ...
- **Web server in Python in chiaro**: `bin/web_server/` — protocollo **OSC**
  (Open Spherical Camera): `control_center.py`, `state_machine.py`,
  `osc_protocol/`, `pro_osc/`, `config.py`. **Qui si interviene riga per riga**
  senza toccare ELF.
- **Kernel** Linux in `bin/Image`. Script di avvio/init in `bin/*.sh` e
  `cfg/init.rc`, tutti testuali. `default.prop` con `ro.version` ecc.
- **Config di ripresa** in `cfg/`: `takevideo_template.json`,
  `takepicture_template.json`, `takelive_template.json`, `vid_customer.json`,
  `def_cfg.json`, `preview.json` — **qui stanno bitrate, parametri codec e
  template delle modalità**.
- **Blob da NON toccare**: `firmware/sys_dsp_rom.devfw` (firmware DSP/sensori).
- **Precedente utile**: la `version.txt` di una build dichiara di essere
  "basata sul firmware 20201112, con modifica del limite di bitrate della
  modalità Google Street View". Insta360 stessa produce varianti con il bitrate
  Street View ritoccato ⇒ la camera **supporta nativamente** questo tipo di
  modifica.

---

## Struttura del repo

```
Titan-Firmware/
├── original-firmware/   # (gitignored) il .bin/.zip originale — mettilo qui
├── extracted/           # (gitignored) albero titan_update/ ricostruito dallo script
├── mods/                # i NOSTRI patch testuali (JSON cfg, .sh, .py) — VERSIONATI
├── docs/                # documentazione, piani, mappe
├── tools/               # script: estrazione, inventario
├── .gitignore
└── README.md
```

**Perché `original-firmware/` e `extracted/` sono gitignored:** contengono
binari proprietari Insta360 (ELF, kernel, blob DSP) che non ridistribuiamo.
Chiunque lavori al repo rigenera l'albero in locale partendo dalla propria
copia del pacchetto di update.

---

## Workflow

1. **Procurati il pacchetto** e mettilo in `original-firmware/`
   (es. `original-firmware/Insta360_Titan_Update.bin`).
2. **Estrai** in modo ripetibile:
   ```bash
   python3 tools/extract_firmware.py original-firmware/Insta360_Titan_Update.bin
   ```
   Ricostruisce `extracted/titan_update/`.
3. **Inventario** di web server e config di ripresa:
   ```bash
   python3 tools/inventory.py extracted/titan_update
   ```
   Elenca i file Python del web server e localizza **bitrate / codec / parametri
   modalità** nei JSON di `cfg/` (file:riga).
4. **Modifica** lo strato applicativo. Tieni i tuoi patch in `mods/` (copia del
   file originale + versione modificata, oppure un diff), così il repo resta
   pulito e i cambiamenti sono tracciati.
5. **Applica in place** sul dispositivo dopo backup completo (vedi
   `docs/root-access-and-backup.md`).
6. **Testa su UNA sola unità sacrificabile** prima di toccare la flotta.

---

## Documentazione

- [`docs/root-access-and-backup.md`](docs/root-access-and-backup.md) — come
  ottenere root (UART/SSH) e la **strategia di backup anti-brick** da fare
  **prima** di qualsiasi modifica.
- [`docs/capture-config-and-webserver-map.md`](docs/capture-config-and-webserver-map.md)
  — dove si toccano **bitrate, codec e parametri delle modalità** di cattura, e
  mappa del web server Python / OSC.

---

## Nota legale

Hardware di proprietà, fuori produzione. Essendo basato su Linux, il produttore
è tenuto dalla **GPL** a fornire i sorgenti di kernel e componenti GPL su
richiesta. In questo repo **teniamo separati i binari proprietari** e **non li
ridistribuiamo** (vedi `.gitignore`): versioniamo solo nostri script, note e
patch testuali. Le modifiche sono per uso interno su hardware proprio.
