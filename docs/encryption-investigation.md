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
