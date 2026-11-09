#!/bin/sh
# ssd_flush.sh — scrive sull'SSD, ogni INTERVALLO secondi, i dati che la
# camera tiene ancora in memoria.
#
# Gira SULLA CAMERA, avviato da wti-titan.service (mod 03).
#
# Perché: la camera chiude bene i file a ogni Stop, ma Linux li riversa
# sull'SSD in ritardo. L'indice di ogni MP4 (il 'moov') viene scritto per
# ultimo, alla chiusura: staccando l'SSD troppo presto i file restano senza
# indice e non si aprono (09/11, VID_20261109_193727). Con questo ciclo, pochi
# secondi dopo lo Stop tutto è sul disco.
#
# Durante la registrazione non scrive dati in più: anticipa solo le scritture
# che Linux farebbe comunque più tardi.

MNT=${MNT:-/mnt/udisk1}
INTERVAL=${INTERVAL:-5}

# 'sync -f' scrive solo il filesystem dell'SSD; i coreutils più vecchi non lo
# hanno e allora si usa 'sync', che scrive tutto.
if sync -f / 2>/dev/null; then
    flush() { sync -f "$MNT"; }
    how="sync -f $MNT"
else
    flush() { sync; }
    how="sync"
fi
echo "ssd_flush: ogni ${INTERVAL} s, '$how', quando $MNT è montato"

while :; do
    if grep -q " $MNT " /proc/mounts; then
        flush
    fi
    sleep "$INTERVAL"
done
