# original-firmware/

Metti **qui** il pacchetto di update originale, es.:

```
original-firmware/Insta360_Titan_Update.bin
```

(va bene anche la variante `Insta360_Titan_Update_vX.Y.Z.zip`.)

Il contenuto di questa cartella è **gitignored**: è un binario proprietario
Insta360 e **non va versionato né ridistribuito**. Resta solo sulla tua
macchina. Lo script `tools/extract_firmware.py` lo legge da qui e ricostruisce
l'albero in `extracted/`.
