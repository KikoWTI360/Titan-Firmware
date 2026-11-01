#!/usr/bin/env bash
# backup_titan.sh — backup della Titan collegata via adb, verso il Mac.
#
# Salva in <destinazione>/titan-backup-<camera>-<data>/:
#   info/               versioni, partizioni, mount, processi, MD5 dei binari
#   applayer.tar        /usr/local, /home/nvidia/insta360, /etc: il ripristino
#                       pratico dei file che modifichiamo
#   gpt_inizio.img      primo MiB dell'eMMC (tabella delle partizioni)
#   gpt_fine.img        ultimo MiB dell'eMMC (copia di riserva della tabella)
#   mmcblk0boot0/1.img  primo stadio di avvio
#   mmcblk0p2...pN.img  tutte le partizioni tranne il sistema (bootloader, kernel,
#                       DTB, ...): ~1 GB, quello che serve contro i brick
#   mmcblk0p1.img       SOLO con --con-sistema: la partizione di sistema (~28 GB)
#   MD5SUMS.txt
#
# Come copia: ogni pezzo viene preparato in /tmp sulla camera, scaricato con
# `adb pull` (il canale file di adb, sicuro per i dati binari) e verificato
# confrontando l'MD5 calcolato sulla camera con quello del file ricevuto; poi
# viene cancellato dalla camera. Al primo errore lo script si ferma e dice dove.
# (`adb exec-out`, che permetterebbe lo streaming, su questa camera non è
# supportato: risponde "error: closed".)
#
# La partizione di sistema (--con-sistema) non sta in /tmp: viene trasmessa in
# streaming con `adb shell`, solo se il canale si dimostra fedele ai dati
# binari, e verificata con l'MD5 del flusso calcolato sulla camera. È presa a
# camera accesa: è "come dopo uno spegnimento brusco".
#
# Uso:  tools/backup_titan.sh <cartella-destinazione> [--con-sistema]
set -euo pipefail

STAGE=/tmp/titan_bk
trap 'echo "ERRORE: comando fallito alla riga $LINENO di $0" >&2' ERR
# A ogni uscita, anche per errore, toglie i file temporanei dalla camera.
trap 'adb shell "rm -rf $STAGE /tmp/bk.fifo /tmp/bk.md5" >/dev/null 2>&1 || true' EXIT
fail() { echo "ERRORE: $*" >&2; exit 1; }
md5_local() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
dev() { adb shell "$1" | tr -d '\r'; }
# Le informazioni sono un di più: un comando che fallisce non ferma il backup.
info() { adb shell "$1" 2>&1 | tr -d '\r' > "$DEST/info/$2" || true; }

DEST_BASE="${1:-}"
[ -n "$DEST_BASE" ] || fail "uso: $0 <cartella-destinazione> [--con-sistema]"
SYSTEM=0
[ "${2:-}" = "--con-sistema" ] && SYSTEM=1

SERIAL=$(adb get-serialno | tr -d '\r')
[ -n "$SERIAL" ] && [ "$SERIAL" != "unknown" ] || fail "nessuna camera collegata via adb"
[ "$(dev 'id -u')" = "0" ] || fail "adb non è root sulla camera"
TAG=$(printf '%s' "$SERIAL" | tr ':/' '__')
DEST="$DEST_BASE/titan-backup-$TAG-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST/info"

# Partizioni dell'eMMC tranne il sistema (p1), in ordine numerico.
PARTS=$(dev 'ls /sys/class/block' | grep -E '^mmcblk0p[0-9]+$' | grep -vx 'mmcblk0p1' | sort -t p -k 2 -n)
[ -n "$PARTS" ] || fail "non trovo le partizioni dell'eMMC"
SECTORS=$(dev 'cat /sys/class/block/mmcblk0/size')

