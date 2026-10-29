# Black box GNSS fisica — design hardware

Logger GPS **indipendente dal tablet**, agganciato alla seconda porta del
ricevitore u-blox, che registra la traccia completa per tutta la sessione.
Serve a eliminare il punto debole per cui una disconnessione del tablet rende
inutilizzabile (o tagliabile) la clip in corso.

> **Dove vive questo doc.** È scritto qui in `Titan-Firmware/docs/` perché è il
> workspace con accesso in scrittura in questa sessione. La sua casa naturale è
> `street-view-suite/docs/` accanto a `RTK_GNSS.md`, `CAMERA_MODULE.md`: va
> rispecchiato lì quando si imposta push + branch su quel repo. I riferimenti a
> file/percorsi SVS sono relativi a quel repo.

---

## 1. Il problema

Oggi la traccia satellitare viene registrata **sul tablet**: il ricevitore
(ArduSimple + u-blox **ZED-X20P**) manda le fix via USB al tablet, e l'app le
scrive — c'è già una black box software (`apps/mobile/lib/gnss_blackbox.dart`),
continua e crash-safe, **ma gira sul tablet**. Quindi:

- se il tablet si disconnette (o va in doze, o perde l'USB), la camera **continua
  a registrare il video**, ma **la traccia GPS si ferma**;
- quella clip, allineata a tavolino, ha video senza traccia sotto → va **tagliata
  prima della fine**, o diventa inutilizzabile.

### 1.1 Il punto debole nel punto debole: l'alimentazione (segnalato 29/10)

Il u-blox oggi è alimentato **attraverso l'hub USB**. Quando l'hub perde
l'enumerazione dal tablet (tablet scollegato/spento/non-host), **l'hub spegne le
porte downstream anche se è alimentato dalla Powerbox** — è lo stesso
comportamento descritto in `RTK_GNSS.md` ("a hub keeps its downstream ports dark
until a host enumerates it; a tablet that is switched off is not a host"). Di
conseguenza **il u-blox perde corrente** quando il tablet se ne va.

Quindi non basta un logger indipendente per i *dati*: serve anche
un'**alimentazione indipendente** per il u-blox e per il logger. È il requisito
centrale di questo design.

---

## 2. Principio di progetto

> **Nessun elemento della catena di logging GPS deve dipendere dal tablet o
> dall'hub — né per i dati né, soprattutto, per l'alimentazione.**

Due disaccoppiamenti:

1. **Alimentazione:** u-blox e logger presi **direttamente dalla Powerbox**, mai
   dal ramo USB/hub/tablet.
2. **Dati:** il u-blox emette lo **stesso stream su due porte indipendenti** —
   USB verso il tablet (real-time, best-effort) e UART2 verso il logger fisico
   (always-on). La perdita dell'USB ferma solo il real-time.

---

## 3. Architettura di alimentazione (la parte critica)

```
Powerbox (12V)
   │
   ├── reg 5V ─────────────► ArduSimple (u-blox)   [pin "5V IN", switch IOREF/5V = INPUT]
   │                               │                 sempre alimentato, indipendente dall'USB
   │                               │
   │                               ├── USB-C ──(SOLO DATI)──► tablet
   │                               │        (real-time; se salta, si perde solo il real-time)
   │                               │
   │                               └── PIXHAWK JST-GH (TX) ──► Black Box logger
   │
   └── reg 5V ─────────────► Black Box logger (MCU + microSD)
                                   + hold-up cap (chiude il file su perdita Powerbox)
```

Regole:

- **u-blox alimentato dal pin `5V IN` della ArduSimple**, con lo slide switch
  `IOREF / 5V INPUT-OUTPUT` su **INPUT** (è la soluzione "per un rig finito" già
  individuata in `RTK_GNSS.md`, "Due route non prese"). Da quel momento il u-blox
  **non dipende più dall'USB** per la corrente.
- **USB verso il tablet = solo dati.** Il tablet resta host e riceve NMEA via
  CDC-ACM (VID `0x1546`), ma **non alimenta** il u-blox. Se l'USB cade (hub
  de-enumerato, cavo, doze), il u-blox continua a funzionare sulla Powerbox.
- **Niente backfeed verso il tablet.** Con la scheda auto-alimentata va garantito
  che non reimmetta 5V sul VBUS del tablet/hub: cavo USB con **VBUS sollevato**
  (data-only) o diodo/ideal-diode di isolamento. **Da verificare sullo schematico
  ArduSimple** (che oggi non abbiamo in mano — vedi §9).
- **Logger alimentato dalla Powerbox** con un **hold-up capacitor / supercap**
  dimensionato per completare l'ultima scrittura+fsync e chiudere il file se la
  Powerbox stessa cade.

Esito per modalità di guasto (§8): qualunque cosa succeda a tablet/hub/USB, il
u-blox e il logger restano accesi e la traccia continua.

---

## 4. Architettura dei dati

Il ZED-X20P può emettere su più porte **contemporaneamente** (USB, UART1, UART2,
I2C, SPI), ognuna configurabile in modo indipendente.

- **USB → tablet:** stream real-time per l'app (invariato rispetto a oggi).
- **UART2 (connettore PIXHAWK, pin TX) → logger:** stesso flusso, in parallelo,
  che il tablet ci sia o no.

### 4.1 La seconda porta — connettore PIXHAWK

Da `RTK_GNSS.md` ("The third port"), il JST-GH 6 pin silkscreen `PIXHAWK`:

```
5V IN   RX   TX   TP   EVT   GND
```

- **TX** → ingresso dati del logger (NMEA e/o UBX dal u-blox).
- **GND** → massa comune col logger.
- **EVT** → ingresso evento esterno del u-blox: un fronte qui viene
  timestampato **dal ricevitore stesso in tempo GPS, sotto il microsecondo**
  (vedi §6, marker di cattura).
- **TP** → time pulse (1 PPS): disponibile se in futuro si vuole disciplinare il
  clock del logger; non necessario per il logging.
- `5V IN`/`GND` del connettore: via alternativa per alimentare la scheda, ma
  preferiamo il pin header dedicato (§3).

### 4.2 Il logger

- **MCU + microSD.** Legge la UART del u-blox e scrive su SD.
- **Scrittura crash-safe**, come la black box software: append + **fsync**
  periodico; il file è un **GPX/log valido in ogni istante** (apribile senza
  riparazione anche dopo uno strappo). Riusa la logica già collaudata in
  `gnss_blackbox.dart` (append a offset tenuto, chiusura riscritta dopo).
- **Auto-datato dal GNSS.** Le sentenze `RMC`/`ZDA` portano l'ora assoluta: il
  log è timestampato dai satelliti, **nessun orologio esterno serve**.
- **Continuo.** Logga sempre quando è alimentato (vedi §6: lo start/stop cattura
  è un *marcatore*, non un interruttore del logging).
- **Nome file** dal clock interno del logger (RTC) in UTC, es.
  `BBX_YYYYMMDD_HHMMSS.log`, un file per accensione.

Costo storage (dai numeri reali in `RTK_GNSS.md`): ~2.3 MB/h in NMEA clear a
~4.7 Hz, ~23 MB per un turno di 10 h. In UBX grezzo di più, comunque trascurabile
su una microSD.

---

## 5. Scelta hardware — riuso dello stack esistente

Il remote da volante (`hardware/remote-controller/`) è già **ESP32-C3 +
PlatformIO + NimBLE + LiPo/TP4056 + USB-C**. Lo stesso stack regge il logger:

- **MCU:** ESP32-C3 (BLE 5, USB nativo) — stesso di `platformio.ini`.
- **Ingresso dati:** una UART hardware del C3 ← PIXHAWK TX del u-blox.
- **Storage:** microSD via SPI (append+fsync).
- **Alimentazione:** 5V dalla Powerbox → regolatore on-board; **hold-up cap** sul
  rail del logger.
- **BLE:** una characteristic per i marker di cattura (§6), nello stile del
  contratto GATT che già usi (`config.h`: `SVC_UUID`, `buttonEvent`...).
- **Offload:** microSD estraibile e/o via USB-C (come flashi/carichi il remote).

Varianti: può essere una **scheda a sé** o una **funzione aggiuntiva del remote**
(stesso MCU, due UART: BLE pulsanti + logging). Decisione in §9.

---

## 6. Start/stop cattura: marcatori, non interruttore

**Raccomandazione: il logger logga sempre; lo start/stop della cattura è un
marcatore scritto nel log, non un on/off del logging.** Motivo (è la stessa
logica per cui la black box software "parte col ricevitore, non con la cattura,
e non si ferma mai"): se il logging dipendesse da un comando software, avremmo
re-introdotto la dipendenza dal tablet che vogliamo eliminare — un comando mai
arrivato = traccia persa.

Due sorgenti di marker, complementari:

1. **BLE (primaria, lato app).** Una characteristic `captureMark` sul logger: il
   tablet notifica start/stop e il logger scrive una riga marcatore col timestamp
   GNSS corrente. Semplice, riusa il pattern del remote.
2. **EVT (precisa, hardware).** Un fronte sul pin `EVT` a start/stop: il u-blox
   stampa l'istante **da solo, in tempo GPS, sotto il µs**. È anche il "physical
   sync mark" tenuto come riserva in `TITAN_INTEGRATION.md` §5 — risolve il
   binding clip↔traccia senza dipendere dai clock. Chi genera il fronte (relay
   dal tablet o segnale dalla camera) è un dettaglio da definire (§9).

Anche se **entrambi** i marker mancassero, la traccia resta **continua e
auto-datata**: a tavolino si ritaglia la finestra UTC della clip dal log del
logger — esattamente il "recut dalla black box" che già fai, ma immune al tablet.

---

## 7. Bonus: UBX grezzo → PPK

Il logger non ha i vincoli real-time del tablet e ha storage abbondante: può
loggare **UBX grezzo** oltre all'NMEA. Questo riempie l'hook `raw_path` già
previsto in `TrackSegment` (vedi `RTK_GNSS.md`, "Raw UBX logging: build the hook,
leave it off") e abilita il **PPK** in futuro, senza costo oggi. Raccomandato:
attivarlo sul logger (è "gratis" lato tablet) e decidere il PPK dopo.

---

## 8. Modalità di guasto — cosa sopravvive

| Evento | u-blox alimentato? | Logger registra? | Real-time al tablet? | Traccia recuperabile? |
|---|---|---|---|---|
| Tutto ok | sì (Powerbox) | sì | sì | sì |
| Tablet scollegato / spento | **sì** | **sì** | no | **sì** (dal logger) |
| Hub de-enumerato (porta downstream spenta) | **sì** (non è sull'hub) | **sì** | no | **sì** |
| Cavo USB dati staccato | **sì** | **sì** | no | **sì** |
| App killata / doze Android | sì | **sì** | no/intermittente | **sì** |
| Powerbox persa | no | fino all'hold-up: chiude il file | no | sì, fino all'istante del taglio |

Il caso che oggi rompe la clip (tablet che se ne va) diventa: real-time perso,
**traccia completa salva sul logger**.

---

## 9. Da confermare prima di costruire

1. **Pinout del connettore PIXHAWK**: in `RTK_GNSS.md` è letto da una **foto
   sfocata**. Verificare ordine pin e livelli (3.3V vs 5V della UART) sul manuale
   ArduSimple.
2. **`EVT` → `EXTINT`**: confermare che EVT raggiunga l'EXTINT del modulo nella
   config che ArduSimple spedisce (open question già in `RTK_GNSS.md`).
3. **Output su UART2**: il tuo `ublox-conf` oggi **azzera** l'NMEA su
   UART1/UART2 (per non riempire il socket XBee). Va **riattivato** l'output sulla
   porta che alimenta il logger (UBX e/o le poche NMEA utili), con baud e rate
   scelti. Config una-tantum via u-center.
4. **5V IN switch**: confermare il comportamento dello slide switch
   `IOREF/5V INPUT-OUTPUT` in INPUT e i limiti di tensione/corrente.
5. **Nessun backfeed USB**: verificare sullo schematico ArduSimple che la scheda
   auto-alimentata non reimmetta VBUS verso il tablet; altrimenti cavo data-only
   (VBUS sollevato) o diodo.
6. **Scheda a sé o funzione del remote**: decidere se il logger è un dispositivo
   separato o una seconda UART sul remote ESP32-C3 esistente.
7. **Sorgente del fronte EVT**: relay pilotato dal tablet, o un segnale prodotto
   dalla camera, o solo marker BLE (EVT in un secondo tempo).

---

## 10. Integrazione Street View Suite

- **BLE:** aggiungere una characteristic `captureMark` al contratto GATT (stile
  `config.h` del remote); l'app la scrive a start/stop.
- **Retrieval:** i file del logger seguono il percorso già esistente
  (`GnssUploads` che cammina `gnss_tracks` e `gnss_blackbox`, upload su rete non
  metered, cancellazione solo dopo conferma hash dall'ufficio). Aggiungere la
  sorgente "logger fisico".
- **Recut ufficio:** quando la copia del tablet è corta, ritagliare la finestra
  UTC della clip dalla traccia del logger (già aritmetica su GPX).
- **HAL:** **non si tocca.** È tutto sotto il livello del contratto camera.

---

## 11. Piano di test

1. Banco: u-blox su 5V da Powerbox (USB staccato) → verifica che continui a
   emettere su UART2 e che il logger scriva un GPX valido.
2. Strappo USB a metà "cattura" simulata → il real-time si ferma, il log del
   logger resta continuo e valido (`kill -9` view: rileggere i byte col file
   ancora aperto).
3. Perdita Powerbox → l'hold-up chiude il file; il file apre in QGIS senza
   riparazione.
4. Marker: start/stop via BLE e/o EVT → verificare che le righe marcatore cadano
   all'istante giusto (confronto con l'ora GNSS).
5. Recut: ritagliare una finestra UTC dal log e confrontarla con la traccia
   per-clip del tablet quando entrambe esistono.
