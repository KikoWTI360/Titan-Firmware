#!/usr/bin/env bash
# deploy_logo.sh — applica il logo WTI360 alla camera collegata via adb.
#
# Usa il bootanimation PRESO DALLA CAMERA, non quello del pacchetto: così vale per
# qualunque versione firmware e non si mette mai sul device un binario di un'altra
# build. Ogni passaggio è verificato con l'MD5 e la sostituzione finale è un
# rename atomico. Non riavvia la camera: lo fai tu dopo aver letto l'esito.
#
# Uso:  mods/02-boot-logo/deploy_logo.sh [logo.png]
#       (default: logo_wti360_94x32.png in questa cartella; codifica: ENC=page-lsb)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOGO="${1:-$HERE/logo_wti360_94x32.png}"
ENC="${ENC:-page-lsb}"
DEV=/usr/local/bin/bootanimation
WORK="$HERE/work"
mkdir -p "$WORK"

md5_local() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
md5_dev() { adb shell "md5sum $1 2>/dev/null" | cut -d' ' -f1 | tr -d '\r'; }
fail() { echo "ERRORE: $*" >&2; exit 1; }

SERIAL=$(adb get-serialno | tr -d '\r')
[ -n "$SERIAL" ] && [ "$SERIAL" != "unknown" ] || fail "nessuna camera collegata via adb"
ORIG="$WORK/bootanimation.$SERIAL.orig"
NEW="$WORK/bootanimation.$SERIAL.wti"

# Se il mod è già stato applicato, l'originale vero è il .orig sulla camera.
SRC=$DEV
if [ -n "$(adb shell "[ -f $DEV.orig ] && echo si" | tr -d '\r')" ]; then
    SRC=$DEV.orig
    echo "La camera ha già $DEV.orig: parto da quello (l'originale)."
fi

echo "== 1/4 Copio $SRC dalla camera $SERIAL"
adb pull "$SRC" "$ORIG" >/dev/null
[ "$(md5_local "$ORIG")" = "$(md5_dev $SRC)" ] || fail "copia dalla camera corrotta"

echo "== 2/4 Creo la versione col logo"
python3 "$HERE/logo_tool.py" patch "$ORIG" "$LOGO" --encoding "$ENC" --out "$NEW" \
    | sed -n '/^Nuovo logo/,/^$/p;/^Byte riscritti/p'

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

echo
echo "Fatto: $DEV ora ha il logo WTI360, l'originale è in $DEV.orig (copia anche in $ORIG)."
echo "Riavvia la camera per vederlo. Rollback: adb shell 'cp -a $DEV.orig $DEV'"