# Spazio: ~2 GB per il backup base, più la partizione di sistema se richiesta.
need_kb=2097152
[ $SYSTEM = 1 ] && need_kb=$(( need_kb + $(dev 'cat /sys/class/block/mmcblk0p1/size') / 2 ))
free_kb=$(df -Pk "$DEST" | awk 'NR==2{print $4}')
[ "$free_kb" -ge "$need_kb" ] || fail "spazio insufficiente in $DEST_BASE: servono ~$((need_kb / 1048576 + 1)) GB, liberi $((free_kb / 1048576)) GB."

echo "== Backup della camera $SERIAL in $DEST"
echo "== Informazioni"
info 'uname -a; cat /home/nvidia/insta360/etc/default.prop' versione.txt
info 'cat /proc/partitions; ls -l /dev/disk/by-partlabel' partizioni.txt
info 'cat /proc/mounts; df -h' mount.txt
info 'ps -ef' processi.txt
info 'md5sum /usr/local/bin/*' md5_usr_local_bin.txt

record() {  # record <nome> <md5 camera>: verifica e annota un file ricevuto
    local sum_mac
    sum_mac=$(md5_local "$DEST/$1")
    [ "$2" = "$sum_mac" ] || fail "$1: MD5 diverso (camera '$2', Mac '$sum_mac'): copia non valida"
    echo "$sum_mac  $1" >> "$DEST/MD5SUMS.txt"
    echo "   ok: $(du -h "$DEST/$1" | cut -f1), md5 $sum_mac"
}

# Prepara un file in $STAGE sulla camera, lo scarica con adb pull e verifica l'MD5.
grab() {
    local name="$1" cmd="$2" sum_dev
    echo "== $name"
    sum_dev=$(dev "mkdir -p $STAGE && $cmd >/dev/null 2>&1; md5sum $STAGE/$name 2>/dev/null" | cut -d' ' -f1) || true
    [ -n "$sum_dev" ] || fail "$name: non sono riuscito a prepararlo sulla camera"
    adb pull "$STAGE/$name" "$DEST/$name" >/dev/null || fail "$name: copia con adb pull fallita"
    dev "rm -f $STAGE/$name" >/dev/null || true
    record "$name" "$sum_dev"
}

grab applayer.tar "tar -cf $STAGE/applayer.tar /usr/local /home/nvidia/insta360 /etc"
grab gpt_inizio.img "dd if=/dev/mmcblk0 of=$STAGE/gpt_inizio.img bs=512 count=2048"
grab gpt_fine.img "dd if=/dev/mmcblk0 of=$STAGE/gpt_fine.img bs=512 skip=$((SECTORS - 2048)) count=2048"
for b in mmcblk0boot0 mmcblk0boot1; do
    grab "$b.img" "dd if=/dev/$b of=$STAGE/$b.img bs=1M"
done
for p in $PARTS; do
    grab "$p.img" "dd if=/dev/$p of=$STAGE/$p.img bs=1M"
done

if [ $SYSTEM = 1 ]; then
    echo "== mmcblk0p1.img (sistema, ~28 GB: via cavo ci vogliono minuti, in Wi-Fi molto di più)"
    # Il canale adb shell deve passare i byte senza alterarli (per esempio \n -> \r\n).
    probe=$(adb shell 'printf "A\nB\r\000\377"' | od -An -tx1 | tr -d ' \n')
    [ "$probe" = "410a420d00ff" ] \
        || fail "su questa camera adb shell altera i dati binari (letto $probe): la partizione di sistema va copiata in un altro modo (SSH). Il resto del backup è completo."
    adb shell "exec 2>/dev/null; rm -f /tmp/bk.fifo /tmp/bk.md5; mkfifo /tmp/bk.fifo; md5sum < /tmp/bk.fifo > /tmp/bk.md5 & dd if=/dev/mmcblk0p1 bs=4M | tee /tmp/bk.fifo; wait" > "$DEST/mmcblk0p1.img" \
        || fail "mmcblk0p1.img: trasferimento interrotto"
    record mmcblk0p1.img "$(dev 'cat /tmp/bk.md5' | cut -d' ' -f1 || true)"
fi

echo
echo "Backup completo e verificato in: $DEST"
cat "$DEST/MD5SUMS.txt"
