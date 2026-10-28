# Mappa: config di ripresa, bitrate/codec, e web server OSC

> **Stato di questo documento.** È una mappa *di partenza* basata sulla
> struttura del firmware che hai descritto + conoscenza generale
> dell'architettura Insta360/OSC. **La verità di terra** (elenco file reale e
> posizioni esatte `file:riga`) la produce lo strumento:
> ```bash
> python3 tools/inventory.py extracted/titan_update --json docs/inventory.json
> ```
> Dopo averlo lanciato sulla tua copia, aggiorna le sezioni qui sotto con gli
> output reali. Non ho potuto eseguirlo qui perché il pacchetto (lo zip da ~37MB
> sul tuo Drive) non è scaricabile in questa sessione.

---

## 1. Dove stanno bitrate, codec e parametri delle modalità

Due livelli, in ordine di preferenza per le modifiche:

### Livello 1 — JSON di `cfg/` (il posto giusto da cui partire)

Destinazione sul device: **`/home/nvidia/insta360/etc`**.

| File                        | A cosa serve                                           |
|-----------------------------|--------------------------------------------------------|
| `takevideo_template.json`   | Template della modalità **video** registrato su SD/SSD |
| `takelive_template.json`    | Template della modalità **live** (streaming RTMP)      |
| `takepicture_template.json` | Template della modalità **foto**                       |
| `vid_customer.json`         | Override/profili "customer" per il video (spesso è qui che vivono i limiti ritoccati, incl. il caso Street View) |
| `def_cfg.json`              | Config di default globale (modalità attiva, parametri comuni) |
| `preview.json`              | Parametri dell'anteprima/preview (basso bitrate)       |

**Chiavi da cercare** (è ciò che `inventory.py` localizza automaticamente):

- **Bitrate**: `bitrate`, `bit_rate`, `max_bitrate`, `video_bitrate`, `rc_mode`
  (rate-control: CBR/VBR), valori in bit/s (es. `120000000` = 120 Mbps).
- **Codec**: `codec`, `h264`/`h265`/`hevc`, `profile`, `entropy`/`cabac`.
- **GOP / frame**: `gop` (distanza tra keyframe), `fps`/`framerate`, `idr`.
- **Risoluzione**: `width`, `height`, oppure stringhe tipo `11968x5984`, `11K`, `8K`.
- **Modalità / template**: `mode`, `template`, e in particolare `google` /
  `street_view` / `gsv` → è il ramo che Insta360 stessa ha ritoccato nella build
  "20201112 con modifica del limite di bitrate GSV".

**Come modificare (esempio bitrate):** apri il JSON della modalità giusta,
individua la chiave `bitrate` nel ramo video, cambia il valore (resta nei limiti
fisici dell'encoder: alzare il bitrate oltre ciò che DSP/encoder reggono dà
artefatti o crash). Salva una copia originale in `mods/<nome>/original/` e la
versione modificata in `mods/<nome>/patched/` (vedi `mods/README.md`).

> **Attenzione al punto di verità.** Se esistono più file che definiscono la
> stessa modalità (es. `takevideo_template.json` *e* un override in
> `vid_customer.json` o `def_cfg.json`), vince quello che il firmware legge per
> ultimo/più specifico. Dopo `inventory.py`, cross-controlla quale file il web
> server carica davvero (sezione 2) prima di decidere dove mettere la patch.

### Livello 2 — ELF `camerad` (ultima spiaggia)

Se un limite è **hard-coded nel binario** `camerad` (non in un JSON), serve un
patch ELF mirato. `camerad` è ARM64 e in parte **non stripped** (ha simboli
C++), quindi è analizzabile (Ghidra/radare2). Fallo **solo se** il JSON non
basta: documenta offset, byte prima/dopo e motivo in un file dedicato dentro
`mods/`. Il caso Street View suggerisce però che il limite sia *configurabile*,
non murato nel binario → prova prima il Livello 1.

---

## 2. Mappa del web server Python (OSC)

Destinazione sul device: `bin/web_server/` → **`/usr/local/bin/web_server`**.
Tutto in chiaro, modificabile riga per riga.

| Componente            | Ruolo (atteso)                                             |
|-----------------------|------------------------------------------------------------|
| `control_center.py`   | Dispatcher centrale: riceve i comandi e orchestra la camera |
| `state_machine.py`    | Macchina a stati (idle → recording → ...); gatekeeper delle transizioni |
| `config.py`           | Costanti/limiti applicativi del web server                 |
| `osc_protocol/`       | Implementazione del protocollo **OSC** standard (`/osc/info`, `/osc/state`, `/osc/commands/execute`) |
| `pro_osc/`            | Estensioni "pro" Insta360 sopra OSC (comandi specifici Titan) |

**Dove intervenire a seconda dell'obiettivo:**

- **Sbloccare/estendere un parametro di ripresa** → quasi sempre basta il JSON
  di `cfg/` (Livello 1). Il web server legge quei template e li passa a
  `camerad`.
- **Nuovo comportamento OSC / nuova modalità logica** → `pro_osc/` +
  `state_machine.py` (aggiungere una transizione/comando) + un template JSON in
  `cfg/`.
- **Rimuovere un controllo/limite applicativo** (es. un `if bitrate > X: clamp`)
  → cercalo in `config.py` / `control_center.py` (lo trova `inventory.py`, gruppo
  BITRATE).
- **Timelapse / intervallo / burst** → cerca `interval`/`timelapse`/`burst`
  (gruppo MODE/TEMPLATE) tra `pro_osc/` e i template foto.

Lo strumento stampa anche una **mappa euristica dei comandi OSC** (sezione D
dell'output) con `file:riga`, così parti dal punto giusto.

### Via alternativa: app Android WTI360 via OSC (senza toccare il firmware)

Molte funzioni si possono reimplementare **lato app** parlando con l'API OSC
della camera (stessa che usa l'app ufficiale): l'app invia
`/osc/commands/execute` con i parametri della modalità. Vantaggio: **zero
rischio di brick**, nessuna modifica al dispositivo. Limite: puoi solo usare ciò
che il firmware **già espone**; per sbloccare parametri oltre i limiti
applicativi serve comunque la patch lato `cfg/`/web server. Strategia consigliata:
prima prova via OSC; se il parametro è "clampato" dal web server, togli il clamp
lato firmware (Livello 1) e poi pilotalo dall'app.

---

## 3. Checklist prima di modificare un parametro

1. `tools/inventory.py` → trova **tutti** i file che citano il parametro.
2. Stabilisci **quale** file è il punto di verità (chi legge per ultimo).
3. Copia l'originale in `mods/<mod>/original/`.
4. Modifica in `mods/<mod>/patched/`; tieni il cambiamento **minimo**.
5. Backup del device (vedi `root-access-and-backup.md`) **prima** di applicare.
6. Applica in place su **una unità sacrificabile**, riavvia il servizio/camera,
   verifica con una ripresa reale (file prodotto, bitrate effettivo, assenza di
   crash nei log).
7. Solo dopo esito positivo → flotta.
