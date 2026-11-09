# Modifiche a street-view-suite per registrare su SSD via cavo USB

Brief per il repo `street-view-suite` (profilo Titan). Tutto quello che segue è
**misurato** sulla camera di test (ITG4524N4SB4KM, firmware 1.1.010911); le
prove sono in `docs/encryption-investigation.md` (prove A-E, 01/11) e
`docs/USB_TRANSPORT.md` (09/11).

## Perché il tablet oggi non registra su SSD (09/11)

Collegamento solo USB riuscito: il tablet vede `ethernet · eth1 ·
192.168.55.12`, ufficio sulla SIM, "CAMERA CONNECTED" su `192.168.55.1`. Poi
il controllo pre-registrazione si ferma: "memory card 2..9 (SD1..SD8) reads
false, should be true". Due cause, entrambe nel profilo:

1. **Il pre-flight pretende le 8 schede dei moduli testate**
   (`_external_dev.entries.1..8.test = true`). Senza schede leggono `false`.
2. **Il comando di avvio non manda `storage_loc`.** Senza `storage_loc` e senza
   schede nei moduli la camera rifiuta con `-310 "module no sdcard"`. Quindi
   anche "Capture anyway" oggi fallirebbe.

## Cosa significa `storage_loc` (misurato, corregge le note del profilo)

| `storage_loc` | dove vanno gli 8 originali a piena risoluzione | SD dei moduli |
|---|---|---|
| assente (oggi) | sulle SD dei moduli; sulla principale solo lrv, preview e pro.prj | **obbligatorie** (senza: `-310`) |
| **0** | **sulla memoria principale**: `origin_1..8.mp4` + `pro.prj` | non servono, non vengono scritte |
| 1 | da nessuna parte (moduli 0 MB, principale ~31 MB): **originali persi** | — |

La nota del profilo che dice "storage_loc 1 = module cards" è sbagliata: con 1
la registrazione parte ma gli originali non vengono salvati. `0` è il valore che
manda l'APK dedicato di Insta360 (registro della camera, 01/11).

La memoria principale è l'SSD (`/mnt/udisk1`) **quando la SD0 non è inserita**:
con SD0 e SSD entrambi presenti la camera resta su `/mnt/SD0`, e con
`storage_loc 0` gli originali finirebbero sulla SD0.

File verificati: MP4 H.264 standard, 5280×2972, 5 fps, un fotogramma chiave al
secondo, nessuna cifratura; nessun fotogramma perso fino a ~80 MB/s totali
(prova al chiuso; la prova all'aperto per scegliere il bitrate è
`tools/titan_bitrate_test.py`).

## Modifiche al profilo (`profiles/titan.profile.json` e la copia in `apps/mobile/assets/camera/`)

1. **Comando di avvio**: aggiungere `"storage_loc":0` (numero) dentro `origin`.
   Bitrate: lasciare `180000` finché la prova all'aperto non dice altro.
2. **Pre-flight, controllo `state`**: togliere `_external_dev.entries.1..8.test`
   (gli indici comunque si spostano quando manca la SD0) e mettere al loro posto:
   - `"_external_dev.save_path": "/mnt/udisk1"`
   - label: "the camera's main storage (SSD)"
   - fix: "Plug the SSD into the camera and take the small card (SD0) out: the
     camera moves its main storage to the SSD by itself. With SD0 in, the eight
     originals would go to SD0."
   Il controllo dell'audio e quello della stabilizzazione restano.
3. **Indirizzo**: `base_url` → `http://192.168.55.1:20000`, `transport`
   resta `ethernet`. È fisso (configurazione della camera), non dipende da un
   router. `fallback` Wi-Fi invariato.
4. **Note**: correggere la tabella di `storage_loc` come sopra.

## Conseguenze da controllare nel resto dell'app

- La cartella della clip (`_recordUrl`) diventa `/mnt/udisk1/VID_...` e
  contiene `origin_1..8.mp4` + `pro.prj`: **niente `origin_N_lrv.mp4` né
  `preview.mp4`**. Tutto ciò che li cerca va rivisto.
- **Gli originali a piena risoluzione diventano raggiungibili via API** (file
  server su porta 8000, percorso assoluto): finora non lo erano perché stavano
  sulle SD dei moduli. Via cavo USB il collegamento è USB 3 (super-speed).
- Test da aggiornare: `apps/mobile/test/camera_profiles_test.dart` verifica oggi
  che `storage_loc` sia assente e quali chiavi chiede il pre-flight.
- Se il profilo della flotta arriva anche dal backend, va aggiornato anche lì.

## Lato camera (repo Titan-Firmware)

Il collegamento USB oggi è una prova temporanea (`tools/titan_usbnet.sh ecm`,
si perde al riavvio). Da rendere permanente come mod 03, con il DHCP del cavo
senza gateway né DNS.
