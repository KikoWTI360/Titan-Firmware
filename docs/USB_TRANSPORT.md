# Tablet ↔ Titan con il solo cavo USB (senza router né cavo Ethernet)

Obiettivo: il tablet comanda la Titan (OSC, stato, preview) attraverso un cavo
USB che parte dall'hub già presente sul rig, al posto di cavo Ethernet +
adattatore + router DHCP. Topologia del rig: `GNSS_BLACKBOX_HW.md` §3.1.

Stato: **ricognizione fatta** (09/11). Niente è stato ancora modificato sulla
camera.

## Ricognizione (09/11, `tools/titan_usb_recon.sh`, Mac collegato via USB)

- L4T **R28.2.1**, kernel 4.4.38. Il kernel ha tutte le funzioni USB che
  servono, compilate dentro: **ECM, NCM**, RNDIS, ACM (seriale), mass storage,
  FunctionFS (adb via USB).
- La porta funziona già: il gadget NVIDIA `l4t` è **collegato** alla porta
  (`3550000.xudc`), stato `configured`, a **super-speed (USB 3, 5 Gbit/s)** con
  il Mac. La nota di `device-recon.md` ("solo non-standard charger") non vale
  più: probabilmente era un cavo o un collegamento diverso.
- Il gadget offre **solo RNDIS** (`rndis.usb0`), che macOS non gestisce: per
  questo il Mac non vede nessuna rete.
- `host_addr` = `56:1b:b8:16:8e:bc`: **MAC locale** (bit `0x02`), quindi un
  host Linux/Android chiamerebbe l'interfaccia `usb0`. Trappola 2 confermata.
- `usb0` è nel bridge `l4tbr0` (`192.168.55.1/24`), già pronto.
- DHCP: gira un `dnsmasq.bin -d` (probabilmente quello di Insta360 per il Wi-Fi
  `192.168.43.x`); è disponibile `udhcpd` dentro busybox. Da capire se il
  dnsmasq serve anche `l4tbr0`.
- OSC (20000), file (8000), adb (5555) e ssh ascoltano su **`0.0.0.0`**:
  risponderanno anche sul collegamento USB senza toccare niente.
- Il MAC di `eth0` è `48:b0:2d:49:e7:b6` (non locale): da qui si ricava il MAC
  lato tablet.
- Due binari Insta360 contengono riferimenti al gadget: `udisk_test` (una
  "modalità disco USB"?) e `Image` (kernel). Da capire se qualcosa li lancia e
  riconfigura la porta.

## Cosa sappiamo già

Dalla camera (`device-recon.md`, 01/11):
- La porta USB OTG della Jetson TX2 **passa in modalità dispositivo** quando è
  collegata a un host (`tegra-xudc-new ... device mode on`), ma l'host vede solo
  un "non-standard charger": **nessuna funzione USB è configurata** su quella
  porta. L'hardware c'è, manca la configurazione.
- Esistono già `usb0` e il bridge `l4tbr0` con `192.168.55.1/24`: lo scheletro
  standard NVIDIA L4T per la "rete su USB" (in origine RNDIS), non collegato
  alla porta.
- Il server OSC (porta 20000) risponde su ogni interfaccia vista finora
  (Ethernet `192.168.8.194` / `192.168.1.5`, Wi-Fi `192.168.43.1`): da
  confermare che ascolti su `0.0.0.0`.

Dal tablet (street-view-suite, `docs/TITAN_INTEGRATION.md` §8, misurato su
Galaxy Tab S10 Lite 5G):
- un adattatore Ethernet USB sull'hub appare come **`ethernet · eth0`**
  (`TRANSPORT_ETHERNET`) e porta traffico vero;
- **Android crea la rete solo se l'interfaccia riceve un indirizzo via DHCP**:
  senza DHCP l'Ethernet sparisce dall'elenco, non appare nemmeno come guasta;
- il tablet **non può salvare un IP statico** (il pulsante Salva è disattivato);
- per questo il rig di oggi ha un router DHCP: la modalità "Direct" della Titan
  non distribuisce indirizzi.
- `CameraNet.kt` lega già i socket della camera a `TRANSPORT_ETHERNET` e tiene
  il traffico verso l'ufficio sulla SIM. **Se il collegamento USB appare come
  Ethernet, l'app non va cambiata**: cambia solo `base_url` nel profilo.

## Come funziona

