# mods/

I **nostri** cambiamenti allo strato applicativo, in forma **testuale** e
**versionata**. A differenza di `extracted/` (rigenerabile, gitignored), qui
teniamo solo ciò che abbiamo modificato noi, così il diff è leggibile e la
cronologia git è utile.

## Convenzione

Una cartella numerata per mod. In git vanno **solo** la descrizione e i nostri
script, non le copie dei file del firmware (sono contenuto proprietario):

```
mods/
└── NN-<nome-mod>/
    ├── README.md        # obiettivo, modifica esatta, rischi, procedura, rollback, registro test
    ├── <script>.py      # nostro script che applica la modifica in modo ripetibile
    └── work/            # (gitignored) copie originali e modificate dei file del firmware
```

La modifica si descrive come ricetta (file, chiave, valore prima → dopo) e la
applica uno script, così è ripetibile su ogni unità della flotta.

## Regole

- **Solo file testuali**: JSON di `cfg/`, script `.sh`, Python di `bin/web_server/`.
  I patch a binari ELF vanno fatti **solo se davvero necessario** e documentati a
  parte (offset, byte prima/dopo, motivo).
- **Mai** committare qui il blob DSP (`sys_dsp_rom.devfw`) né binari pesanti.
- Ogni mod ha un `README.md` che spiega: problema, modifica, rischio, come
  applicarla in place sul device, come verificarla, come fare rollback.
- Testa ogni mod su **una sola unità sacrificabile** prima della flotta.

## Mod

| # | Mod | Stato |
|---|-----|-------|
| 01 | [Bitrate di registrazione GSV](01-bitrate-gsv/README.md) | Pronto da provare su unità sacrificabile |
