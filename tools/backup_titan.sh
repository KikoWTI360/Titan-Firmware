#!/usr/bin/env bash
# backup_titan.sh — backup completo della Titan collegata via adb, verso il Mac.
#
# Salva in <destinazione>/titan-backup-<camera>-<data>/:
#   info/              versioni, partizioni, mount, processi, MD5 dei binari
#   applayer.tar       /usr/local, /home/nvidia/insta360, /etc: il ripristino
#                      pratico dei file che modifichiamo
#   mmcblk0boot0.img   partizioni di boot dell'eMMC (primo stadio di avvio)
#   mmcblk0boot1.img
#   mmcblk0.img        l'intera eMMC (~29 GB): la rete di sicurezza totale
#   MD5SUMS.txt
#
# Ogni flusso è verificato: la camera calcola l'MD5 degli stessi byte che invia
# e lo script lo confronta con l'MD5 del file ricevuto. Al primo errore si ferma.
# La partizione di sistema è in uso durante la copia, quindi mmcblk0.img è
# "come dopo uno spegnimento brusco": per i file che modifichiamo fa fede
# applayer.tar, che è coerente.
#
# Uso:  tools/backup_titan.sh <cartella-destinazione> [--senza-immagine]
#       --senza-immagine salta mmcblk0.img (che richiede ~30 GB liberi)
set -euo pipefail

trap 'echo "ERRORE: comando fallito alla riga $LINENO di $0" >&2' ERR
# A ogni uscita, anche per errore, toglie i file temporanei dalla camera.
trap 'adb shell "rm -f /tmp/bk.fifo /tmp/bk.md5" >/dev/null 2>&1 || true' EXIT
fail() { echo "ERRORE: $*" >&2; exit 1; }
md5_local() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
dev() { adb shell "$1" | tr -d '\r'; }
# Le informazioni sono un di più: un comando che fallisce non ferma il backup.
info() { adb shell "$1" 2>&1 | tr -d '\r' > "$DEST/info/$2" || true; }

DEST_BASE="${1:-}"
[ -n "$DEST_BASE" ] || fail "uso: $0 <cartella-destinazione> [--senza-immagine]"
FULL=1
[ "${2:-}" = "--senza-immagine" ] && FULL=0

SERIAL=$(adb get-serialno | tr -d '\r')
[ -n "$SERIAL" ] && [ "$SERIAL" != "unknown" ] || fail "nessuna camera collegata via adb"
[ "$(dev 'id -u')" = "0" ] || fail "adb non è root sulla camera"
TAG=$(printf '%s' "$SERIAL" | tr ':/' '__')
DEST="$DEST_BASE/titan-backup-$TAG-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST/info"

if [ $FULL = 1 ]; then
    # /sys/.../size è in settori da 512 byte; +1 GB di margine
    need_kb=$(( $(dev 'cat /sys/class/block/mmcblk0/size') / 2 + 1048576 ))
    free_kb=$(df -Pk "$DEST" | awk 'NR==2{print $4}')
    [ "$free_kb" -ge "$need_kb" ] || fail "spazio insufficiente in $DEST_BASE: servono ~$((need_kb / 1048576)) GB, liberi $((free_kb / 1048576)) GB. Usa un disco esterno, oppure --senza-immagine."
fi

echo "== Backup della camera $SERIAL in $DEST"
echo "== Informazioni"
info 'uname -a; cat /home/nvidia/insta360/etc/default.prop' versione.txt
info 'cat /proc/partitions; ls -l /dev/disk/by-partlabel' partizioni.txt
info 'cat /proc/mounts; df -h' mount.txt
info 'ps -ef' processi.txt
info 'md5sum /usr/local/bin/*' md5_usr_local_bin.txt

# Copia sul Mac l'output di un comando della camera e verifica che sia arrivato
# intatto: sulla camera tee manda gli stessi byte anche a md5sum tramite una fifo.
stream() {
    local name="$1" cmd="$2" out="$DEST/$1" sum_dev sum_mac
    echo "== $name"
    adb exec-out "exec 2>/dev/null; rm -f /tmp/bk.fifo /tmp/bk.md5; mkfifo /tmp/bk.fifo; md5sum < /tmp/bk.fifo > /tmp/bk.md5 & $cmd | tee /tmp/bk.fifo; wait" > "$out" \
        || fail "$name: trasferimento interrotto (connessione adb?)"
    sum_dev=$(dev 'cat /tmp/bk.md5' | cut -d' ' -f1) || true
    sum_mac=$(md5_local "$out")
    [ -n "$sum_dev" ] && [ "$sum_dev" = "$sum_mac" ] \
        || fail "$name: MD5 diverso (camera '$sum_dev', Mac '$sum_mac'): trasferimento non valido"
    echo "$sum_mac  $name" >> "$DEST/MD5SUMS.txt"
    echo "   ok: $(du -h "$out" | cut -f1), md5 $sum_mac"
}

stream applayer.tar "tar -cf - /usr/local /home/nvidia/insta360 /etc"
stream mmcblk0boot0.img "dd if=/dev/mmcblk0boot0 bs=1M"
stream mmcblk0boot1.img "dd if=/dev/mmcblk0boot1 bs=1M"
if [ $FULL = 1 ]; then
    echo "   (l'eMMC intera è ~29 GB: via cavo Ethernet ci vogliono minuti, in Wi-Fi molto di più)"
    stream mmcblk0.img "dd if=/dev/mmcblk0 bs=4M"
fi

echo
echo "Backup completo e verificato in: $DEST"
cat "$DEST/MD5SUMS.txt"
