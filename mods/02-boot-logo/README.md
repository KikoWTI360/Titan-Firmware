# Mod 02 — Logo WTI360 all'avvio della camera

**Stato:** strumento pronto e testato su un binario di prova. Da eseguire sul
`bootanimation` vero e poi provare su UNA unità sacrificabile.

## Obiettivo

Sostituire il logo Insta360 che compare sul piccolo schermo all'accensione con
il logo WTI360.

## Cosa abbiamo trovato (01/11)

- Lo splash lo disegna il servizio `bootanimation`, avviato per primo da
  `init.rc` (`service bootan /usr/local/bin/bootanimation`, `oneshot`).
- Il logo **non è un file esterno**: nel pacchetto non ci sono zip, PNG o BMP. È
  un bitmap **incorporato nel binario**, nel simbolo:

  ```
  prologo_17_16_94_32   →  94×32 pixel, disegnato a x=17, y=16
  ```

  Il binario è C++ e il logo è una variabile `static`, quindi nella tabella dei
  simboli il nome compare codificato come `_ZL19prologo_17_16_94_32`. Lo strumento
  lo riconosce da solo.
- Lo schermo è **128×64 monocromatico** (c'è un'icona `reboot_128_64` a schermo
  intero). Ogni pixel è acceso o spento: niente grigi né colori.
- Il controller dello schermo è un **SSD1306** (funzione
  `oled_module::ssd1306_disp_icon`), quindi la codifica attesa è `page-lsb`, il
  suo formato nativo. `show` serve a confermarlo.
- Il binario ha la tabella dei simboli, quindi si sa esattamente dove sta il
  bitmap e quanti byte occupa: si riscrivono **solo quei byte**, il resto del
  file resta identico.
- Nel binario ci sono anche le icone `set_bottomlogo_*`: sono le voci di menu
  dell'impostazione `set_logo` (il logo alla base del video 360, il "nadir").
  È un'altra cosa rispetto allo splash e questo mod non la tocca.

## Il logo

