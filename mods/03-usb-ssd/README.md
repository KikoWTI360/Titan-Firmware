# Mod 03 — Rete USB verso il tablet e SSD sempre scritto

**Stato:** installata sulla camera di test e attiva a ogni avvio (09/11);
manca la prova "Stop, 10 s, SSD staccato". Le due parti sono
già state provate a mano: la rete USB (`tools/titan_usbnet.sh ecm`, Mac e
tablet, 09/11) e il problema che la scrittura periodica risolve (file senza
indice se l'SSD si stacca presto, 09/11). Installazione e avvio automatico
provati solo su una camera simulata.

## Obiettivo

Rendere permanente, a ogni accensione, quello che serve al rig "Titan SSD"
(profilo street-view-suite `titan_ssd`):

1. **Porta USB come scheda di rete standard (CDC-ECM)** con un MAC che Android
   chiama `eth`: il tablet raggiunge la camera su `192.168.55.1` con il solo
   cavo USB, senza router né cavo Ethernet. Il DHCP c'è già (dnsmasq di
   Insta360, `192.168.55.10-30`). Dettagli: `docs/USB_TRANSPORT.md`.
2. **Scrittura sull'SSD ogni 5 secondi e "SSD PRONTO".** La camera chiude
   bene i file a ogni Stop, ma Linux li riversa sull'SSD in ritardo, e
   l'indice di ogni MP4 è l'ultima cosa scritta: staccando l'SSD troppo presto
   i file non si aprono (09/11, `VID_20261109_193727`, tutti senza `moov`).
   Una semplice attesa non basta: con la sola scrittura ogni 5 s e l'SSD
   staccato 10 s dopo lo Stop, 7 file su 8 erano completi (09/11,
   `VID_20261109_202301`, `origin_3` senza indice), perché i file non si
   chiudono tutti insieme. Quindi `ssd_flush.py` controlla la **struttura**
   degli 8 `origin_N.mp4` della ripresa più recente: quando sono tutti MP4
   chiusi (indice compreso) fa l'ultima scrittura e annota nel registro
   **"SSD PRONTO"** con l'ora. Da quel momento l'SSD si può staccare.

## Cosa installa (solo file nuovi)

| file sulla camera | da | cosa fa |
|---|---|---|
| `/opt/wti/titan_usbnet.sh` | `tools/titan_usbnet.sh` | porta USB in ECM, MAC per Android, nel bridge `l4tbr0` |
| `/opt/wti/ssd_flush.py` | questa cartella | `sync -f /mnt/udisk1` ogni 5 s; "SSD PRONTO" quando gli 8 file della ripresa sono chiusi (Python di Insta360, 3.6) |
| `/etc/systemd/system/wti-titan.service` | questa cartella | li avvia a ogni accensione, dopo il servizio USB di NVIDIA |

Nessun file Insta360 o NVIDIA viene modificato. `/opt/wti` non è tra le
cartelle che un aggiornamento firmware riscrive.

## Installazione

Prerequisiti: adb come root **in rete** (Ethernet o Wi-Fi della camera), backup
già fatto (`docs/root-access-and-backup.md`).

```bash
mods/03-usb-ssd/install.sh
```

Carica i tre file, verifica ognuno con l'MD5, attiva il servizio all'avvio, lo
avvia e controlla che sia attivo. Stato in qualunque momento:

```bash
mods/03-usb-ssd/install.sh --stato
```

## Verifica

1. Riavvia la camera: dopo l'avvio `install.sh --stato` deve dire `active` e
   la porta USB `ecm.usb0`.
2. Tablet sul cavo USB: Network check → `ethernet · eth… · 192.168.55.x`.
3. Ripresa dall'app (profilo `titan_ssd`), Stop, poi
   `mods/03-usb-ssd/install.sh --stato` finché il registro dice
   **"SSD PRONTO: VID_..."**; solo allora stacca l'SSD. Sul Mac i file devono
   avere l'indice:
   `python3 tools/elf_probe.py file <origin_1.mp4>` → `Video: 5280x2972 ...`,
   nessun "Verdetto: MP4 SENZA 'moov'".
4. Prova di bitrate all'aperto con la mod attiva (`tools/titan_bitrate_test.py`):
   la scrittura ogni 5 s non deve far perdere fotogrammi.

## Rollback

```bash
mods/03-usb-ssd/install.sh --rimuovi
```

Ferma e toglie il servizio, cancella `/opt/wti` e rimette la porta in RNDIS.
In alternativa: `adb shell systemctl disable --now wti-titan.service` e riavvio
(la configurazione NVIDIA originale torna da sola all'avvio).

## Rischi

- **Prestazioni durante la registrazione:** `sync` ogni 5 s non aggiunge dati,
  anticipa solo scritture che Linux farebbe comunque; da confermare con la
  prova di bitrate (fotogrammi persi = 0).
- **Il Mac collegato via USB** riceve dal DHCP di Insta360 anche gateway e DNS
  e può provare a usare la camera per internet (vedi `docs/USB_TRANSPORT.md`).
  Per il tablet non è un problema.
- **Un aggiornamento firmware** non tocca `/opt/wti` né il servizio; se però
  cambiasse lo script USB di NVIDIA, il servizio riproverebbe ogni 5 s senza
  danni: `--stato` lo mostra.

## Registro test

| Data | Unità | Cosa | Esito / note |
|------|-------|------|--------------|
| 09/11 | camera di test | `titan_usbnet.sh ecm` a mano: Mac (DHCP + OSC) e tablet (SVS "CAMERA CONNECTED") | riuscito |
| 09/11 | camera di test | ripresa dall'app, SSD staccato presto | file senza indice: motivo della scrittura periodica |
| 09/11 | simulazione (adb e systemctl finti) | `install.sh`, `--rimuovi`, servizio non attivo | riuscito; da provare sulla camera |
| 09/11 | camera di test | ripresa ~55 s dall'app, Stop, SSD staccato dopo 10 s (versione con solo `sync` ogni 5 s) | 7 file su 8 completi, `origin_3` senza indice: attesa fissa insufficiente → controllo strutturale e "SSD PRONTO" |
| 09/11 | simulazione (tmpfs, camera finta) | `ssd_flush.py`: chiusura sfalsata di 4 s; una lente che non chiude | "SSD PRONTO" 1 s dopo l'ultima chiusura, mai prima; lente mancante segnalata per nome |
| 09/11 | camera di test | `install.sh` + riavvio | **riuscito**: servizio `enabled`/`active`, partito 5 s dopo l'avvio; porta in `ecm.usb0` a super-speed (interfaccia `usb1` nel bridge), `ssd_flush` con `sync -f /mnt/udisk1`; il tablet ha ricevuto `192.168.55.12` un secondo dopo. Da fare: ripresa + Stop + 10 s + SSD staccato |
