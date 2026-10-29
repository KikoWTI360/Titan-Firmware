# Brief: progetto "wti360-stitcher" (prompt per nuova sessione Claude Code)

Questo file contiene il **prompt pronto da incollare** in una NUOVA sessione
Claude Code, per avviare lo stitcher 360 ottimizzato per Mac, distribuibile via
Mac Multi Hub, che produce MP4 equirettangolare + CAMM con GPS RTK iniettato in
fase di export.

**Decisioni bloccate (29/10):**
- Nome repo: **`wti360-stitcher`**
- Motore: **Swift** (nativo VideoToolbox/Metal + nativo Mac Multi Hub)
- GPS: **solo tempo reale** (RTK), con la pipeline predisposta ad accettare in
  futuro posizioni PPK senza modifiche (nessun lavoro PPK ora)

**Come usarlo:** apri una nuova sessione Claude Code, (consigliato) aggancia in
sola lettura i repo `street-view-suite` e `mac-multi-hub`, poi incolla il blocco
qui sotto.

---

```text
Apri un PROGETTO NUOVO E SEPARATO, con un suo repo GitHub privato sotto
l'account KikoWTI360. Nome: "wti360-stitcher".
NON è un sottomodulo di altri repo: è un progetto a sé, ma progettato per
incastrarsi con due progetti esistenti (li trovi su GitHub, stesso account):
  - street-view-suite  (il nostro suite di cattura/pubblicazione Street View)
  - mac-multi-hub       (cluster di Mac per distribuire il calcolo)
Prima di scrivere codice, AGGANCIA e leggi questi due repo (add_repo in sola
lettura) per allinearti ai loro contratti reali.

== OBIETTIVO ==
Uno STITCHER 360 ottimizzato per Mac che, partendo dalle registrazioni della
Insta360 Titan, produce in output un MP4 EQUIRETTANGOLARE con traccia CAMM
(Camera Motion Metadata, lo standard Google per Street View). Due requisiti
non negoziabili:
  1. Le posizioni GPS ad alta precisione (RTK, dal nostro ricevitore u-blox
     ZED-X20P) vanno INIETTATE DURANTE l'export, scrivendo la CAMM dalle nostre
     fix RTK al momento dell'encode — NON sostituendo le posizioni dopo che il
     file è già stitchato.
  2. Il lavoro di stitching deve essere DISTRIBUIBILE su più Mac tramite
     mac-multi-hub (un Mac hub spezza il lavoro e lo manda ai worker, poi
     riassembla), per dimezzare o più i tempi.

== FATTI DI PARTENZA (verificati sul firmware/materiale Titan, dati per buoni) ==
- Input di una cattura: per ogni lente un file "orig_N.mp4" (N=1..8), MP4
  STANDARD H.264/H.265 full-size (NON .insv opachi), più un file "pro.prj" che
  è "ciò che lo stitcher legge" (ricetta di stitching + calibrazione: modello
  fisheye, estrinseci/offset delle 8 lenti). C'è anche un "_lrv.mp4" (preview
  low-res) che puoi ignorare.
- Il PERNO della fattibilità è PARSARE pro.prj: lì c'è la calibrazione che fa la
  qualità. Primo task di R&D: procurarsi un pro.prj reale e reverse-engineerarne
  il formato (potrebbe essere JSON/XML/protobuf/binario proprietario). Finché non
  è decodificato, lavora dietro un'interfaccia "Calibration" con un
  provider mock, così il resto della pipeline avanza.
- Il GPS NON influenza la geometria dello stitching: la geometria dipende da
  calibrazione (pro.prj) + IMU (orientamento/stabilizzazione). Il GPS è solo
  metadato (CAMM). Quindi i worker fanno PIXEL; la CAMM/GPS si scrive in fase di
  ASSEMBLE dal track RTK autorevole. Tienili separati.
- Il time-binding video<->track è già risolto in street-view-suite (offset
  misurato ~44 ms, più un pin hardware EVT sul ricevitore che timestampa un
  evento in tempo GPS sub-µs). Riusa quella logica/quel contratto per sapere a
  quale istante UTC corrisponde ogni frame; NON reinventarlo.

== ARCHITETTURA RICHIESTA ==
- Linguaggio: Swift (nativo Mac + nativo mac-multi-hub). Il motore di stitching
  deve usare: VideoToolbox per il decode hardware degli 8 stream, Metal per la
  reproiezione fisheye->sfera e il blend delle cuciture, VideoToolbox/AVFoundation
  per l'encode HEVC. Sfrutta Apple Silicon (memoria unificata, 8 decoder).
- Separa nettamente in package SwiftPM:
    * StitchCore   — libreria pura: decode -> reproiezione (da Calibration) ->
                     blend seam -> encode di un SEGMENTO equirettangolare.
                     Nessuna dipendenza da mac-multi-hub né da GPS.
    * CammMux      — scrittura della traccia CAMM da un track RTK + orientamento
                     IMU + time-binding (contratto condiviso con street-view-suite;
                     se SVS ha già un writer/parser CAMM o core/gnss, riusalo via
                     FFI o replica il contratto, NON divergere).
    * ProPrj       — parser di pro.prj dietro un protocollo Calibration (mock +
                     reale quando il formato è noto).
    * StitcherCLI  — eseguibile standalone: dato un folder cattura (8 orig_N.mp4
                     + pro.prj) e un track RTK, produce l'MP4 equirettangolare+CAMM.
    * StitcherMMH  — l'integrazione con mac-multi-hub: un TaskHandler
                     ("stitch.segment") che esegue UN segmento via StitchCore, e
                     un JobPlan che in `partition` spezza la cattura in segmenti
                     temporali e in `assemble` concatena i segmenti e muxa la CAMM
                     (via CammMux) una volta sola sul risultato finale.

== DISTRIBUZIONE (incastro con mac-multi-hub) — leggi il suo README/Sources ==
- Integra tramite MacMultiHubKit: `HubClient`, protocolli `TaskHandler`
  (var taskType; func run(_:Data) async throws -> Data) e `JobPlan`
  (partition/assemble). Rispetta ESATTAMENTE quelle firme.
- Partizionamento: per SEGMENTI TEMPORALI allineati ai keyframe/GOP, con un
  piccolo OVERLAP di frame tra segmenti adiacenti (serve alla continuità del
  blend optical-flow ai bordi del segmento). Documenta come gestisci l'overlap
  in `assemble` (crossfade/discard dei frame di guardia).
- Movimento dati: NON spedire i video come `Data`. Il TaskDescriptor di un
  segmento porta RIFERIMENTI (quale cattura, quale range temporale/di frame, e
  dove stanno gli orig_N.mp4 su STORAGE CONDIVISO di rete — NAS/SMB). Ogni worker
  legge solo il range che gli serve. Rendi esplicito il requisito "storage
  condiviso raggiungibile da tutti i Mac" (c'è un repo NAS-organizer, tienine
  conto come possibile sorgente condivisa).
- La CAMM/GPS NON entra nei task dei worker: viene scritta una sola volta in
  `assemble` dal track RTK (CammMux), così le posizioni sono autorevoli dall'inizio
  e non c'è nessun passo di "sostituzione dopo".
- Fai sì che lo stesso StitchCore giri identico standalone (1 Mac) e distribuito
  (N Mac): la modalità distribuita è solo un orchestratore sopra lo stesso motore.

== INTEGRAZIONE street-view-suite ==
- Allineati ai suoi contratti esistenti per: formato del track RTK/GPX, CAMM,
  time-binding, e al modello HAL ("stitching dietro l'HAL"). Lo stitcher è
  l'implementazione "fai-da-te" dietro quell'HAL, alternativa all'SDK del vendor.
- Non duplicare la logica GNSS/tempo: riusa o rispecchia street-view-suite/core
  (gnss, export). Documenta il contratto condiviso.

== GPS: SOLO TEMPO REALE (ora), PORTA APERTA AL PPK ==
- Usa le posizioni RTK in tempo reale. NON implementare il PPK adesso (richiede
  dati di stazione base che al momento non abbiamo).
- Ma modella il GPS come una "traccia" astratta, così posizioni PPK-corrette
  potranno sostituire la sorgente in futuro senza cambiare la pipeline.

== CONSEGNE DI QUESTA PRIMA SESSIONE ==
1. Crea il repo privato e il package SwiftPM multi-target con lo scheletro dei
   moduli sopra (che compili: `swift build` verde, anche se gli stadi sono stub).
2. docs/ARCHITECTURE.md: pipeline, i confini dei moduli, la strategia di
   partizionamento/overlap, il contratto col TaskHandler/JobPlan di mac-multi-hub,
   il contratto CAMM/GNSS condiviso con street-view-suite.
3. docs/PROPRJ_RE.md: piano di reverse engineering di pro.prj (cosa guardare,
   come validare la calibrazione estratta) e l'interfaccia Calibration col mock.
4. Un percorso end-to-end FUNZIONANTE in forma minima: con una Calibration mock
   e un blend banale (anche solo feather/overlap semplice), produrre un
   equirettangolare + una CAMM valida da un set di test — così il "tubo" esiste e
   si migliora la qualità dopo.
5. Uno StitcherMMH che si registra come worker e accetta un task "stitch.segment"
   di prova (anche con StitchCore stub), dimostrando l'incastro con mac-multi-hub.
6. README con quadro, build/run, e stato (cosa è reale vs stub).

== VINCOLI ==
- Sviluppa su un branch dedicato e committa/pusha lì; NON toccare street-view-suite
  né mac-multi-hub (sono solo riferimento in lettura).
- Dichiara onestamente nel README cosa è reale e cosa è stub (soprattutto: la
  calibrazione reale dipende dal parsing di pro.prj, ancora da fare; la qualità
  delle cuciture è il lavoro grosso successivo).
- Non promettere la qualità di cucitura di Insta360: punta prima alla correttezza
  geometrica (sufficiente per Street View) e lascia il seam optical-flow come
  fase successiva.
```