**Pronto all'uso:** `logo_wti360_94x32.png` in questa cartella. È il logo WTI360
(l'icona di Street View Suite) adattato allo schermo: i quattro tasselli in fila,
`[W][T][I][360]`, invece che a griglia 2×2, che a 32 pixel di altezza verrebbe
minuscola. Tasselli accesi, lettere scavate. Il "360" è disegnato a mano pixel
per pixel, perché con un font a quella misura le cifre si impastavano.
Verificato: i pixel riletti dal binario patchato sono identici a quelli del PNG.

Per preparare un logo diverso:

- **Esattamente 94×32 pixel.** Un'immagine più grande viene ridotta, ma i
  dettagli sottili spariscono: in prova un testo normale ridotto da 300×90 è
  scomparso del tutto, è rimasta solo la cornice.
- **Bianco e nero puro, tratti spessi** (almeno 2 pixel). Niente sfumature.
- **Bianco = pixel acceso** (sull'OLED si vede chiaro su nero). Se il logo è
  scuro su chiaro, aggiungi `--invert`.
- Formato PNG. Serve Pillow (`pip install pillow`; è già in Anaconda).

## Procedura

Dalla cartella del repo, sul Mac:

1. **Aggiorna** il repo:
   ```bash
   git pull
   ```
2. **Trova la codifica dei pixel.** Lo strumento disegna il logo attuale nel
   terminale in tutte le codifiche compatibili con la sua dimensione. Quella in
   cui si legge il logo Insta360 è la codifica giusta (es. `page-lsb`, il
   formato nativo degli OLED tipo SSD1306):
   ```bash
   python3 mods/02-boot-logo/logo_tool.py show extracted/titan_update/bin/bootanimation
   ```
3. **Genera la copia con il nuovo logo** (l'originale non viene mai toccato; la
   cartella `work/` è esclusa da git):
   ```bash
   python3 mods/02-boot-logo/logo_tool.py patch extracted/titan_update/bin/bootanimation mods/02-boot-logo/logo_wti360_94x32.png --encoding page-lsb --out mods/02-boot-logo/work/bootanimation
   ```
   Controlla l'anteprima ASCII "Nuovo logo": è quello che vedrai sullo schermo.
   Lo strumento stampa anche gli SHA-256 di originale e copia.
4. **Verifica la copia** rileggendola:
   ```bash
   python3 mods/02-boot-logo/logo_tool.py show mods/02-boot-logo/work/bootanimation --encoding page-lsb
   ```

Sulla camera di test (serve l'accesso adb/root e il backup già fatto, vedi
`docs/root-access-and-backup.md`):

5. **Applica il logo con lo script** (consigliato):
   ```bash
   mods/02-boot-logo/deploy_logo.sh
   ```
   Lo script prende il `bootanimation` **dalla camera collegata**, non quello del
   pacchetto. È importante: un binario di un'altra versione firmware potrebbe non
   partire (librerie diverse), e siccome `bootanimation` è nella catena di avvio
   la camera non finirebbe di accendersi. Partendo dal file della camera stessa,
   il mod vale per ogni unità qualunque firmware abbia.

   Cosa fa, fermandosi al primo controllo che non torna:
   1. copia `/usr/local/bin/bootanimation` dalla camera e verifica l'MD5;
   2. crea la versione col logo (`logo_tool.py patch`);
   3. la carica in `/tmp` sulla camera e verifica l'MD5;
   4. salva l'originale in `/usr/local/bin/bootanimation.orig` (solo la prima
      volta, così il backup non viene mai sovrascritto), poi sostituisce con un
      rename atomico e verifica di nuovo l'MD5 del file installato e del backup.

   Rilanciarlo è sicuro: se trova già il `.orig`, riparte da quello. Una copia
   dell'originale resta anche sul Mac, in `work/bootanimation.<seriale>.orig`.
   Se segnala un filesystem in sola lettura: `adb shell mount -o remount,rw /` e
   rilancia.
6. **Riavvia la camera** e guarda lo schermo all'accensione.

I passi 2-4 qui sopra (sul file del pacchetto) servono a vedere e controllare il
risultato sul Mac; lo script rifà la stessa operazione sul file della camera.

## Rollback

```bash
adb shell 'cp -a /usr/local/bin/bootanimation.orig /usr/local/bin/bootanimation'
```
poi riavvia.

**Attenzione: `bootanimation` fa parte della catena di avvio.** In `init.rc`:
`logd` → `bootanimation`, che imposta `sys.bootan=true` → `update_check` →
`camerad` e web server. Se `bootanimation` non parte o si blocca, la camera **non
finisce di avviarsi**.

- La patch cambia solo byte di dati (il bitmap), non il codice: la logica, che
  imposta `sys.bootan`, resta identica. Il rischio vero è una copia sbagliata o
  corrotta, o un binario di un'altra build: per questo lo script parte dal file
  della camera e verifica l'MD5 a ogni passaggio.
- Se dopo il riavvio la camera non si avvia del tutto, adb è comunque
  disponibile: `adbd` è nella classe `base`, che parte prima di `bootanimation`.
  Da lì si esegue il rollback qui sopra. Ultima risorsa: la console UART.

## Limiti

- **Stessa misura e stessa posizione** del logo originale (94×32 a x=17, y=16).
  Cambiare dimensione o posizione vuol dire modificare il codice che lo disegna:
  non è coperto da questo mod.
- Un aggiornamento firmware riscrive `/usr/local/bin/bootanimation` (`bin/` →
  `/usr/local/bin` nel `bill.list`): dopo un update il mod va riapplicato.
- Cambia solo lo splash d'avvio. Se il logo Insta360 compare anche in altre
  schermate, quelle le disegna un altro binario (probabilmente `system_server`):
  si cerca con lo stesso metodo,
  `python3 tools/elf_probe.py symbols extracted/titan_update/bin/system_server "logo|icon"`.

## Registro test

| Data | Unità | Codifica | Esito / note |
|------|-------|----------|--------------|
|      |       |          |              |
