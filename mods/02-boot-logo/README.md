# Mod 02 — Logo WTI360 all'avvio della camera

**Stato:** funziona. Sulla camera di test (01/11) all'avvio compare il logo
WTI360 a schermo intero. La prima versione sostituiva solo `prologo`, senza
effetto visibile: l'immagine giusta è `bootLogo` (vedi sotto).

## Obiettivo

Sostituire la schermata "Insta360 Titan" che compare sul piccolo schermo
all'accensione con il logo WTI360.

## Cosa abbiamo trovato (01/11)

- Lo splash lo disegna il servizio `bootanimation`, avviato per primo da
  `init.rc` (`service bootan /usr/local/bin/bootanimation`, `oneshot`).
- Le immagini **non sono file esterni**: sono bitmap **incorporati nel binario**,
  in variabili `static` C++ (nella tabella dei simboli i nomi sono codificati,
  es. `_ZL8bootLogo`; gli strumenti li riconoscono da soli):

  | Simbolo | Misura | Offset nel file | Cosa mostra |
  |---|---|---|---|
  | `bootLogo` | 128×64, 1024 byte | `0x11cd0` | **la schermata d'avvio "Insta360 Titan"** (schermo intero) |
  | `prologo_17_16_94_32` | 94×32, 376 byte | `0x181b0` | il logo "Pro", ereditato dalla Insta360 Pro che condivide il codice |

  Prova: sostituendo solo `prologo`, all'avvio compariva ancora "Insta360
  Titan"; rendendo `bootLogo` con `show` si legge proprio quella scritta.
- Lo schermo è **128×64 monocromatico**, pilotato da un controller **SSD1306**
  (`oled_module::ssd1306_disp_icon`). Codifica dei pixel **`page-lsb`**, il suo
  formato nativo: confermata con `show` su entrambe le immagini.
- Si riscrivono **solo i byte delle due immagini**: il resto del file resta
  identico.
- Nel binario ci sono anche le icone `set_bottomlogo_*` (voci di menu
  dell'impostazione `set_logo`, il logo alla base del video 360) e le icone di
  modalità `live_icon`, `video_icon`, `camera_icon`: questo mod non le tocca.

## I loghi

In questa cartella, tutti già alla misura giusta, in bianco e nero:

| File | Per | Contenuto |
|---|---|---|
| `logo_wti360_128x64.png` | `bootLogo` (**predefinito**) | i quattro riquadri `[W][T][I][360]` in fila e sotto "WORLD TRAVEL / IN 360" |
| `logo_wti360_128x64_griglia.png` | `bootLogo` (alternativa) | la griglia 2×2 fedele al logo, col globo e "360" nel quarto riquadro |
| `logo_wti360_94x32.png` | `prologo` | i quattro riquadri in fila |

Ricavati dall'icona WTI360 di Street View Suite. Riquadri accesi, lettere
scavate; il "360" nei riquadri è disegnato a mano pixel per pixel, perché con un
font a quella misura le cifre si impastavano.

Per preparare un logo diverso:

- **Esattamente la misura dell'immagine da sostituire** (128×64 o 94×32).
  Un'immagine più grande viene ridotta, ma i dettagli sottili spariscono: in
  prova un testo normale ridotto da 300×90 a 94×32 è scomparso del tutto.
- **Bianco e nero puro, tratti spessi** (almeno 2 pixel). Niente sfumature.
- **Bianco = pixel acceso** (sull'OLED si vede chiaro su nero). Se il logo è
  scuro su chiaro, aggiungi `--invert`.
- Formato PNG. Serve Pillow (`pip install pillow`; è già in Anaconda).

## Versione del firmware sul logo (09/11)

`deploy_logo.sh` scrive in basso a destra, piccola (font 3×5 pixel, nelle righe
59-63 che il logo lascia vuote), la **versione del firmware letta dalla
camera** al momento del deploy, es. `FW 1.1.010911`: è la stessa che la camera
dichiara all'app (`camera._connect`), quindi all'avvio si vede quale firmware
gira davvero. Il pacchetto installato si chiama v1.2.23, ma la camera di test
si dichiara `1.1.010911`: sul logo va quello che dice la camera.

- serve adb **in rete** (IP:5555) e l'app del tablet **chiusa** (la camera
  accetta un solo client);
- testo a scelta: `TESTO="FW 1.2.23 WTI" mods/02-boot-logo/deploy_logo.sh`;
- nessun testo: `TESTO= mods/02-boot-logo/deploy_logo.sh`;
- anteprima sul Mac senza toccare la camera:
  `python3 mods/02-boot-logo/logo_tool.py anteprima mods/02-boot-logo/logo_wti360_128x64.png --testo "FW 1.1.010911" --out /tmp/anteprima.png`.

Il testo è fisso: si legge al deploy. Se un aggiornamento firmware sostituisce
`bootanimation` torna il logo Insta360, che è a sua volta il segnale che il mod
va riapplicato (e con la nuova versione).

## Procedura

Prerequisiti: accesso adb/root e backup già fatto (`docs/root-access-and-backup.md`).

1. **Aggiorna** il repo: `git pull`.
2. **Applica i loghi** con lo script:
   ```bash
   mods/02-boot-logo/deploy_logo.sh
   ```
   Per usare la griglia invece del logo predefinito:
   ```bash
   LOGO_FULL=mods/02-boot-logo/logo_wti360_128x64_griglia.png mods/02-boot-logo/deploy_logo.sh
   ```
3. **Riavvia la camera** e guarda lo schermo all'accensione.

Lo script prende il `bootanimation` **dalla camera collegata**, non quello del
pacchetto: un binario di un'altra versione firmware potrebbe non partire
(librerie diverse), e siccome `bootanimation` è nella catena di avvio la camera
non finirebbe di accendersi. Così il mod vale per ogni unità.

Cosa fa, fermandosi al primo controllo che non torna:
1. copia `/usr/local/bin/bootanimation` dalla camera (o il `.orig`, se esiste
   già) e verifica l'MD5;
2. sostituisce `bootLogo` e `prologo` (`logo_tool.py patch`, due passate);
3. carica il risultato in `/tmp` sulla camera e verifica l'MD5;
4. salva l'originale in `/usr/local/bin/bootanimation.orig` (solo la prima
   volta, così non viene mai sovrascritto), sostituisce con un rename atomico,
   verifica l'MD5 del file installato e del backup, toglie il file temporaneo.

