# extracted/

Cartella di lavoro **rigenerabile**. Qui `tools/extract_firmware.py` ricostruisce
l'albero del pacchetto:

```
extracted/titan_update/
├── bin/        -> /usr/local/bin           (ELF, web_server/, *.sh, Image)
├── cfg/        -> /home/nvidia/insta360/etc (JSON di ripresa, init.rc, default.prop)
├── data/       -> /home/nvidia/insta360
├── lib/        -> /usr/local/lib
├── firmware/   -> /usr/local/lib/firmware  (sys_dsp_rom.devfw — NON toccare)
├── wav/
└── bill.list   (manifest cartella -> percorso device)
```

Il contenuto è **gitignored** (binari proprietari). Rigeneralo con:

```bash
python3 tools/extract_firmware.py original-firmware/Insta360_Titan_Update.bin
```
