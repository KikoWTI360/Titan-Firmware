# Indagine: cifratura delle registrazioni su SSD

**Obiettivo:** far salvare al firmware custom (registrazione diretta su SSD
esterno) i **file nuovi in chiaro**, come avviene sulle SD. Non interessa
decifrare i file vecchi.

**Data indagine:** 2026-10-28 · firmware estratto da `Insta360_Titan_Update.bin`.

**Confermato dall'utente:** `Insta360_Titan_Update_v1.2.23` è la **build custom**
effettivamente in uso sulla flotta. L'analisi qui sotto è quindi su quella build.
Esito: nessun interruttore di cifratura nel layer applicativo → i prossimi passi
sono (1) l'impronta di un file registrato e (2) l'accesso al device via adb.

## Cosa abbiamo escluso (con prove)

Il layer applicativo **non contiene un interruttore di cifratura**:

- **Config/JSON di `cfg/`** — nessuna chiave `encrypt`/`secure`/`key`. I file
  `*_customer.json`, `def_cfg.json`, `user_cfg.json` non hanno campi di
  cifratura. L'"origin option" di `camerad` ha i campi:
  `storage, mime, width, height, framerate, bitrate, override, tofile, stab,
  loc, module, live, log, hdr, logo, index` — **nessun campo encrypt**.
- **Script di avvio/mount** (`init.rc`, `fstab`, `hardware_init.sh`,
  `*_record_setup.sh`) — nessun `cryptsetup`/`dmsetup`/`dm-crypt`/`luks`,
  nessun `/dev/mapper`. Il mount dell'SSD **non è cifrato**.
- **Web server Python** (`bin/web_server/`) — nessuna cifratura; `secure_filename`
  è solo un sanitizer di werkzeug.
- **Volume manager `system_server`** — è un vold in stile Android: monta/smonta/
  formatta ext4/exfat, gestisce la "Udisk mode". Nessun dm-crypt/luks.
- **Librerie crypto** — `lib/libcrypto.so`, `lib/libssl.so` ci sono, ma l'unico
  binario che li usa è **`nginx`** (HTTPS). `camerad`, `system_server`,
  `crtmpserver`, ecc. **non** dipendono da OpenSSL.
- **`module_tool` / `factory_test`** — nessun comando/chiave di cifratura.

## L'unica traccia: `camerad` ↔ moduli

`camerad` parla ai moduli camera via USB. Il set completo dei comandi
(`USB_CMD_*`) contiene registrazione (`START/STOP_VIDEO_RECORD`,
`SET_VIDEO_PARAM`, `VIDEO_FRAGMENT`), aggiornamento (`UPDATE_SYSTEM_START/
COMPLETE`), calibrazioni, ecc. Riguardo alla cifratura c'è **una sola stringa**:

```
USB_CMD_ENCRYPT_FAIL        (codice di ERRORE, non un comando)
```

**Non esiste** `USB_CMD_SET_ENCRYPT` / `ENABLE_ENCRYPT` / `SET_KEY`. Quindi:

- `camerad` **non attiva/disattiva** la cifratura e **non invia una chiave**.
- Se il video è cifrato, lo fa il **firmware dei moduli** (`sys_dsp_rom.devfw`),
  e `camerad` ne vede solo l'errore (`ENCRYPT_FAIL`).

**Ambiguità da sciogliere:** `USB_CMD_ENCRYPT_FAIL` potrebbe riguardare:
- (a) la cifratura del **video registrato**, oppure
- (b) la verifica di un **pacchetto di aggiornamento** cifrato per i moduli —
  nel qual caso i file video **non sono cifrati da questo meccanismo**.

## Registrazione di prova da terminale (01/11)

Con l'accesso adb e l'SSD montato in `/mnt/udisk1` (USB 3.0), la prova si fa
senza l'app:

```bash
python3 tools/titan_record.py <IP-camera> --secondi 15
```

Lo script usa gli stessi comandi dell'app Insta360 (corpi catturati in rete il
27/09, da `street-view-suite/profiles/titan.profile.json`): apre la sessione,
avvia la registrazione (h264 5280×2972, 180000 kbps, 5 fps, solo originali),
tiene viva la sessione, ferma, aspetta la chiusura dei file e chiude la
sessione. La risposta all'avvio contiene `_recordUrl`, **la cartella in cui la
camera scrive**: dice subito se il firmware custom scrive sull'SSD. Alla fine
elenca la cartella via adb. Prima va chiusa l'app sul tablet (sessione
esclusiva). Testato contro la Titan simulata di street-view-suite
(`fake_titan.py`): sessione tenuta oltre i 10 s, Ctrl-C che ferma la
registrazione, secondo client rifiutato senza disturbare il primo.