```
tablet ──USB-C── hub ──USB── porta OTG della Titan
                              │
                              └─ gadget USB "scheda di rete" (CDC-ECM o NCM)
                                 + server DHCP sulla Titan: un indirizzo al
                                 tablet, nessun gateway, nessun DNS
tablet: eth0, 192.168.55.x  ──►  http://192.168.55.1:20000  (OSC)
```

La Titan si presenta al tablet come una **scheda di rete USB** (ruolo
"dispositivo", come quando un telefono fa tethering USB verso un PC) e gli
assegna un indirizzo con un piccolo server DHCP. Dal punto di vista del tablet
è identico all'adattatore Ethernet di oggi + router, senza i due oggetti in
mezzo.

Tre punti in cui si può sbagliare, tutti noti in anticipo:

1. **La classe USB.** CDC-ECM (o CDC-NCM) è lo standard: macOS e Linux la
   gestiscono senza driver, ed è quella che i kernel Android includono più
   spesso. RNDIS, che L4T usa di default, è il formato di Windows: macOS non lo
   gestisce e su Android spesso manca. Si parte da ECM, NCM come seconda prova.
2. **Il MAC decide il nome dell'interfaccia sul tablet.** Il driver Linux lato
   host chiama l'interfaccia `usbN` se il MAC è "amministrato localmente" (bit
   `0x02` del primo byte) e `ethN` altrimenti. Il servizio Ethernet di Android
   prende per default solo `eth\d`: con un MAC locale (quello casuale che i
   gadget usano di solito) **sul tablet non apparirebbe niente**. Il MAC lato
   tablet va quindi impostato non locale, derivato dal MAC di `eth0` della
   singola Titan (unico per unità).
3. **DHCP senza gateway né DNS.** Il link porta solo alla camera: se il DHCP
   annunciasse un gateway, Android proverebbe a usarlo per internet.
   `CameraNet.kt` lo gestisce comunque, ma è più pulito non annunciarlo.

E un controllo elettrico, già richiesto in `GNSS_BLACKBOX_HW.md` §3.1: in
modalità dispositivo la porta della Titan **non deve fornire VBUS** verso l'hub
(misurare con un tester prima di collegarla all'hub del rig).

## Piano (sempre sull'unità sacrificabile; backup già fatto il 01/11)

1. **Ricognizione, sola lettura** — `tools/titan_usb_recon.sh`: controller USB
   dispositivo, gadget esistenti, supporto ECM/NCM nel kernel, script L4T, chi
   altro usa la porta (Insta360 potrebbe usarla per una sua "modalità disco"),
   server DHCP disponibili, porte in ascolto.
2. **Prova temporanea col Mac** (si annulla riavviando la camera): gadget ECM
   collegato alla porta + DHCP. Il Mac deve ricevere `192.168.55.x` e
   rispondere a `python3 tools/titan_record.py 192.168.55.1 --stato`.
   Si usa il Mac per primo perché gestisce ECM senza driver e mostra subito
   cosa succede; adb resta disponibile via Ethernet durante la prova.
3. **Prova col tablet**: street-view-suite → Opzioni → Connessione → Network
   check deve mostrare `ethernet · eth0` con `192.168.55.x`. Poi una sessione e
   una ripresa con `base_url` = `http://192.168.55.1:20000`.
4. **Persistenza** (mod 03): la configurazione parte da sola all'avvio della
   camera, con copia di sicurezza dei file toccati e rollback documentato.
5. **Profilo SVS**: `base_url` della Titan a `192.168.55.1`, `transport`
   resta `ethernet`. Modifica in street-view-suite, a parte.

## Piano B (se il tablet non gestisce il gadget USB)

Stesso server DHCP, ma sulla **porta Ethernet** della Titan in modalità
Direct: tablet → hub con adattatore Ethernet → cavo → Titan. Sparisce il
router, restano cavo e adattatore. È la stessa metà del lavoro (il DHCP), quindi
non è lavoro perso.

## Note

- Velocità: la porta OTG della TX2 lavora in USB 2.0 nel caso peggiore
  (~200-300 Mbit/s reali con ECM). Basta: sul link passano stato, comandi e al
  più la preview; i file a piena risoluzione stanno sull'SSD.
- Se l'hub spegne le porte (tablet staccato o in sospensione) il link cade e la
  Titan continua a registrare; l'app si ricollega da sola (già gestito in SVS).
