# Mod 01 — Bitrate di registrazione GSV

**Stato:** pronto da provare su UNA unità sacrificabile. Da confermare quale file
usa davvero il flusso GSV (vedi "Domande aperte").

## Obiettivo

Alzare il bitrate video della registrazione usata per Google Street View oltre i
180 Mbps attuali, come fa la build Insta360 "20201112 con modifica del limite di
bitrate GSV".

## Cosa abbiamo trovato (firmware estratto, 2026-10-28)

- `cfg/vid_customer.json` è una chiamata `camera._startRecording` salvata. Il
  bitrate video è `parameters.origin.bitrate = 184320`.
- Unità: kbps con base 1024. `184320 / 1024 = 180` → **180 Mbps**. Il `128` nello
  stesso file è l'audio AAC.
- Altri parametri del flusso: h264, 30 fps, 5280×2972, `saveOrigin: true`,
  `storage_loc: 1`.
- **Nel web server Python non c'è clamp sul bitrate di registrazione.** I
  `_bitrate` in `pro_osc/gg_osc_option.py` e `osc_protocol/ins_osc_option.py`
  sono le tabelle di opzioni che la camera annuncia all'app per live/preview
  (menù di scelte), non un tetto. `config.py` definisce solo il nome della chiave
  (`BIT_RATE = 'bitrate'`).
- `user_cfg.json` seleziona `mode_select_video: 44`: c'è anche un blocco del
  template `takevideo_template.json` coinvolto (da analizzare).

## Limiti reali (quelli che il software non mostra)

Che il web server non limiti non vuol dire che il valore venga rispettato:

1. **`camerad` o il firmware dei moduli** (blob DSP `sys_dsp_rom.devfw`) possono
   limitare il bitrate senza segnalarlo. Lo si scopre solo misurando il file
   prodotto.
2. **Throughput di scrittura**: con `storage_loc: 1` (SSD) tutti gli stream
   vanno sullo stesso disco. Se 180 Mbps è per modulo, 8 moduli sono già circa
   1,44 Gbps (circa 180 MB/s). Oltre una certa soglia arrivano frame persi o
   errori di registrazione.
3. **Temperatura**: più bitrate significa encoder più carico. Tieni d'occhio
   `camera._updateSysTemp` e la ventola.

Per questo si sale **a piccoli passi** e si misura ogni volta.

## Lo strumento

`set_bitrate.py` modifica il bitrate di tutti gli stream video di un JSON di
`cfg/` (salta l'audio), crea un backup `.orig` e stampa le modifiche:

```bash
python3 mods/01-bitrate-gsv/set_bitrate.py extracted/titan_update/cfg/vid_customer.json --mbps 200 --dry-run
```

Opzioni: `--mbps N` | `--kbps N` | `--factor F`, `--only-current <valore>` per
toccare solo gli stream con un certo bitrate, `--out <file>` o `--inplace`.

## Procedura

Prerequisiti: accesso root e backup verificato (vedi
`docs/root-access-and-backup.md`).

1. **Prepara sul Mac** la versione modificata (primo passo: 200 Mbps):
   ```bash
   python3 mods/01-bitrate-gsv/set_bitrate.py extracted/titan_update/cfg/vid_customer.json --mbps 200 --out mods/01-bitrate-gsv/work/vid_customer.json
   ```
2. **Sul device**, backup del file originale:
   ```bash
   cp -a /home/nvidia/insta360/etc/vid_customer.json /home/nvidia/insta360/etc/vid_customer.json.orig
   ```
3. **Copia** il file modificato in `/home/nvidia/insta360/etc/vid_customer.json`
   (via `scp` se hai SSH, oppure chiavetta USB e shell UART).
4. **Riavvia la camera.**
5. **Test reale**: registra 3–5 minuti con il flusso GSV abituale (app, SSD).
6. **Misura** i file origin prodotti:
   ```bash
   ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,bit_rate -show_entries format=bit_rate,duration -of default=nw=1 <file_origin>.mp4
   ```
   Controlla che il bitrate sia salito davvero, che la durata corrisponda al
   tempo registrato (niente frame persi) e che non ci siano errori nei log.
7. Se tutto va bene, ripeti con 220 e poi 240 Mbps. **Al primo problema torna al
   passo precedente.**

## Rollback

```bash
cp -a /home/nvidia/insta360/etc/vid_customer.json.orig /home/nvidia/insta360/etc/vid_customer.json
```
poi riavvia. Se la camera non parte più, usa la console UART e il backup completo.

## Domande aperte

- Il flusso GSV (app → "11K 5fps" → SSD) legge `vid_customer.json` oppure il
  blocco 44 di `takevideo_template.json`? Serve l'analisi del template
  (comando C) o un test: cambia solo `vid_customer.json` e misura.
- 180 Mbps è per modulo o totale? Si capisce misurando i file origin.
- `camera._setCustomerParam` riscrive `vid_customer.json`? Se sì, l'app può
  impostare il bitrate senza toccare file a mano.

## Registro test

| Data | Unità | Bitrate impostato | Bitrate misurato | Esito / note |
|------|-------|-------------------|------------------|--------------|
|      |       |                   |                  |              |
