#!/usr/bin/env bash
# install.sh — installa la mod 03 sulla camera collegata via adb (in RETE).
#
#   mods/03-usb-ssd/install.sh            installa, attiva all'avvio e avvia subito
#   mods/03-usb-ssd/install.sh --rimuovi  toglie tutto e rimette la porta USB in RNDIS
#   mods/03-usb-ssd/install.sh --stato    mostra se è attiva e cosa sta facendo
#
# Cosa mette sulla camera (solo file nuovi, niente di Insta360 viene toccato):
#   /opt/wti/titan_usbnet.sh               (da tools/) porta USB in ECM, MAC per Android
#   /opt/wti/ssd_flush.py                  scrittura sull'SSD ogni 5 s e "SSD PRONTO"
#   /etc/systemd/system/wti-titan.service  li avvia a ogni accensione
# /opt/wti non è tra le cartelle che un aggiornamento Insta360 riscrive
# (bill.list: /usr/local/bin e /home/nvidia/insta360/etc).
#
# adb deve passare dalla rete (Ethernet o Wi-Fi della camera): durante
# l'attivazione la porta USB si scollega per qualche secondo.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
UNIT=wti-titan.service
# sorgente sul Mac -> destinazione sulla camera
FILES=(
    "$ROOT/tools/titan_usbnet.sh:/opt/wti/titan_usbnet.sh"
    "$HERE/ssd_flush.py:/opt/wti/ssd_flush.py"
    "$HERE/$UNIT:/etc/systemd/system/$UNIT"
)

md5_local() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
md5_dev() { adb shell "md5sum $1 2>/dev/null" | cut -d' ' -f1 | tr -d '\r'; }
dev() { adb shell "$1" | tr -d '\r'; }
fail() { echo "ERRORE: $*" >&2; exit 1; }

SERIAL=$(adb get-serialno 2>/dev/null | tr -d '\r' || true)
[ -n "$SERIAL" ] && [ "$SERIAL" != "unknown" ] || fail "nessuna camera collegata via adb (adb connect IP:5555)"

status() {
    echo "== Servizio $UNIT: $(dev "systemctl is-enabled $UNIT 2>/dev/null" | tail -1) all'avvio, $(dev "systemctl is-active $UNIT 2>/dev/null" | tail -1) adesso"
    dev "journalctl -u $UNIT -n 12 --no-pager 2>/dev/null" | sed 's/^/   /'
    dev "[ -f /opt/wti/titan_usbnet.sh ] && sh /opt/wti/titan_usbnet.sh stato"
}

case "${1:-}" in
    --stato)
        status
        exit 0 ;;
    --rimuovi)
        echo "== Tolgo la mod 03 dalla camera $SERIAL"
        dev "systemctl disable --now $UNIT 2>&1; rm -f /etc/systemd/system/$UNIT; systemctl daemon-reload; [ -f /opt/wti/titan_usbnet.sh ] && sh /opt/wti/titan_usbnet.sh rndis; rm -rf /opt/wti"
        [ -z "$(dev "ls /etc/systemd/system/$UNIT /opt/wti 2>/dev/null")" ] || fail "qualcosa è rimasto: controlla con --stato"
        echo "Fatto: niente più ECM all'avvio, porta USB in RNDIS come da NVIDIA."
        exit 0 ;;
    "") ;;
    *) fail "opzione sconosciuta: $1 (usa --stato o --rimuovi)" ;;
esac

echo "== 1/4 Carico i file sulla camera $SERIAL e li verifico"
for pair in "${FILES[@]}"; do
    src=${pair%%:*}
    [ -f "$src" ] || fail "manca $src"
    tmp=/tmp/wti.$(basename "$src")
    adb push "$src" "$tmp" >/dev/null
    [ "$(md5_local "$src")" = "$(md5_dev "$tmp")" ] || fail "trasferimento di $(basename "$src") corrotto, non ho installato niente"
done

echo "== 2/4 Installo in /opt/wti e $UNIT"
cmd="mkdir -p /opt/wti && rm -f /opt/wti/ssd_flush.sh"
for pair in "${FILES[@]}"; do
    src=${pair%%:*}; dst=${pair#*:}
    mode=755; case "$dst" in *.service) mode=644 ;; esac
    cmd="$cmd && cp /tmp/wti.$(basename "$src") $dst.new && chmod $mode $dst.new && mv $dst.new $dst"
done
dev "$cmd && rm -f /tmp/wti.* && echo ok" | grep -qx ok || fail "copia sulla camera non riuscita (filesystem in sola lettura?)"
for pair in "${FILES[@]}"; do
    src=${pair%%:*}; dst=${pair#*:}
    [ "$(md5_local "$src")" = "$(md5_dev "$dst")" ] || fail "$dst sulla camera non corrisponde"
done

echo "== 3/4 Attivo il servizio all'avvio e lo avvio adesso"
dev "systemctl daemon-reload && systemctl enable $UNIT 2>&1 && systemctl restart $UNIT" | sed 's/^/   /'

echo "== 4/4 Controllo"
sleep 6
state=$(dev "systemctl is-active $UNIT 2>/dev/null" | tail -1)
status
[ "$state" = active ] || fail "il servizio non è attivo ($state): guarda il registro qui sopra; per tornare indietro: $0 --rimuovi"
echo
echo "Fatto: porta USB in ECM e scrittura sull'SSD, anche dopo ogni riavvio."
echo "Dopo ogni Stop: stacca l'SSD solo quando il registro dice 'SSD PRONTO' ($0 --stato)."
echo "Rimozione: $0 --rimuovi"
