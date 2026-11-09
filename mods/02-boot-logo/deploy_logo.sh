#!/usr/bin/env bash
# deploy_logo.sh — applica il logo WTI360 alla camera collegata via adb.
#
# Usa il bootanimation PRESO DALLA CAMERA, non quello del pacchetto: così vale per
# qualunque versione firmware e non si mette mai sul device un binario di un'altra
# build. Ogni passaggio è verificato con l'MD5 e la sostituzione finale è un
# rename atomico. Non riavvia la camera: lo fai tu dopo aver letto l'esito.
#
# Sostituisce due immagini del bootanimation:
#   bootLogo  128x64, la schermata d'avvio ("Insta360 Titan")  ← LOGO_FULL
#   prologo   94x32, il logo "Pro" ereditato dalla Insta360 Pro ← LOGO_SMALL
#
# Uso:  mods/02-boot-logo/deploy_logo.sh
#       Default: logo_wti360_128x64.png e logo_wti360_94x32.png in questa cartella.
#       Altra immagine: LOGO_FULL=/percorso/logo.png mods/02-boot-logo/deploy_logo.sh
#       Codifica dei pixel: ENC=page-lsb (default, verificata sulla Titan).
#
# Versione sul logo: in basso a destra, piccola, la versione del firmware
# LETTA DALLA CAMERA al momento del deploy (la stessa che la camera dichiara
# all'app), così all'avvio si vede quale firmware gira. Serve adb in rete
# (IP:5555) e l'app del tablet chiusa (la camera accetta un solo client).
#       Testo a scelta:  TESTO="FW 1.2.23 WTI" mods/02-boot-logo/deploy_logo.sh
#       Nessun testo:    TESTO= mods/02-boot-logo/deploy_logo.sh
# Se un aggiornamento firmware sostituisce bootanimation torna il logo Insta360:
# anche quello è un segnale visivo (il mod va riapplicato).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOGO_FULL="${LOGO_FULL:-$HERE/logo_wti360_128x64.png}"
LOGO_SMALL="${LOGO_SMALL:-$HERE/logo_wti360_94x32.png}"
ENC="${ENC:-page-lsb}"
TESTO="${TESTO-auto}"
DEV=/usr/local/bin/bootanimation
WORK="$HERE/work"
mkdir -p "$WORK"

md5_local() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
md5_dev() { adb shell "md5sum $1 2>/dev/null" | cut -d' ' -f1 | tr -d '\r'; }
fail() { echo "ERRORE: $*" >&2; exit 1; }

SERIAL=$(adb get-serialno | tr -d '\r')
[ -n "$SERIAL" ] && [ "$SERIAL" != "unknown" ] || fail "nessuna camera collegata via adb"
# Collegata in rete il seriale è "IP:5555": niente ':' o '/' nei nomi dei file.
TAG=$(printf '%s' "$SERIAL" | tr ':/' '__')
ORIG="$WORK/bootanimation.$TAG.orig"
NEW="$WORK/bootanimation.$TAG.wti"

if [ "$TESTO" = auto ]; then
    case "$SERIAL" in
        *:*) IP=${SERIAL%%:*} ;;
        *) fail "per leggere la versione serve adb in rete (IP:5555). Oppure: TESTO=\"FW x.y.z\" (o TESTO= per nessun testo)" ;;
    esac
    echo "== 0/4 Leggo la versione del firmware dalla camera $IP"
    VER=$(python3 "$HERE/../../tools/titan_record.py" "$IP" --stato 2>&1 \
          | sed -n 's/.*, firmware \([^,]*\).*/\1/p' | head -1 | tr -d '\r ')
    [ -n "$VER" ] && [ "$VER" != "?" ] || fail "versione non letta (app del tablet aperta? la camera accetta un solo client). Oppure: TESTO=\"FW x.y.z\" (o TESTO= per nessun testo)"
    TESTO="FW $VER"
fi
TEXT_ARGS=()
[ -n "$TESTO" ] && TEXT_ARGS=(--testo "$TESTO")
[ -n "$TESTO" ] && echo "   testo sul logo: $TESTO"

# Se il mod è già stato applicato, l'originale vero è il .orig sulla camera.
SRC=$DEV
if [ -n "$(adb shell "[ -f $DEV.orig ] && echo si" | tr -d '\r')" ]; then
    SRC=$DEV.orig
    echo "La camera ha già $DEV.orig: parto da quello (l'originale)."
fi

echo "== 1/4 Copio $SRC dalla camera $SERIAL"
adb pull "$SRC" "$ORIG" >/dev/null
[ "$(md5_local "$ORIG")" = "$(md5_dev $SRC)" ] || fail "copia dalla camera corrotta"

echo "== 2/4 Creo la versione coi loghi"
# ${arr[@]+...}: con set -u il bash 3.2 del Mac rifiuta un array vuoto.
python3 "$HERE/logo_tool.py" patch "$ORIG" "$LOGO_FULL" --symbol bootLogo --size 128x64 \
    --encoding "$ENC" --out "$NEW.tmp" ${TEXT_ARGS[@]+"${TEXT_ARGS[@]}"} \
    | sed -n '/^Testo sul logo/p;/^Avviso/p;/^Nuovo logo/,/^$/p;/^Byte riscritti/p'
python3 "$HERE/logo_tool.py" patch "$NEW.tmp" "$LOGO_SMALL" \
    --encoding "$ENC" --out "$NEW" | sed -n '/^Byte riscritti/p'
rm -f "$NEW.tmp"

echo "== 3/4 Carico la nuova versione sulla camera e verifico"
adb push "$NEW" /tmp/bootanimation.wti >/dev/null
[ "$(md5_local "$NEW")" = "$(md5_dev /tmp/bootanimation.wti)" ] || fail "trasferimento corrotto, non ho toccato niente"

echo "== 4/4 Sostituisco (l'originale resta in $DEV.orig)"
adb shell "[ -f $DEV.orig ] || cp -a $DEV $DEV.orig; cp /tmp/bootanimation.wti $DEV.new && chmod 755 $DEV.new && mv $DEV.new $DEV" \
    | tr -d '\r'
if [ "$(md5_local "$NEW")" != "$(md5_dev $DEV)" ]; then
    fail "la sostituzione non è andata a buon fine (filesystem in sola lettura? prova: adb shell mount -o remount,rw /). Il file sulla camera non è quello nuovo: controlla con il rollback del README."
fi
[ "$(md5_local "$ORIG")" = "$(md5_dev $DEV.orig)" ] || fail "il backup $DEV.orig non corrisponde all'originale"
adb shell "rm -f /tmp/bootanimation.wti" >/dev/null

echo
echo "Fatto: $DEV ora ha il logo WTI360, l'originale è in $DEV.orig (copia anche in $ORIG)."
echo "Riavvia la camera per vederlo. Rollback: adb shell 'cp -a $DEV.orig $DEV'"
