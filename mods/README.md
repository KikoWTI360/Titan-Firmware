# mods/

I **nostri** cambiamenti allo strato applicativo, in forma **testuale** e
**versionata**. A differenza di `extracted/` (rigenerabile, gitignored), qui
teniamo solo ciò che abbiamo modificato noi, così il diff è leggibile e la
cronologia git è utile.

## Convenzione

Una cartella per mod, che rispecchia il percorso dentro `titan_update/`:

```
mods/
└── <nome-mod>/
    ├── README.md                 # cosa fa, perché, come applicarlo, come testarlo
    ├── original/                 # copia ESATTA del/dei file originali (riferimento)
    │   └── cfg/takevideo_template.json
    └── patched/                  # la nostra versione modificata
        └── cfg/takevideo_template.json
```

In alternativa, per modifiche piccole, un singolo file `.diff`/`.patch`.

## Regole

- **Solo file testuali**: JSON di `cfg/`, script `.sh`, Python di `bin/web_server/`.
  I patch a binari ELF vanno fatti **solo se davvero necessario** e documentati a
  parte (offset, byte prima/dopo, motivo).
- **Mai** committare qui il blob DSP (`sys_dsp_rom.devfw`) né binari pesanti.
- Ogni mod ha un `README.md` che spiega: problema, modifica, rischio, come
  applicarla in place sul device, come verificarla, come fare rollback.
- Testa ogni mod su **una sola unità sacrificabile** prima della flotta.

## Mod pianificati / da fare

_(Da compilare quando definiamo le funzioni/bug — es. limite di bitrate, nuove
modalità di ripresa, comportamento OSC, timelapse, ecc.)_