Impronta dei file direttamente sulla camera, senza trasferirli (Python 3.6 è
già sulla camera):
```bash
adb push tools/elf_probe.py /tmp/elf_probe.py
adb shell 'for f in <cartella>/*; do /usr/local/bin/python3.6 /tmp/elf_probe.py file "$f"; echo; done; rm /tmp/elf_probe.py'
```

### Risultati sulla camera di test (TITAN 4, ITG4524N4SB4KM, firmware 1.1.010911)

- **Senza SD nei moduli** la registrazione è rifiutata con `-310 "module no
  sdcard"`, sia senza `storage_loc` sia con `storage_loc: 1` (con
  `storage_loc: 0` ancora da provare). Il messaggio è in
  `camerad` (non nel web server Python). Lo schermo mostra in tempo reale le
  schede mancanti (`No SD(1,2,...)`): i moduli comunicano lo stato della propria
  scheda al corpo principale.
- **Prova A, con le 8 SD e l'SSD, senza `storage_loc`** (come l'app): riuscita.
  - `_recordUrl` = `/mnt/udisk1/VID_20261101_192735`: con l'SSD collegato la
    memoria principale è l'SSD (la voce "SD0" nello stato riporta lo spazio
    dell'SSD).
  - Sull'SSD solo i file leggeri, 34 MB in 15 s: `origin_1..8_lrv.mp4` (versioni
    a bassa risoluzione, 2-5 MB l'una), `preview.mp4`, `pro.prj` (5373 byte).
  - I file a piena risoluzione sono sulle SD dei moduli: 101-161 MB per
    obiettivo in 15 s.
  - Quindi in questa modalità l'SSD prende il posto della SD0, non quello delle
    SD dei moduli.
- **Prova B, con le 8 SD e l'SSD, `storage_loc: 1`**: la registrazione parte
  (`/mnt/udisk1/VID_20261101_192908`), ma:
  - le SD dei moduli **non vengono scritte** (0,0 MB su tutte e otto);
  - sull'SSD arrivano **solo i file leggeri** (31 MB: 8 `lrv`, `preview.mp4`
    da 524 KB, `pro.prj`). `df` conferma: 31 MB in tutto sull'SSD.
  - **I file a piena risoluzione non vengono salvati da nessuna parte.** Non
    usare `storage_loc: 1` per riprese vere.