Rilanciarlo è sicuro: riparte sempre dall'originale. Una copia dell'originale
resta anche sul Mac, in `work/bootanimation.<seriale>.orig`. Se segnala un
filesystem in sola lettura: `adb shell mount -o remount,rw /` e rilancia.

### A mano, per vedere e controllare sul Mac

Disegnare un'immagine del binario nel terminale:
```bash
python3 mods/02-boot-logo/logo_tool.py show extracted/titan_update/bin/bootanimation --symbol bootLogo --size 128x64 --encoding page-lsb
```
Creare una copia con un logo (l'originale non viene mai toccato):
```bash
python3 mods/02-boot-logo/logo_tool.py patch extracted/titan_update/bin/bootanimation mods/02-boot-logo/logo_wti360_128x64.png --symbol bootLogo --size 128x64 --encoding page-lsb --out mods/02-boot-logo/work/bootanimation
```
Senza `--symbol`/`--size` lo strumento lavora su `prologo_17_16_94_32`.

## Rollback

```bash
adb shell 'cp -a /usr/local/bin/bootanimation.orig /usr/local/bin/bootanimation'
```
poi riavvia. L'originale è anche nel backup (`applayer.tar`).

**Attenzione: `bootanimation` fa parte della catena di avvio.** In `init.rc`:
`logd` → `bootanimation`, che imposta `sys.bootan=true` → `update_check` →
`camerad` e web server. Se `bootanimation` non parte o si blocca, la camera **non
finisce di avviarsi**.

- La patch cambia solo byte di dati (i bitmap), non il codice: la logica, che
  imposta `sys.bootan`, resta identica. Il rischio vero è una copia sbagliata o
  corrotta, o un binario di un'altra build: per questo lo script parte dal file
  della camera e verifica l'MD5 a ogni passaggio.
- Se dopo il riavvio la camera non si avvia del tutto, adb è comunque
  disponibile: `adbd` è nella classe `base`, che parte prima di `bootanimation`.
  Da lì si esegue il rollback qui sopra. Ultima risorsa: la console UART (login
  root automatico su `ttyS0`).

## Limiti

- **Stessa misura e stessa posizione** delle immagini originali. Cambiarle vuol
  dire modificare il codice che le disegna: non è coperto da questo mod.
- Un aggiornamento firmware riscrive `/usr/local/bin/bootanimation` (`bin/` →
  `/usr/local/bin` nel `bill.list`): dopo un update il mod va riapplicato.
- Cambia solo lo splash d'avvio. Se il logo Insta360 compare anche in altre
  schermate, quelle le disegna un altro binario (probabilmente `system_server`):
  si cerca con lo stesso metodo,
  `python3 tools/elf_probe.py symbols extracted/titan_update/bin/system_server "logo|icon"`.

## Registro test

| Data | Unità | Cosa | Esito / note |
|------|-------|------|--------------|
| 01/11 | camera di test (192.168.1.5) | solo `prologo` (94×32) | installato e verificato (MD5), all'avvio ancora "Insta360 Titan": `prologo` non è la schermata d'avvio della Titan |
| 01/11 | camera di test | `bootLogo` (128×64, logo B) + `prologo` | **riuscito**: all'avvio compare "WTI 360 / WORLD TRAVEL IN 360" (foto) |
| 09/11 | simulazione (adb e camera finti) | versione sul logo: letta dalla camera, testo a scelta, nessun testo | riuscito; da provare sulla camera |