- **Prova APK dedicato, con le 8 SD, `storage_loc: 0` e bitrate 30000**
  (registro della camera, 01/11 ore 20:00). L'APK non manda nessun comando
  speciale: legge le impostazioni (`camera._getSysSetting`) e poi avvia con
  l'origine `{"bitrate": 30000, "framerate": 5, "width": 5280, "height": 2972,
  "saveOrigin": true, "storage_loc": 0, ...}`.
  - La memoria principale in quel momento era la SD0 (l'SSD era stato staccato
    e al reinserimento il percorso era rimasto `/mnt/SD0`), e sull'SSD infatti
    non c'è niente.
  - Nella cartella `/mnt/SD0/VID_20261101_195955` ci sono **tutti gli 8
    `origin_N.mp4`** (31-57 MB ciascuno, ~15 s a 30 Mbit/s) e `pro.prj` (4791
    byte); niente `lrv` né `preview.mp4`.
  - `elf_probe.py file` sulla camera: `origin_1.mp4` e `origin_5.mp4` sono MP4
    H.264 standard (`ftyp isom`, `avc1`), **5280x2972, 14.8 s, nessuna
    struttura di cifratura**; 30.6 e 16.9 Mbit/s (stessa durata: la differenza
    è la scena, non fotogrammi persi). Piena risoluzione e in chiaro.
  - **Quindi la piena risoluzione arriva al corpo principale e viene salvata
    sulla memoria principale** con `storage_loc: 0`. Con l'SSD come memoria
    principale dovrebbe finire sull'SSD (prova successiva).
- **Prova C, da terminale, `titan_record.py --ssd`** (`storage_loc: 0`, 30000
  kbit/s, 8 SD nei moduli, SSD collegato ma `save_path` ancora `/mnt/SD0`):
  stesso risultato dell'APK. `/mnt/SD0/VID_20261101_201044` contiene gli 8
  `origin_N.mp4` + `pro.prj`, 380 MB in 15 s (`df`: SD0 +379 MiB, SSD 0, SD dei
  moduli invariate). Il comando dell'APK è quindi riprodotto per intero. Il
  menu e il riavvio non hanno spostato `save_path` sull'SSD; la camera passa
  da sola a un'altra memoria principale quando quella attuale viene rimossa.
  La connessione riporta i moduli con firmware `…211210B1`, lo stesso del
  pacchetto v1.2.23.
- **Conclusione (corretta dopo la prova APK):** `storage_loc` sceglie dove
  vanno gli originali: assente = SD dei moduli, `0` = memoria principale,
  `1` = da nessuna parte. Da verificare: (1) che con l'SSD come principale gli
  `origin_N.mp4` vadano sull'SSD; (2) ~~risoluzione e cifratura~~ verificato:
  5280x2972 in chiaro; (3) se con `storage_loc: 0` il
  controllo `-310` delle SD dei moduli viene saltato; (4) il bitrate massimo
  che il trasferimento moduli → corpo principale regge (l'APK usa 30000,
  l'app Insta360 180000).
- **I file scritti sull'SSD non sono cifrati** (prova A, analisi sulla camera
  con `elf_probe.py file`): `origin_N_lrv.mp4` e `preview.mp4` sono MP4 standard
  (`ftyp isom`, brand `avc1`/`mp41`, cioè H.264) con `mdat` leggibile; l'entropia
  alta a metà file è quella del video compresso. `pro.prj` è **XML in chiaro**
  (`<?xml ...><project><offset>...`).
- La build v1.2.23 non cifra i file della memoria principale visti finora
  (prova A). La registrazione completa sulla memoria principale c'è, ma la
  attiva `storage_loc: 0` (prova APK), non un firmware diverso. (`UL2GSV` non
  c'entra: è un tool per GPX/file, non per la camera.) I file a piena
  risoluzione sulle SD dei moduli vengono stitchati ogni giorno con lo Stitcher
  Insta360, quindi non sono cifrati nemmeno loro.
- `elf_probe.py file` ora legge tutte le strutture di primo livello di un MP4
  (anche `moov`, che le camere scrivono in fondo) e cerca i segni della
  cifratura standard dentro un MP4 valido (CENC: `encv`, `sinf`, `schm`,
  `tenc`, `pssh`, `senc`). Verificato con ffmpeg su un MP4 normale, uno con
  `moov` all'inizio, uno cifrato CENC e uno troncato.
- Da verificare: `camera._change_module_usb_mode` (in `config.py`) cambia la
  modalità USB dei moduli; se esistesse una modalità in cui i moduli inviano i
  file a piena risoluzione al corpo principale, insieme a `storage_loc: 1`
  darebbe la registrazione diretta su SSD.

## I due test che decidono la strada

### Test 1 — Impronta di un file registrato (il più importante)

```bash
python3 tools/elf_probe.py file "/percorso/di/un/file/dall_SSD"
```
Interpretazione:
- box `ftyp`/`moov` leggibili + entropia medio-bassa → **non cifrato** (è un
  container/formato proprietario): problema diverso e facile.
- entropia ~8.0 da subito, zero box → **cifrato tutto** → pista moduli.
- header in chiaro + corpo ad alta entropia → **cifratura parziale** → spesso
  aggirabile lato container.

Annotare anche estensione e dimensione rispetto a un file normale.

### Test 2 — Accesso al device (per ispezione e prove)

`init.rc` avvia `adbd` → la camera espone **adb**. Via d'accesso probabile
senza UART:
```bash
adb devices && adb shell id
```
Con una shell si può: vedere come viene montato l'SSD a runtime, ispezionare
`monitor` (orchestratore presente solo sul device), e soprattutto **testare**
qualunque modifica su una sola unità.

## TROVATO: la cifratura è nel firmware dei moduli (Ambarella Crypto Engine)

L'esame del blob `firmware/sys_dsp_rom.devfw` (31 MB) è decisivo. Il blob **non è
opaco**: entropia ~5.4/8, pieno di stringhe leggibili. È il firmware dei moduli
camera, basati su SoC **Ambarella** (encoder H264/H265: `AMBA_DSP_H264ENC...`).

Contiene esplicitamente codice di cifratura che usa il **Crypto Engine hardware**
del DSP:

```
%s [chan] [aes|des] [key] [string]     <- interfaccia che cifra con AES/DES e una chiave
Crypto Engine %u:
Ciphertext :
Key        :
3. t app key mode
```

**Conclusione:** i moduli cifrano il flusso registrato tramite il Crypto Engine
(AES/DES) del loro SoC Ambarella; `camerad` sul corpo principale riceve/scrive e
gestisce solo l'errore `USB_CMD_ENCRYPT_FAIL`. Non esiste un interruttore nel
layer applicativo perché la cifratura **non è lì**: è nel firmware dei moduli.

### Implicazione per l'obiettivo (file nuovi in chiaro)

Niente fix rapido via JSON/config. Le strade reali:

1. **Reflash dei moduli con firmware Titan di SERIE (non cifrante)**, mantenendo
   il firmware custom del CORPO principale (che fa la registrazione su SSD). La
   registrazione su SSD è lato corpo (camerad + udisk mount); la cifratura è lato
   moduli → sostituendo solo il firmware dei moduli con quello ufficiale, i
   moduli dovrebbero streammare in chiaro e il corpo scriverebbe file in chiaro
   sull'SSD. **Da validare su un'unità sacrificabile.** Richiede un
   `sys_dsp_rom.devfw` ufficiale (da un update Insta360 standard) e il meccanismo
   di flash dei moduli (`update_app`/`module_tool`).
2. **Patch del firmware dei moduli custom** per saltare la chiamata al Crypto
   Engine. Richiede reverse engineering del firmware Ambarella (possibili
   checksum/firme). Più difficile.
3. **Revert completo al firmware ufficiale** → niente cifratura, ma si perde
   anche la registrazione diretta su SSD. Da evitare se serve l'SSD.

La via 1 è la più promettente: separa la feature voluta (SSD, lato corpo) da
quella non voluta (cifratura, lato moduli).

### CORREZIONE (dai file di versione del blob)

`firmware/redmine.txt` documenta la customizzazione di questa build:

> 基于20201112固件，修改谷歌街景模式码率限制
> = "Basato sul firmware 20201112, modificata la limitazione del bitrate della
>   modalità Google Street View."

`firmware/version.txt` = `2.19072200.190716.190716.211210B1` (firmware moduli).

**La modifica custom documentata è SOLO il bitrate GSV — niente cifratura né
SSD.** Quindi la conclusione "TROVATO" va ridimensionata:

- Le stringhe "Crypto Engine / aes / key" nel blob sono quasi certamente
  **boilerplate dell'SDK Ambarella** (presenti in ogni firmware Titan), non una
  cifratura su misura.
- È probabile che **la cifratura/registrazione-su-SSD non sia in v1.2.23**. Il
  firmware di quella feature potrebbe essere un **altro pacchetto**: sospettato
  n.1 `UL2GSV_v17.zip` (149 MB, "Upload-to-GSV") presente nel Drive.
- Alternativa: la "cifratura" è il **formato proprietario** Insta360 dei file
  origin (serve lo Stitcher), non una cifratura con chiave.

**Da verificare:** (A) contesto delle stringhe crypto nel blob (legate alla
registrazione o solo test SDK); (B) contenuto di `UL2GSV_v17.zip`; (C) impronta
di un file registrato reale.

## Strade possibili (vecchie ipotesi, superate dai risultati sopra)

1. **File non cifrato davvero** → nessuna cifratura: usare lo strumento giusto
   (es. Stitcher) o correggere il container. Caso migliore.
2. **Cifratura nel firmware dei moduli** →
   a. **Reflash dei moduli con firmware Titan di serie** (non cifrante) via
      `module_tool`, verificando la compatibilità col firmware custom del corpo
      principale (la registrazione su SSD è orchestrata dal corpo, i moduli
      streammano l'origin via USB → plausibile che funzioni, **da validare su
      un'unità sacrificabile**).
   b. Patch del blob `sys_dsp_rom.devfw` (difficile: opaco, probabilmente con
      checksum/firma; ultima opzione).
3. **`ENCRYPT_FAIL` legato agli update, non al video** → i file non sono
   cifrati da quel meccanismo: tornare al Test 1 per capire cosa sono davvero.

## Nota

In ogni scenario che tocca i moduli serve comunque l'accesso al device (Test 2)
e il backup completo (vedi `root-access-and-backup.md`). Nessuna modifica ai
moduli senza un'unità sacrificabile e un backup verificato.
